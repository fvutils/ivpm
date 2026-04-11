# Design: Lazy Python Virtual-Environment Creation

## Problem

`project_ops.py::ProjectOps.update()` unconditionally creates
`packages/python` up front — before any packages are fetched —
unless `skip_venv=True` is passed.  This means every `ivpm update`
run creates (or re-uses) a venv even for projects that contain no
Python packages at all.

Additionally, the logic that auto-inserts `ivpm` as a PyPI dependency
(when the project's dep-set doesn't already name it) currently lives in
`project_ops.py` as a generic concern.  Per the discussion, that is a
**python-handler** responsibility: only relevant when a Python venv is
being created.

## Goals

1. The venv (`packages/python`) is created **only when the python
   handler actually runs** — i.e. only when at least one Python package
   is present in the resolved dependency set.
2. A top-level `python:` section in `ivpm.yaml` allows per-project
   configuration of the python handler (tool choice, system-site-packages,
   etc.) without requiring command-line flags.
3. Auto-insertion of `ivpm` as a PyPI package moves entirely into the
   python handler.
4. The changes are backward-compatible: projects that don't have a
   `python:` section and that relied on the implicit venv creation
   continue to work because the handler's `root_when` already fires
   correctly whenever Python packages are detected.

---

## Proposed Design

### 1. New `with:` map in `ivpm.yaml`, containing `python:`

The `package:` section gains an optional `with:` map.  Each key under
`with:` is either the name of a known handler/source type or a built-in
option.  An unrecognised key is reported as an error.

```yaml
package:
  name: myproject
  with:
    python:                        # optional; all keys within are optional
      venv: uv                     # false | true | uv | pip
      system-site-packages: false  # pass --system-site-packages to venv
      pre-release: false           # equivalent to --py-prerls-packages
  dep-sets:
    ...
```

Only the keys the author cares about need be present; all have sensible
defaults matching current behaviour.

#### `venv` key semantics

| Value | Meaning |
|---|---|
| `false` | Do not create a venv or install Python packages (skip-install) |
| `true` *(default)* | Create a venv using auto-detected tool (uv if available, else pip) |
| `uv` | Create a venv, force uv |
| `pip` | Create a venv, force pip |

The `venv` key replaces the separate `uv:`, `pip:`, and `skip-install:`
keys from the earlier draft.

#### Full schema for `with.python`

| Key | Type | Default | CLI equivalent |
|---|---|---|---|
| `venv` | `false\|true\|uv\|pip` | `true` | `--py-uv` / `--py-pip` / `--skip-py-install` |
| `system-site-packages` | bool | `false` | `--py-system-site-packages` |
| `pre-release` | bool | `false` | `--py-prerls-packages` |

Priority: **CLI flag > `with.python` yaml > built-in default**
(CLI already controls `update_info.args`; yaml values fill in only when
the corresponding arg attribute is absent or at its default.)

---

### 2. `ProjInfo` — new field `python_config`

```python
# proj_info.py
from enum import Enum

class VenvMode(str, Enum):
    SKIP  = "false"   # do not create venv / skip install
    AUTO  = "true"    # create venv, auto-detect tool
    UV    = "uv"      # create venv, force uv
    PIP   = "pip"     # create venv, force pip

@dc.dataclass
class PythonConfig:
    venv: VenvMode = VenvMode.AUTO
    system_site_packages: bool = False
    pre_release: bool = False

class ProjInfo:
    ...
    python_config: PythonConfig = dc.field(default_factory=PythonConfig)
```

`IvpmYamlReader.read()` parses the optional `with.python` map from the
`package:` section and populates `ProjInfo.python_config`.  An unknown
key under `with:` is reported as an error.

---

### 3. `ProjectUpdateInfo` — new field `python_config`

```python
@dc.dataclass
class ProjectUpdateInfo(ProjectOpsInfo):
    ...
    python_config: Optional['PythonConfig'] = None   # from root ivpm.yaml
```

`project_ops.py` reads `proj_info.python_config` and stores it on
`handler_update_info` so the python handler can access it.

---

### 4. `project_ops.py` — remove venv creation block and ivpm auto-inject

**Remove** the entire `if not skip_venv:` block that calls `setup_venv()`
(lines ~60–105 in `update()`).  The python handler will own this.

**Remove** the `if "ivpm" not in ds.packages.keys():` block that
auto-injects ivpm.  The python handler will own this.

The `UpdateEventType.VENV_START / VENV_COMPLETE / VENV_ERROR` event
dispatch currently emitted from `project_ops.py` moves into the python
handler (it already uses `task_context()` for structured tasks; the venv
creation becomes one more named task).

---

### 5. `PackageHandlerPython.on_root_post_load()` — take over venv creation

New flow inside `on_root_post_load()`:

```
1.  Resolve effective settings (CLI args > python_config > defaults).
2.  If venv_mode == SKIP → return early (no venv, no install).
3.  Inject ivpm into pypi_pkg_s if "ivpm" not already there.
4.  If venv doesn't exist → create it via setup_venv(), emitting
    HANDLER_TASK_START / END / ERROR events.
5.  Continue with existing requirements.txt / pip install flow.
```

Helper method `_resolve_python_settings(update_info) -> _PythonSettings`
reads `update_info.python_config` (from yaml) and overlays
`update_info.args` (CLI), returning a plain dataclass with all resolved
values.

```python
@dc.dataclass
class _PythonSettings:
    venv_mode: VenvMode   # SKIP | AUTO | UV | PIP
    system_site_packages: bool
    pre_release: bool
```

Resolution rules:

- `--skip-py-install` (args) → `venv_mode = SKIP`
- `--py-uv` (args) → `venv_mode = UV` (unless SKIP already set)
- `--py-pip` (args) → `venv_mode = PIP` (unless SKIP already set)
- Otherwise: use `python_config.venv` from yaml
- Default: `VenvMode.AUTO`

---

### 6. `PackageHandlerPython.on_leaf_post_load()` — no change

The existing leaf logic (detecting PyPI / `type: python` / setup.py
presence) is correct and unchanged.

---

### 7. `root_when` — no change needed

`root_when = [HasType("python")]` already fires iff at least one Python
package was detected by leaf scan.  No change required; the handler
simply won't run (and no venv will be created) for non-Python projects.

---

## Call-flow comparison

### Current

```
ProjectOps.update()
  ├─ setup_venv()              ← always, up-front
  ├─ inject ivpm package       ← always, in project_ops
  ├─ fetch all packages
  └─ pkg_handler.on_root_post_load()
       └─ PackageHandlerPython.on_root_post_load()
            └─ pip install (venv already exists)
```

### Proposed

```
ProjectOps.update()
  ├─ (no venv creation here)
  ├─ (no ivpm injection here)
  ├─ fetch all packages
  └─ pkg_handler.on_root_post_load()
       └─ PackageHandlerPython.on_root_post_load()  ← fires only if python pkgs present
            ├─ _resolve_python_settings()
            ├─ inject ivpm if auto_ivpm
            ├─ setup_venv() if venv absent
            └─ pip install
```

---

## Backward Compatibility

| Scenario | Today | After |
|---|---|---|
| Project with Python deps, no `with:` section | venv always created | venv created by handler (same result) |
| Project with no Python deps | venv created unnecessarily | venv NOT created ✓ |
| `--skip-py-install` flag | skips venv + install | resolves to `venv_mode=SKIP` in handler |
| `--py-uv` / `--py-pip` CLI | honored in handler | still honored (CLI > yaml > default) |
| `with: { python: { venv: uv } }` in yaml | (not supported yet) | now supported |
| Existing `packages/python` present | venv reused | venv reused (handler checks `isdir`) |

`skip_venv` on `ProjectUpdateInfo` is kept for callers that pass it
directly; the handler maps it to `venv_mode=SKIP` in `_resolve_python_settings()`.

---

## Files to Change

| File | Change |
|---|---|
| `src/ivpm/proj_info.py` | Add `VenvMode` enum + `PythonConfig` dataclass + `python_config` field on `ProjInfo` |
| `src/ivpm/ivpm_yaml_reader.py` | Parse `package.with.python` → `PythonConfig`; error on unknown `with:` keys |
| `src/ivpm/project_ops_info.py` | Add `python_config: Optional[PythonConfig]` to `ProjectUpdateInfo` |
| `src/ivpm/project_ops.py` | Remove venv block; remove ivpm auto-inject; pass `python_config` to `handler_update_info` |
| `src/ivpm/handlers/package_handler_python.py` | `_resolve_python_settings()`; venv creation; ivpm auto-inject |
| `test/unit/test_python_handler_config.py` | New tests (see below) |
| Existing `test/unit/test_venv.py` | Update: `skip_venv=True` path may need tweak |

---

## Tests

### `test_python_handler_config.py` (new)

- `TestPythonConfigYaml` — `IvpmYamlReader` correctly populates all
  `PythonConfig` fields from `with.python:` section; missing keys get
  defaults; unknown `with:` keys raise an error.
- `TestVenvModeResolution` — `_resolve_python_settings()` priority chain:
  CLI `--skip-py-install` → SKIP; CLI `--py-uv` → UV; yaml `venv: pip` →
  PIP; no yaml, no CLI → AUTO.
- `TestLazyVenvCreation` — integration test: project with no python deps
  does **not** create `packages/python`; project with one PyPI dep does.
- `TestIvpmAutoInjection` — ivpm is always added to the pypi set when
  absent (now unconditional, no yaml toggle).

### Existing tests to verify / update

- `test_venv.py` — tests that check for venv isolation still pass
  (handler will create the venv once Python pkgs are present).
- `test_smoke.py::test_pypi_install` — still passes: pypi dep triggers
  handler, handler creates venv and installs.
- `test_smoke.py` tests using `skip_venv=True` — unchanged; handler
  respects `update_info.skip_venv`.

---

## Open Questions / Future Work

- Should `venv` be site-configurable via `SiteConfig`?  Natural
  extension: `SiteConfig.get_default_venv_mode() -> VenvMode`.  Defer for
  now; the `with.python.venv` yaml key handles the per-project case and
  CLI flags cover one-off overrides.
- The `VENV_START / VENV_COMPLETE / VENV_ERROR` event types are already
  marked deprecated in `update_event.py`.  The venv creation step should
  emit `HANDLER_TASK_START/END/ERROR` using `task_context()` instead,
  letting the TUI display it uniformly alongside other handler tasks.
  The deprecated event constants and TUI handlers for them can be removed
  as part of this change.
