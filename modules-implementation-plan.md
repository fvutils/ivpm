# Environment Modules — Implementation Plan

## Implementation Status

> **Status: Phases 1-4 implemented** (2026-05-06)
>
> All four phases have been implemented with passing tests.
> Documentation updates (Phase 5/6) remain pending.

| Phase | Status | Tests |
|-------|--------|-------|
| 1. `ModulesInterface` | **Done** | 19/19 pass (`test_modules_interface.py`) |
| 2. `PackageModule` source type | **Done** | 14/14 pass (`test_package_module.py`) |
| 3. `ModuleContentType` | **Done** | 7/7 pass (added to `test_pkg_content_type.py`) |
| 4. `PackageHandlerModules` | **Done** | 6/6 pass (`test_modules_handler.py`) |
| Test fixtures | **Done** | `module_leaf1/`, `module_leaf2/`, `module_no_ivpm/` |
| Documentation | Pending | |

### Implementation Notes

- **Handler module specifier fallback**: When `type: module` is used on a
  non-module source (e.g. `src: dir`), the handler uses `pkg.name` as the
  module specifier fallback, since `ModuleTypeData.module` is only populated
  by `PackageModule.process_options()`.
- **Site-packages sync**: The development environment has a stale copy of
  ivpm in site-packages alongside the editable `.pth` install.  New files
  were symlinked and modified files were copied to site-packages to enable
  handler entry point discovery.  A clean `pip install -e .` would eliminate
  this need.
- **Entry point registration**: The `modules` handler entry point was added
  to both `pyproject.toml` and the installed `entry_points.txt` for
  immediate test availability.

---

## Scope

This plan covers the full implementation of Environment Modules support as
described in `design-modules-support.md`.  The work is split into four
phases:

1. **`ModulesInterface`** — variant-aware subprocess wrapper for querying
   the module system.
2. **`PackageModule` source type** (`src: module`) — resolves a module
   specifier to a root directory and sets `pkg.path`.
3. **`ModuleContentType`** (`type: module`) — validates `with:` parameters
   and signals the handler.
4. **`PackageHandlerModules`** handler — collects module-typed packages
   and generates `modules.envrc` / patches `packages.envrc`.

Each phase is self-contained and testable on its own.

---

## Phase 1 — `ModulesInterface`

### 1.1 New File

**`src/ivpm/modules_interface.py`**

Extracted from the variant-aware wrapper in `modules_from_python.md`
(approach 3/4).  This is the single place where IVPM interacts with
`modulecmd.tcl` / `lmod` subprocesses.

### 1.2 Data Model

```python
import enum
import dataclasses as dc
from typing import Optional, Tuple

class ModulesVariant(enum.Enum):
    MODULES_3X_TCL = "modules-3x"
    MODULES_4X     = "modules-4x"
    LMOD           = "lmod"
    UNKNOWN        = "unknown"

class ModulesError(Exception):
    """Raised when a modulecmd subprocess fails or returns unexpected output."""
    pass

@dc.dataclass
class ModulesInterface:
    variant: ModulesVariant = ModulesVariant.UNKNOWN
    cmd_path: Optional[str] = None       # path to modulecmd.tcl or lmod
    tclsh_path: Optional[str] = None     # path to tclsh (Modules 3.x/4.x)
```

### 1.3 Detection (`detect_variant`)

A module-level function `detect_variant()` that probes the environment for
the modules installation:

1. Check `LMOD_CMD` env var → `ModulesVariant.LMOD`.
2. Check `MODULESHOME` env var → look for `modulecmd.tcl` inside it.
   - If `modulecmd.tcl` supports `--version` with 4.x+ output →
     `MODULES_4X`.
   - Otherwise → `MODULES_3X_TCL`.
3. Fall back to `shutil.which("modulecmd")` or
   `shutil.which("modulecmd.tcl")`.
4. Return `(ModulesVariant, cmd_path, tclsh_path)`.

### 1.4 Query Methods

All methods call `subprocess.run()` and parse stdout/stderr.  IVPM never
calls `exec()` or evaluates modulecmd-generated Python code.

| Method | Purpose | Used by |
|--------|---------|---------|
| `is_avail(module: str) -> bool` | Check module availability | `PackageModule.update()`, `PackageModule.status()` |
| `module_path(module: str) -> Optional[str]` | Return the absolute path to the modulefile | `PackageModule.update()` |
| `module_show(module: str) -> str` | Return raw `module show` output (stderr) | `PackageModule.update()` (opt-in `resolve_root`) |
| `avail(pattern: str) -> List[str]` | List available modules matching a pattern | Future `ivpm search --modules` |

Each method raises `ModulesError` (a new exception class defined in the
same file) on subprocess failure, with the stderr output included in the
message.

### 1.5 Variant-Specific Handling

Per the variant table in `modules_from_python.md`:

| Variant | `module_path()` implementation |
|---------|-------------------------------|
| Modules 4.x | `modulecmd.tcl python path <module>` → parse stdout |
| Modules 3.x | `tclsh modulecmd.tcl python path <module>` → parse stdout, handle temp-file quirk |
| Lmod | `$LMOD_CMD python show <module>` → parse stderr for filename line |

### 1.6 Lazy Instantiation

The `ModulesInterface` is stored on `ProjectUpdateInfo` as
`modules_interface: Optional[ModulesInterface]` (see Phase 4, §4.6).
It is created lazily on first access: `PackageModule.update()` calls
`_get_modules_interface(update_info)` which runs `detect_variant()` once
and caches the result.

### 1.7 Explicit Override

When `handler_configs["modules"]` contains `variant` and/or `modulecmd`
keys, those override auto-detection.  This lets sites with non-standard
installations configure the path explicitly.

---

## Phase 2 — `PackageModule` Source Type (`src: module`)

### 2.1 New File

**`src/ivpm/pkg_types/package_module.py`**

Extends `Package` directly (like `PackageFuseSoC` and `PackagePyPi`).

### 2.2 Class Structure

```python
@dc.dataclass
class PackageModule(Package):
    module: str = None              # e.g. "gcc/15.2.0"
    module_root: str = None         # resolved root directory
    modulefile_path: str = None     # absolute path to the modulefile
    root_override: str = None       # explicit root: from YAML
    resolve_root: bool = False      # opt-in module-show parsing
```

### 2.3 `process_options()`

```python
def process_options(self, opts, si):
    super().process_options(opts, si)
    self.src_type = "module"
    if "module" not in opts:
        fatal("src: module requires a 'module:' specifier")
    self.module = opts["module"]
    if "root" in opts:
        self.root_override = opts["root"]
    if "resolve-root" in opts:
        self.resolve_root = bool(opts["resolve-root"])
```

Also implicitly adds `ModuleTypeData` to `pkg.type_data` when no explicit
`type:` is specified — mirroring how `src: pypi` implicitly gets
`PythonTypeData`:

```python
    # Implicit type assignment (unless user specified type: explicitly)
    if not any(td.type_name == "module" for td in self.type_data):
        from ..pkg_content_type import ModuleTypeData
        td = ModuleTypeData()
        td.type_name = "module"
        td.module = self.module
        self.type_data.append(td)
```

### 2.4 `create()` and `source_info()`

```python
@staticmethod
def create(name, opts, si) -> 'PackageModule':
    pkg = PackageModule(name)
    pkg.process_options(opts, si)
    return pkg

@classmethod
def source_info(cls):
    from ..show.info_types import PkgSourceInfo
    return PkgSourceInfo(
        name="module",
        description="Environment Modules (module load) — resolves a module "
                    "specifier to the modulefile directory on disk",
        origin="built-in",
    )
```

### 2.5 `update()` — Resolution Strategy

```python
def update(self, update_info: ProjectUpdateInfo) -> 'ProjInfo':
    update_info.report_package(cacheable=False)
    mi = _get_modules_interface(update_info)
```

**Step 1 — Locate the modulefile:**

```python
    mf_path = mi.module_path(self.module)
    if mf_path is None:
        fatal("Module '%s' is not available (module_path returned None). "
              "Check MODULEPATH and module availability." % self.module)
    self.modulefile_path = mf_path
```

**Step 2 — Determine the root directory (priority order):**

```python
    if self.root_override:
        root = os.path.expandvars(self.root_override)
    elif self.resolve_root:
        root = self._resolve_root_from_show(mi)
    else:
        # Default: modulefile directory
        root = os.path.dirname(mf_path) if os.path.isfile(mf_path) else mf_path
```

The `_resolve_root_from_show()` helper parses `module show` output for
`setenv *_HOME`, `prepend-path PATH`, or `set root` directives.  It is
only called when `resolve_root: true` is explicitly set.

**Step 3 — Set `pkg.path`:**

Note: `PackageUpdater._resolve_pkg()` sets `pkg.path` to
`os.path.join(deps_dir, pkg.name)` before calling `update()`.
`PackageModule.update()` must override this default:

```python
    self.path = root
    self.module_root = root
```

**Step 4 — Load sub-project info:**

```python
    return ProjInfo.mkFromProj(root)
```

If the root directory contains an `ivpm.yaml`, its dep-sets, self-types,
handler_configs, and skills declarations are loaded and participate in
dependency resolution — exactly like a git or dir package.

### 2.6 `sync()` and `status()`

```python
def sync(self, sync_info) -> 'PkgSyncResult':
    from ..pkg_sync import PkgSyncResult, SyncOutcome
    return PkgSyncResult(
        name=self.name,
        outcome=SyncOutcome.SKIPPED,
        reason="environment module (externally managed)",
    )
```

`status()` reports the module name, resolved root, and whether the module
is still available via `mi.is_avail()`.

### 2.7 Registration

In `src/ivpm/pkg_types/pkg_type_rgy.py`, add to `_load()`:

```python
from .package_module import PackageModule
self.register("module", PackageModule.create, PackageModule.source_info())
```

### 2.8 Schema Update

In `src/ivpm/schema/ivpm.json`, add `"module"` to the `src` field's
`oneOf` array:

```json
{
    "const": "module",
    "title": "Environment Module — resolves a module specifier to its modulefile directory"
}
```

Also add `"module"`, `"root"`, and `"resolve-root"` to the `package-dep`
properties:

```json
"module": {
    "type": "string",
    "title": "Module specifier (e.g. gcc/15.2.0). Required when src is 'module'"
},
"root": {
    "type": "string",
    "title": "Explicit root directory override for src: module"
},
"resolve-root": {
    "type": "boolean",
    "title": "Opt-in: parse module show output to determine the install prefix",
    "default": false
}
```

### 2.9 Lock File Support

In `src/ivpm/package_lock.py`, add a `"module"` branch to
`_entry_from_pkg()`:

```python
elif src == "module":
    entry["module"] = getattr(pkg, "module", None)
    entry["modulefile"] = getattr(pkg, "modulefile_path", None)
    entry["root"] = getattr(pkg, "module_root", None)
```

And a corresponding branch in `_spec_matches_lock()`:

```python
elif src == "module":
    return getattr(pkg, "module", None) == lock_entry.get("module")
```

And in `IvpmLockReader.build_packages_info()`, add reconstruction:

```python
elif src == "module":
    p = PackageModule(name)
    p.module = entry.get("module")
    p.modulefile_path = entry.get("modulefile")
    p.module_root = entry.get("root")
    p.path = entry.get("root")
    p.src_type = "module"
    pkg = p
```

---

## Phase 3 — `ModuleContentType` (`type: module`)

### 3.1 Additions to Existing File

**`src/ivpm/pkg_content_type.py`** — add alongside `PythonContentType`
and `RawContentType`:

```python
@dc.dataclass
class ModuleTypeData(TypeData):
    load: bool = True        # emit 'module load' into envrc
    module: str = None       # module specifier (copied from PackageModule.module)

class ModuleContentType(PkgContentType):
    @property
    def name(self) -> str:
        return "module"

    def create_data(self, with_opts: dict, si) -> ModuleTypeData:
        known = {"load"}
        for k in with_opts:
            if k not in known:
                fatal("type 'module' does not accept parameter '%s' "
                      "(known: %s)" % (k, ", ".join(sorted(known))))
        data = ModuleTypeData()
        if "load" in with_opts:
            data.load = bool(with_opts["load"])
        data.type_name = self.name
        return data
```

### 3.2 Registration

In `src/ivpm/pkg_content_type_rgy.py`, add to `_load()`:

```python
from .pkg_content_type import ModuleContentType
self.register(ModuleContentType())
```

### 3.3 Schema Update

Add `"module"` to the `type` field's `oneOf` array in
`src/ivpm/schema/ivpm.json`:

```json
{
    "const": "module",
    "title": "Environment Module — emit module load and/or resolve root directory for handler discovery"
}
```

---

## Phase 4 — `PackageHandlerModules`

### 4.1 New File

**`src/ivpm/handlers/package_handler_modules.py`**

Follows the structure of `PackageHandlerFuseSoC` (stateful handler with
leaf discovery, root aggregation, sentinel-based `packages.envrc`
patching, and output file generation).

### 4.2 Handler Metadata

```python
@dc.dataclass
class PackageHandlerModules(PackageHandler):
    name               = "modules"
    description        = "Generates module load statements into modules.envrc"
    leaf_when          = None        # inspect every package
    root_when          = None        # always run on_root_post_load
    phase              = 1           # after direnv (0), before python (5)
    conditions_summary = "Active when any package carries ModuleTypeData"
```

### 4.3 Per-Run State

```python
    module_pkgs: Dict[str, str] = dc.field(default_factory=dict)
    # Maps pkg_name -> module specifier (e.g. "gcc/15.2.0")
```

### 4.4 Leaf Phase (`on_leaf_post_load`)

For each package, check if it carries `ModuleTypeData` with `load: true`:

```python
def on_leaf_post_load(self, pkg: Package, update_info):
    from ..package import get_type_data
    from ..pkg_content_type import ModuleTypeData
    td = get_type_data(pkg, ModuleTypeData)
    if td is None:
        return
    if not td.load:
        return
    module_spec = td.module or getattr(pkg, 'module', None)
    if module_spec is None:
        return
    with self._lock:
        self.module_pkgs[pkg.name] = module_spec
```

### 4.5 Root Phase (`on_root_post_load`)

1. **Generate `packages/modules.envrc`:**

   ```python
   def on_root_post_load(self, update_info):
       deps_dir = update_info.deps_dir
       if not self.module_pkgs:
           self._cleanup(deps_dir)
           return

       envrc_path = os.path.join(deps_dir, "modules.envrc")
       with open(envrc_path, "w") as fp:
           fp.write("# Generated by IVPM modules handler -- do not edit manually\n")
           for pkg_name in sorted(self.module_pkgs.keys()):
               fp.write("module load %s\n" % self.module_pkgs[pkg_name])
   ```

   Ordering: topological by dependency, then alphabetical (same strategy
   as the direnv handler).  The sorted fallback above is simplified; the
   real implementation will use the topological ordering available from
   `update_info`.

2. **Patch `packages/packages.envrc`** using the sentinel pattern from
   the Python and FuseSoC handlers:

   ```python
   _MODULES_SENTINEL_BEGIN = "# --- ivpm:modules begin ---"
   _MODULES_SENTINEL_END   = "# --- ivpm:modules end ---"
   ```

   Insert **before** the python handler's section (phase 1 < phase 5).
   If `packages.envrc` does not exist, skip (direnv handler not active).

3. **Cleanup**: If no module packages remain, remove the sentinel section
   from `packages.envrc` and delete `modules.envrc`.

### 4.6 `ProjectUpdateInfo` Changes

In `src/ivpm/project_ops_info.py`, add to `ProjectUpdateInfo`:

```python
modules_interface: Optional['ModulesInterface'] = None
```

This field is lazily populated by `PackageModule.update()` on first use
and shared across all module packages and the handler.

### 4.7 Lock File Contribution

```python
def get_lock_entries(self, deps_dir: str) -> dict:
    if not self.module_pkgs:
        return {}
    entries = {}
    for pkg_name, module_spec in sorted(self.module_pkgs.items()):
        entries[pkg_name] = {"module": module_spec}
    return {"modules": entries}
```

### 4.8 Handler Config

The handler reads `update_info.handler_configs.get("modules", {})` in
`on_root_post_load()` to obtain user-specified configuration:

```yaml
package:
  name: my-project
  with:
    modules:
      variant: auto          # auto | modules-4x | modules-3x | lmod
      modulecmd: /path/to/modulecmd.tcl
```

These values are forwarded to `ModulesInterface.detect_variant()` as
overrides.

### 4.9 Entry-Point Registration

In `pyproject.toml`, add:

```toml
[project.entry-points."ivpm.handlers"]
modules = "ivpm.handlers.package_handler_modules:PackageHandlerModules"
```

---

## Test Plan

### 5.1 Test Fixtures

Create test data in `test/unit/data/`:

```
module_leaf1/
  ivpm.yaml          # name: module_leaf1, declares sub-deps or skills
  SKILL.md           # optional: verifies handler discovery through pkg.path

module_leaf2/
  ivpm.yaml          # name: module_leaf2, no sub-deps

module_no_ivpm/
  README.md          # no ivpm.yaml — verifies ProjInfo.mkFromProj returns None
```

Additionally, tests that exercise the full `PackageModule.update()` path
require a mock `ModulesInterface` (see §5.3) because real `modulecmd.tcl`
is not available in CI.

### 5.2 Unit Tests — `ModulesInterface`

**File:** `test/unit/test_modules_interface.py`

These tests verify the parsing and detection logic.  They mock
`subprocess.run` to return canned outputs for each variant.

| Test | What it verifies |
|------|-----------------|
| `test_detect_variant_modules_4x` | `MODULESHOME` set, `modulecmd.tcl --version` returns 4.x → `MODULES_4X` |
| `test_detect_variant_lmod` | `LMOD_CMD` set → `LMOD` |
| `test_detect_variant_modules_3x` | `MODULESHOME` set, no 4.x version string → `MODULES_3X_TCL` |
| `test_detect_variant_none` | No env vars, no `which` result → `UNKNOWN`, methods raise `ModulesError` |
| `test_module_path_4x` | Parses `modulecmd.tcl python path` stdout → correct path |
| `test_module_path_lmod` | Parses `lmod python show` stderr → extracts modulefile path |
| `test_is_avail_true` | Module available → `True` |
| `test_is_avail_false` | Module not available → `False` |
| `test_module_show_output` | Returns raw stderr for further parsing |
| `test_explicit_override` | `variant` and `modulecmd` config override auto-detection |
| `test_module_path_not_found` | Module not in MODULEPATH → returns `None` |

### 5.3 Unit Tests — `PackageModule`

**File:** `test/unit/test_package_module.py`

These tests use a `FakeModulesInterface` stub that returns canned
modulefile paths without calling real subprocesses.  The stub is injected
via `update_info.modules_interface`.

| Test | What it verifies |
|------|-----------------|
| `test_create_from_options` | `PackageModule.create()` with `module: gcc/15.2.0` → correct fields set |
| `test_missing_module_field` | `create()` without `module:` → `fatal()` |
| `test_update_sets_path_to_modulefile_dir` | `update()` sets `pkg.path` to the modulefile's parent directory (default) |
| `test_update_with_root_override` | `root: /custom/path` → `pkg.path` set to override, not modulefile dir |
| `test_update_with_resolve_root` | `resolve-root: true` → `_resolve_root_from_show()` called, result used |
| `test_update_loads_proj_info` | Root dir contains `ivpm.yaml` → `ProjInfo` loaded and returned |
| `test_update_no_ivpm_yaml` | Root dir has no `ivpm.yaml` → returns `None` (no error) |
| `test_update_module_not_available` | `module_path()` returns `None` → `fatal()` with clear message |
| `test_sync_returns_skipped` | `sync()` returns `SKIPPED` with "environment module" reason |
| `test_implicit_module_type_data` | No explicit `type:` → `ModuleTypeData` auto-added to `type_data` |
| `test_explicit_type_raw_no_module_data` | `type: raw` → no `ModuleTypeData` in `type_data` |
| `test_src_type_is_module` | `pkg.src_type` is `"module"` after construction |
| `test_root_override_env_expansion` | `root: $TOOL_ROOT/gcc` → env var expanded |

### 5.4 Unit Tests — `ModuleContentType`

**File:** `test/unit/test_pkg_content_type.py` (extend existing file)

| Test | What it verifies |
|------|-----------------|
| `test_module_type_registered` | `PkgContentTypeRgy.inst().has("module")` is `True` |
| `test_module_type_create_data_defaults` | `create_data({})` → `ModuleTypeData(load=True)` |
| `test_module_type_create_data_load_false` | `create_data({"load": False})` → `ModuleTypeData(load=False)` |
| `test_module_type_unknown_param` | `create_data({"foo": 1})` → `fatal()` |

### 5.5 Unit Tests — `PackageHandlerModules`

**File:** `test/unit/test_modules_handler.py`

Uses the `TestBase` integration pattern with `self.mkFile()` and
`self.ivpm_update(skip_venv=True)`.  Module packages are simulated using
`src: dir` with `type: module` to avoid needing a real module system.

#### 5.5.1 Basic Output Generation

| Test | What it verifies |
|------|-----------------|
| `test_modules_envrc_generated` | Single module dep → `packages/modules.envrc` created with `module load` statement |
| `test_modules_envrc_multiple` | Multiple module deps → all `module load` statements present, sorted |
| `test_no_module_deps_no_output` | No module-typed deps → `modules.envrc` not created |

#### 5.5.2 `packages.envrc` Integration

| Test | What it verifies |
|------|-----------------|
| `test_appends_to_packages_envrc` | Direnv handler active → `packages.envrc` contains sentinel-wrapped modules section |
| `test_sentinel_replaces_old_section` | Second `ivpm update` → old sentinel section replaced, not duplicated |
| `test_no_packages_envrc_no_append` | `packages.envrc` does not exist → handler does not create it |
| `test_modules_before_python` | Sentinel section appears before python handler's section (phase 1 < 5) |

#### 5.5.3 `load: false` Behavior

| Test | What it verifies |
|------|-----------------|
| `test_load_false_no_module_load` | `type: { module: { load: false } }` → no `module load` in envrc |
| `test_load_false_still_sets_path` | `load: false` dep → `pkg.path` still resolved for handler discovery |

#### 5.5.4 Cleanup

| Test | What it verifies |
|------|-----------------|
| `test_stale_modules_envrc_removed` | Second run with no module deps → `modules.envrc` deleted, sentinel removed |
| `test_idempotent_second_run` | Second run with same deps → outputs unchanged |

#### 5.5.5 Lock File

| Test | What it verifies |
|------|-----------------|
| `test_lock_entries_recorded` | Module packages appear under `"modules"` key in `package-lock.json` |
| `test_lock_empty_when_no_modules` | No module deps → no `"modules"` key in lock file |

#### 5.5.6 Handler Discovery Through `pkg.path`

| Test | What it verifies |
|------|-----------------|
| `test_agents_finds_skills_in_module_root` | Module root contains `SKILL.md` → agents handler discovers it |
| `test_fusesoc_finds_cores_in_module_root` | Module root contains `.core` files → fusesoc handler discovers them |

### 5.6 Integration Test — Full Pipeline (Manual)

These tests require a real module system and are expected to run on
developer workstations or EDA-configured CI hosts, not in unit-test CI:

| Test | What it verifies |
|------|-----------------|
| `test_real_module_resolves` | `src: module, module: <available_module>` → `ivpm update` succeeds, `pkg.path` exists |
| `test_real_module_envrc_loadable` | Generated `modules.envrc` can be sourced in a shell without errors |
| `test_real_module_not_available` | Non-existent module specifier → clear error message |

Mark these tests with `@unittest.skipUnless(os.environ.get("IVPM_TEST_MODULES"), "requires module system")`
so they are skipped by default.

---

## Documentation Plan

### 6.1 Inline Code Documentation

- Module-level docstring in each new file describing its role in the
  modules pipeline.
- Docstring on each public method following existing codebase conventions.
- `handler_info()` classmethod on `PackageHandlerModules` returning
  `HandlerInfo` for `ivpm show handler modules`.
- `source_info()` classmethod on `PackageModule` returning `PkgSourceInfo`
  for `ivpm show source module`.

### 6.2 Update `docs/source/package_types.rst`

Add a `module` source type section describing:

- YAML syntax with examples (minimal, with `root:` override, with
  `resolve-root: true`).
- Resolution strategy (modulefile directory as default root).
- Relationship to `type: module` and the handler.
- Limitations (read-only roots, no `packages/` representation).

### 6.3 Update `docs/source/handlers.rst`

Add a `modules` handler section describing:

- Purpose and use case (EDA/HPC tool management).
- Output artifacts (`modules.envrc`, sentinel in `packages.envrc`).
- Configuration keys (`variant`, `modulecmd`).
- Phase ordering relative to direnv and python handlers.

### 6.4 Update `docs/source/integrations.rst`

Add a section on Environment Modules integration covering:

- Supported variants (Modules 3.x, 4.x, Lmod).
- How to configure `MODULEPATH` and `MODULESHOME` for IVPM.
- Interaction with direnv (`module load` in envrc).

### 6.5 Update `docs/packages_and_types.md`

Add `module` to the source type and content type tables.

### 6.6 Update `design-modules-support.md`

After implementation, replace design sketches with references to actual
files.  Mark open issues as resolved where applicable.

---

## Files Changed Summary

| File | Action | Phase |
|------|--------|-------|
| `src/ivpm/modules_interface.py` | **Create** | 1 |
| `src/ivpm/pkg_types/package_module.py` | **Create** | 2 |
| `src/ivpm/pkg_types/pkg_type_rgy.py` | **Edit** — register `"module"` source type | 2 |
| `src/ivpm/schema/ivpm.json` | **Edit** — add `module` to `src` enum, add `module`/`root`/`resolve-root` dep properties | 2 |
| `src/ivpm/package_lock.py` | **Edit** — add `module` branches to `_entry_from_pkg`, `_spec_matches_lock`, `IvpmLockReader` | 2 |
| `src/ivpm/pkg_content_type.py` | **Edit** — add `ModuleTypeData` and `ModuleContentType` classes | 3 |
| `src/ivpm/pkg_content_type_rgy.py` | **Edit** — register `ModuleContentType` | 3 |
| `src/ivpm/handlers/package_handler_modules.py` | **Create** | 4 |
| `src/ivpm/project_ops_info.py` | **Edit** — add `modules_interface` field | 4 |
| `pyproject.toml` | **Edit** — add `modules` handler entry point | 4 |
| `test/unit/test_modules_interface.py` | **Create** | 1 |
| `test/unit/test_package_module.py` | **Create** | 2 |
| `test/unit/test_pkg_content_type.py` | **Edit** — add module content type tests | 3 |
| `test/unit/test_modules_handler.py` | **Create** | 4 |
| `test/unit/data/module_leaf1/ivpm.yaml` | **Create** | 2 |
| `test/unit/data/module_leaf1/SKILL.md` | **Create** | 2 |
| `test/unit/data/module_leaf2/ivpm.yaml` | **Create** | 2 |
| `test/unit/data/module_no_ivpm/README.md` | **Create** | 2 |
| `docs/source/package_types.rst` | **Edit** — add `module` source type | docs |
| `docs/source/handlers.rst` | **Edit** — add `modules` handler | docs |
| `docs/source/integrations.rst` | **Edit** — add modules integration section | docs |
| `docs/packages_and_types.md` | **Edit** — add `module` to tables | docs |

## No-Change Files

The following files do **not** need modification because `PackageModule`
is indistinguishable from any other resolved package once `pkg.path` is
set:

- `src/ivpm/ivpm_yaml_reader.py` — `"module"` is handled by the registries
- `src/ivpm/package_updater.py` — calls `pkg.update()` generically
- `src/ivpm/project_ops.py` — unchanged
- `src/ivpm/project_sync.py` — unchanged
- `src/ivpm/handlers/handler_conditions.py` — `HasType("module")` and
  `HasSourceType("module")` work with existing infrastructure
- `src/ivpm/handlers/package_handler_direnv.py` — unchanged
- `src/ivpm/handlers/package_handler_python.py` — unchanged
- `src/ivpm/handlers/package_handler_agents.py` — unchanged
- `src/ivpm/handlers/package_handler_fusesoc.py` — unchanged
- All `cmds/` files — unchanged

---

## Implementation Order

```
Week 1: Core infrastructure
  Day 1  — modules_interface.py + test_modules_interface.py
  Day 2  — package_module.py skeleton + process_options + create
  Day 3  — package_module.py update() + resolution logic
  Day 4  — pkg_type_rgy.py registration + schema update
  Day 5  — test_package_module.py (all unit tests with FakeModulesInterface)

Week 2: Content type + handler
  Day 1  — ModuleContentType + ModuleTypeData in pkg_content_type.py
  Day 2  — pkg_content_type_rgy.py registration + test_pkg_content_type.py additions
  Day 3  — package_handler_modules.py skeleton + leaf phase
  Day 4  — package_handler_modules.py root phase (envrc generation, sentinel patching)
  Day 5  — test_modules_handler.py (handler tests)

Week 3: Integration + documentation
  Day 1  — package_lock.py module branches + lock file tests
  Day 2  — project_ops_info.py + pyproject.toml entry point
  Day 3  — End-to-end integration testing on module-equipped host
  Day 4  — Documentation updates (package_types.rst, handlers.rst, integrations.rst)
  Day 5  — Update design-modules-support.md + code review
```
