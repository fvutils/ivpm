# FuseSoC Integration — Implementation Plan

## Scope

This plan covers Phase 1 of the design in `fusesoc-integration.md` (the
`PackageHandlerFuseSoC` handler — a self-contained, high-value feature) plus
the `PackageFuseSoC` source type (Phase 2 — VLNV resolution).

---

## Phase 1 — `PackageHandlerFuseSoC`

### 1.1 New File

**`src/ivpm/handlers/package_handler_fusesoc.py`**

Follows the structure of `package_handler_agents.py` (stateful handler with
leaf discovery, root aggregation, state persistence, and stale-entry cleanup).

### 1.2 Handler Metadata

```python
name               = "fusesoc"
description        = "Discovers .core dirs from deps; generates fusesoc-cores.envrc and fusesoc-cores.txt"
leaf_when          = None
root_when          = None
phase              = 10    # after direnv (phase=0)
```

### 1.3 Per-Run State

```python
_core_dirs: Dict[str, List[str]]  # pkg_name -> [abs_core_dir, ...]
_prev_state: dict                  # from ivpm.json["handlers"]["fusesoc"]
_output_written: bool              # whether we wrote outputs this run
```

### 1.4 Reset / Pre-Load

`on_root_pre_load` calls `reset()` (clear `_core_dirs`) and loads `_prev_state`
from `update_info.handler_state.get("fusesoc", {})`.

`reset()` also clears `_output_written`.

### 1.5 Leaf-Phase Discovery (`on_leaf_post_load`)

For each non-PyPI package with a path on disk:

1. **Explicit declaration** (priority): read the dep's own `ivpm.yaml` and check
   for `with.fusesoc.cores`. If present, resolve glob patterns relative to the
   package root.

2. **Auto-detection** (fallback): scan the package root (non-recursively) for
   `*.core` files. If any are found, register the package root.

   **Validation**: each `*.core` file found by auto-detection is checked for
   the `CAPI=2` marker in its first few lines. False positives (non-FuseSoC
   `*.core` files) are logged at DEBUG level and do not trigger registration.

3. Accumulate results in `self._core_dirs` protected by `self._lock`.

### 1.6 Root-Phase Output Generation (`on_root_post_load`)

Order of operations:

1. **Filter by import list** (`import: all` or a list of package names).

2. **Include project root** if it has `*.core` files (non-recursive scan).

3. **Remove stale entries** from the previous run (see §1.7).

4. **Write `packages/fusesoc-cores.txt`** — absolute paths, one per line,
   preceded by a comment showing the originating package each came from.

5. **Write `packages/fusesoc-cores.envrc`** —
   `export FUSESOC_CORES="<path1>:<path2>:..."` with `os.pathsep` for
   cross-platform correctness (Unix `:`, Windows `;`). Dirs are deduplicated
   and sorted for deterministic ordering.

6. **Contribute to `packages/packages.envrc`** — the handler manages its *own
   section* inside `packages.envrc` using begin/end sentinel comments, so it
   does not rely on fragile line-append-after-direnv semantics:

   ```bash
   # --- ivpm:fusesoc begin ---
   source_env ./fusesoc-cores.envrc
   # --- ivpm:fusesoc end ---
   ```

   If `packages.envrc` does not exist (direnv handler not active), skip. If it
   exists, replace any existing section between the sentinels, or append a new
   section.

7. **Optional `fusesoc.conf` patch** (when `update-conf: true`):
   - Read existing `fusesoc.conf` via `configparser`.
   - Remove stale `[library.ivpm.*]` sections whose package is no longer in
     `_core_dirs`.
   - Write/update sections for current core dirs.
   - Preserve all non-`library.ivpm.*` sections.

8. **Log feedback**: `note("Generated fusesoc-cores.envrc with %d core directories from %d packages", total_dirs, pkg_count)`

### 1.7 Stale-Entry Cleanup

Follow the agents-handler pattern:

- `get_state_entries()` persists `{"core_dirs": {pkg_name: [dirs...], ...}}`.
- On the next run, `_prev_state` is loaded in `on_root_pre_load`.
- In `on_root_post_load`, before writing new outputs, compare `_prev_state`
  against the current `_core_dirs` and remove entries that no longer apply.

Specifically for `fusesoc.conf` (when `update-conf: true`): remove any
`[library.ivpm.*]` section whose name does not match a package in the current
`_core_dirs`.

### 1.8 FuseSoC Core File Validation

Add `_has_cores_nonrecursive(path) -> bool` that:

1. Lists `*.core` files in the immediate directory (non-recursive).
2. For each file found, reads the first 4 KB and checks for `CAPI=2` (the
   FuseSoC core-file format marker).
3. Returns `True` only if at least one file passes validation.

This prevents false positives from core dumps or other `*.core` extensions.

### 1.9 Edge Cases

| Case | Behaviour |
|---|---|
| No deps, no project cores | No outputs created; handler silently no-ops |
| `packages.envrc` missing (direnv off) | fusesoc-cores.envrc still written; packages.envrc contribution skipped |
| `fusesoc.conf` doesn't exist (but `update-conf: true`) | Created from scratch with IVPM sections only |
| Same core dir discovered from multiple packages | Deduplicated by absolute path |
| Empty `import: []` | Only project root cores included |
| Package declares cores dir that doesn't exist | Warning logged, entry skipped |
| `packages.envrc` has old fusesoc section | Replaced in-place via sentinel comments |
| `fusesoc.conf` has stale `[library.ivpm.*]` sections | Removed during update |

### 1.10 Entry-Point Registration

In `pyproject.toml`, add:

```toml
fusesoc = "ivpm.handlers.package_handler_fusesoc:PackageHandlerFuseSoC"
```

### 1.11 Exports

In `src/ivpm/handlers/__init__.py`, no changes needed — the registry discovers
handlers dynamically via entry points, not imports.

---

## Phase 2 — `PackageFuseSoC` Source Type

### 2.1 New File

**`src/ivpm/pkg_types/package_fusesoc.py`**

### 2.2 Class Structure

```python
@dc.dataclass
class PackageFuseSoC(Package):
    vlnv: str = None       # e.g. "::wb_intercon:1.0"
    # Inherits name, path, src_type="fusesoc", etc. from Package
```

### 2.3 VLNV Resolution Algorithm

The resolution proceeds in three steps:

**Step 1 — Ensure the fusesoc-cores index is available in deps-dir.**
The `PackageFuseSoC.update()` method auto-declares the registry as a
`PackageGit` dependency with `cache: true` and fetches it into
`<deps-dir>/fusesoc-cores/`:

```python
def _ensure_index(self, deps_dir: str, updater) -> str:
    index_path = os.path.join(deps_dir, "fusesoc-cores")
    if os.path.isdir(index_path):
        return index_path                      # already fetched this run
    # Auto-declare the registry as a cached git dep
    index_pkg = PackageGit(
        name="fusesoc-cores",
        url="https://github.com/fusesoc/fusesoc-cores.git",
        cache=True)
    updater.resolve_pkg(index_pkg)             # fetches into deps-dir
    return index_path
```

The user never declares `fusesoc-cores` in their `ivpm.yaml` — it is
auto-declared by the source type when any dep has `src: fusesoc`. Existing
IVPM caching semantics apply: `ivpm update --refresh` re-fetches; otherwise
the cached clone is reused.

**Step 2 — Walk index `.core` files.**
- Recursively find all `*.core` files in the index.
- Parse each as YAML, extract the `name:` field.
- Compare against the requested VLNV (strict match by default; `version: latest`
  selects the highest version).

**Step 3 — Extract provider info and delegate.**
- From the matching `.core` file, read the `provider:` block.
- Build a `PackageGit(name=..., url=..., tag=version)` and delegate to its
  `update()` method.

### 2.4 Registration

In `src/ivpm/pkg_types/pkg_type_rgy.py`, add:

```python
rgy.register("fusesoc", PackageFuseSoC.create, "fusesoc")
```

### 2.5 Edge Cases

| Case | Behaviour |
|---|---|
| VLNV not found in index | Fatal error with list of closest-matching core names |
| Provider type not git (e.g. `provider: local`) | Fatal error; only git providers are supported |
| Index clone fails | Fatal error with underlying git error |
| Multiple cores match same name at different versions | Strict match preferred; `version: latest` picks highest |
| `fusesoc-cores/` already exists in deps-dir | Reused; `--refresh` triggers re-fetch |

---

## Test Plan

### 3.1 Test Fixtures

Create test data in `test/unit/data/`:

```
fusesoc_leaf1/
  ivpm.yaml                     # name: fusesoc_leaf1, no deps
  wb_intercon.core              # valid CAPI=2 core file
  extra.core                    # dummy .core file (to test multi-core detection)

fusesoc_leaf2/
  ivpm.yaml                     # name: fusesoc_leaf2, no deps
  cores/
    uart16550.core              # core in subdir (requires explicit declaration)

fusesoc_leaf3/
  ivpm.yaml                     # name: fusesoc_leaf3
  with:
    fusesoc:
      cores:
        - cores/
  cores/
    uart.core

fusesoc_no_cores/
  ivpm.yaml                     # name: fusesoc_no_cores, no deps, no .core files

fusesoc_pypi/
  ivpm.yaml                     # name: fusesoc_pypi, src_type should skip

fusesoc_not_core/
  ivpm.yaml                     # name: fusesoc_not_core
  data.core                     # NOT a CAPI-2 file (e.g. "core dump" content)
```

### 3.2 Unit Tests

**File:** `test/unit/test_fusesoc_handler.py`

#### 3.2.1 Basic Output Generation

| Test | What it verifies |
|---|---|
| `test_cores_envrc_generated` | Single dep with `.core` → `fusesoc-cores.envrc` created with correct `FUSESOC_CORES` content |
| `test_cores_txt_generated` | `fusesoc-cores.txt` created with absolute paths and provenance comments |
| `test_no_cores_no_output` | Dep without `.core` files → no output files created |
| `test_project_root_cores_included` | Root project has `.core` file → included in outputs |

#### 3.2.2 Explicit Declaration

| Test | What it verifies |
|---|---|
| `test_declared_cores_used` | Dep declares `with.fusesoc.cores` → those dirs used, not auto-detect |
| `test_declared_nonroot_dirs` | Dep declares subdirectory core path → that path appears in outputs |
| `test_declared_glob_pattern` | Glob pattern in `cores:` resolves correctly |

#### 3.2.3 Import Filtering

| Test | What it verifies |
|---|---|
| `test_import_all_default` | `import: all` (or absent) → all dep cores included |
| `test_import_filtered_list` | `import: [pkg_a]` → only pkg_a's cores included |
| `test_import_empty_list` | `import: []` → only project root cores included |

#### 3.2.4 `packages.envrc` Integration

| Test | What it verifies |
|---|---|
| `test_appends_to_packages_envrc` | Direnv handler active → `packages.envrc` contains sentinel-wrapped fusesoc section |
| `test_no_direnv_no_append` | Direnv handler not active → `packages.envrc` not created/touched |
| `test_sentinel_replaces_old_section` | Second `ivpm update` → old sentinel section replaced, not duplicated |

#### 3.2.5 `fusesoc.conf` Update (opt-in)

| Test | What it verifies |
|---|---|
| `test_conf_update_creates_sections` | `update-conf: true` → `fusesoc.conf` has `[library.ivpm.*]` sections |
| `test_conf_update_preserves_user_sections` | User-maintained `[library.my_core]` section preserved |
| `test_conf_update_removes_stale` | Dep removed → stale `[library.ivpm.*]` section removed |
| `test_conf_update_false_default` | `update-conf: false` (default) → `fusesoc.conf` not touched |

#### 3.2.6 Stale Entry Cleanup

| Test | What it verifies |
|---|---|
| `test_stale_entries_removed` | Second run with fewer deps → stale core dirs removed from outputs |
| `test_idempotent_second_run` | Second run with same deps → outputs unchanged |

#### 3.2.7 Edge Cases

| Test | What it verifies |
|---|---|
| `test_pypi_packages_skipped` | PyPI dep with `.core` files → not scanned |
| `test_fake_core_file_ignored` | `data.core` without CAPI-2 content → not registered |
| `test_missing_declared_dir_warns` | Dep declares nonexistent core dir → warning logged |
| `test_dedup_core_dirs` | Two deps pointing to same dir → single entry in outputs |
| `test_cross_platform_pathsep` | `FUSESOC_CORES` uses `os.pathsep` not hardcoded `:` |
| `test_no_deps_no_project_cores` | No deps + no project cores → no files created |

#### 3.2.8 Symlink / Copy Behaviour

| Test | What it verifies |
|---|---|
| `test_existing_correct_symlink_left_alone` | If symlink exists and points to correct target → silently leave it |
| `test_symlink_to_wrong_deps_target_replaced` | Symlink points to different target within deps_dir → replace it |
| `test_symlink_outside_deps_dir_warned_and_left` | Symlink points outside deps_dir → warn and leave as-is |
| `test_non_symlink_file_at_dest_warned_and_skipped` | Regular file exists at dest → warn and skip |

### 3.3 Phase 2 Tests (`test_pkg_fusesoc.py`)

| Test | What it verifies |
|---|---|
| `test_vlnv_resolves_to_git` | VLNV string → correct git URL + tag extracted |
| `test_vlnv_not_found` | Unknown VLNV → fatal error with close-match hint |
| `test_vlnv_latest_version` | `version: latest` → highest version selected |
| `test_non_git_provider_fails` | Provider not git → fatal error |
| `test_index_auto_declared` | `src: fusesoc` dep → `fusesoc-cores/` fetched into deps-dir automatically |
| `test_index_cached_reused` | Second update without changes → cached index reused, no re-clone |

### 3.4 Test Implementation Notes

- All tests use `TestBase` (`from .test_base import TestBase`) and
  `self.mkFile()` + `self.ivpm_update(skip_venv=True)`.
- Test fixture paths use `${DATA_DIR}/<fixture_name>` with `src: dir`.
- Output verification reads files from `self.testdir` subdirectories.
- `assertLogs` context manager used for warning/error verification.
- Core file fixtures contain minimal valid CAPI-2 YAML:
  ```yaml
  CAPI=2:
  name: ::wb_intercon:1.0
  filesets:
    rtl:
      files: [wb_intercon.v]
  ```

---

## Documentation Plan

### 4.1 Inline Code Documentation

- Module-level docstring in `package_handler_fusesoc.py` describing the
  publish/import pattern and output artifacts.
- Docstring on each public method (following the agents-handler convention).
- `handler_info()` classmethod returning `HandlerInfo` for the `ivpm show
  handler` command.

### 4.2 Update `fusesoc-integration.md`

Replace the implementation sketches in the existing design doc with references
to the actual implementation. Specifically:

- Replace the sketch in §"Handler Implementation Sketch" with a summary of the
  real class and its key methods.
- Replace the sketch in §"Implementation Sketch" (Part 2) with a reference to
  `package_fusesoc.py`.
- Update §"Design Decisions and Rationale" to include the new decisions made
  during implementation (sentinel-based `packages.envrc` contribution,
  CAPI-2 validation, `os.pathsep` for Windows).
- Resolve or strike through the Open Questions with their implementation
  answers:
  - Q1 (version matching): strict match, error if not found, `version: latest`
    shorthand.
  - Q2 (transitive deps): no — let FuseSoC handle at build time.
  - Q3 (index freshness): fetched into deps-dir as a `cache: true` dep; standard
    IVPM caching semantics apply (`--refresh` to re-fetch).
  - Q4 (packages.envrc append safety): sentinel-based section management
    replaces fragile append.

### 4.3 Update `docs/source/handlers.rst`

Add a `fusesoc` entry to the handler reference documentation, describing:

- Purpose and use case.
- Configuration keys (`import`, `update-conf`).
- Published artifacts.
- Interaction with the direnv handler.

### 4.4 Update `docs/source/package_types.rst`

Add a `fusesoc` source type entry describing VLNV dependency declaration.

---

## Implementation Order

```
Week 1: Phase 1 core
  Day 1  — package_handler_fusesoc.py skeleton + leaf discovery
  Day 2  — Root-phase output generation (envrc, txt, packages.envrc)
  Day 3  — Test fixtures + basic output tests
  Day 4  — Stale-entry cleanup + fusesoc.conf update
  Day 5  — Remaining Phase 1 tests + edge cases + symlink handling

Week 2: Phase 2 + Documentation
  Day 1  — PackageFuseSoC + VLNV resolution
  Day 2  — Phase 2 tests
  Day 3  — Documentation updates (design doc, handlers.rst, package_types.rst)
  Day 4  — Code review + integration test
  Day 5  — Buffer / bugfix
```

---

## Files Changed Summary

| File | Action |
|---|---|
| `src/ivpm/handlers/package_handler_fusesoc.py` | **Create** |
| `src/ivpm/pkg_types/package_fusesoc.py` | **Create** |
| `src/ivpm/pkg_types/pkg_type_rgy.py` | **Edit** — register `"fusesoc"` source type |
| `pyproject.toml` | **Edit** — add `fusesoc` entry point |
| `test/unit/test_fusesoc_handler.py` | **Create** |
| `test/unit/test_pkg_fusesoc.py` | **Create** |
| `test/unit/data/fusesoc_leaf1/ivpm.yaml` | **Create** |
| `test/unit/data/fusesoc_leaf1/wb_intercon.core` | **Create** |
| `test/unit/data/fusesoc_leaf1/extra.core` | **Create** |
| `test/unit/data/fusesoc_leaf2/ivpm.yaml` | **Create** |
| `test/unit/data/fusesoc_leaf2/cores/uart16550.core` | **Create** |
| `test/unit/data/fusesoc_leaf3/` (explicit declaration) | **Create** |
| `test/unit/data/fusesoc_no_cores/` | **Create** |
| `test/unit/data/fusesoc_pypi/` | **Create** |
| `test/unit/data/fusesoc_not_core/` | **Create** |
| `docs/fusesoc-integration.md` | **Edit** — replace sketches with implementation references |
| `docs/source/handlers.rst` | **Edit** — add fusesoc handler entry |
| `docs/source/package_types.rst` | **Edit** — add fusesoc source type entry |
