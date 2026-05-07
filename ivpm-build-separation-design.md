# Design: Separating IVPM Build Support into `ivpm-build`

## Background and Problem Statement

IVPM is a language-agnostic package manager. However, the `ivpm` package currently carries
`ivpm.setup` — a collection of setuptools wrappers and CMake build helpers that are only
relevant when building Python extension packages. This creates several problems:

- Installing `ivpm` (e.g., to _run_ a project) pulls in build-only concerns
- The build helpers are tightly coupled to the legacy `setup.py`/`setuptools` model
- There is no support for `pyproject.toml`-native build backends (PEP 517/518/660)
- CMake support is embedded alongside Python packaging logic, even though they serve
  different projects
- It is increasingly hard to recommend `ivpm.setup` to new projects since the community
  has moved decisively to `pyproject.toml`-declarative builds

The goal of this document is to design a path that:

1. Extracts the build-support functionality into a dedicated package (`ivpm-build`)
2. Defines how that package provides a **pyproject.toml-native build backend**
3. Clarifies the role of CMake support (keep together or split)
4. Preserves full backward compatibility for ~12 existing downstream projects


---

## Current Functionality Audit (`ivpm.setup`)

| Module | Role |
|---|---|
| `wrapper.py` → `setup()` | Wraps `setuptools.setup()`, intercepts IVPM-specific kwargs, wires in custom command classes |
| `build_ext.py` → `BuildExt` | Extends `build_ext`; triggers CMake build if `CMakeLists.txt` is present; renames built extension files using platform-aware mapping (`ivpm_ext_name_m`) |
| `install_lib.py` → `InstallLib` | Extends `install_lib`; copies extra non-Python data (shared libs, include trees, etc.) into the installed package tree (`ivpm_extra_data`) |
| `ivpm_data.py` | Global state module: `_ivpm_extra_data`, `_ivpm_extdep_data`, `_ivpm_hooks`, `_ivpm_ext_name_m` + lifecycle phases (`setup.pre/post`, `build.pre/post`) |

**IVPM-specific kwargs consumed by `setup()`:**

| Kwarg | Purpose |
|---|---|
| `ivpm_extra_data` | Dict mapping package name → list of `(src, dst)` file/dir pairs copied at install time |
| `ivpm_extdep_data` | List of `(src, dst)` pairs copied after CMake install |
| `ivpm_hooks` | Dict mapping phase name → list of callables |
| `ivpm_ext_name_m` | Dict mapping ext module name → platform-expanded output filename |
| `ivpm_extdep_pkgs` | List of IVPM or importable package names whose include/lib paths should be injected into extensions |

**Key unique value:** reading the `ivpm.pkg_info` registry to collect include dirs, lib dirs,
and library names from IVPM-managed dependencies, then injecting them into Cython/C++
extension module descriptors automatically.

---

## Modern Python Build Backend Landscape (2024)

| Backend | PEP 517/660 | CMake | Declarative | Notes |
|---|---|---|---|---|
| `setuptools.build_meta` | ✅ | via hooks | partial | still dominant; `setup.py` optional |
| `flit_core` | ✅ | ❌ | ✅ | pure-Python only |
| `hatchling` | ✅ | via `hatch_build.py` | ✅ | extensible plugin system |
| `scikit-build-core` | ✅ | ✅ (first-class) | ✅ | best-in-class for CMake extensions |
| custom backend | ✅ | as needed | as needed | full control, high effort |

**Key standards:**

- **PEP 517** – build backend interface (`build_wheel`, `build_sdist`, `build_editable`, etc.)
- **PEP 518** – `[build-system]` table in `pyproject.toml`
- **PEP 660** – editable installs via PEP 517 (`build_editable`)
- **PEP 621** – `[project]` metadata in `pyproject.toml`

---

## Design Decision: CMake Support — Keep Together or Split?

**Arguments for keeping CMake support in `ivpm-build`:**

- CMake support exists specifically to build Python extension modules (`.so`/`.pyd`)
  alongside Cython/C++ code; it is not a standalone CMake orchestration tool
- The downstream projects (pssparser, pyhdl-if, etc.) use CMake _only_ in the context of
  building a Python wheel; it is always invoked through the Python build machinery
- A single package reduces dependency management overhead for downstream projects
- scikit-build-core can serve as a pure-CMake backend alternative for new projects;
  `ivpm-build` does not need to reinvent that wheel — it can optionally delegate to it

**Arguments for splitting CMake into `ivpm-cmake`:**

- Separation of concerns: some projects may need only Python packaging helpers, not CMake
- Allows `ivpm-cmake` to evolve independently (e.g., wrap scikit-build-core)

**Recommendation: Keep CMake support in `ivpm-build`**, gated by an optional dependency
(`scikit-build-core` as a soft dep, falling back to direct subprocess calls). The CMake
functionality is inherently entangled with the extension-build lifecycle; splitting it would
fragment a small package for minimal benefit. This can be revisited if usage patterns diverge.

---

## Proposed Architecture: `ivpm-build`

### Package Identity

| Field | Value |
|---|---|
| Package name (PyPI) | `ivpm-build` |
| Import name | `ivpm_build` |
| Depends on | `ivpm`, `setuptools` |
| Optional deps | `scikit-build-core` (for new-style cmake projects), `hatchling` |

### Directory Layout

```
ivpm-build/
├── pyproject.toml
├── src/
│   └── ivpm_build/
│       ├── __init__.py
│       ├── _version.py
│       │
│       ├── # --- PEP 517 build backend (new, primary interface) ---
│       ├── backend.py          # build_wheel, build_sdist, build_editable, ...
│       ├── config.py           # reads [tool.ivpm-build] from pyproject.toml
│       │
│       ├── # --- setuptools integration (backward compat + current default) ---
│       ├── setup/
│       │   ├── __init__.py     # re-exports setup() for drop-in compat
│       │   ├── wrapper.py      # ivpm-aware setup() wrapper (from ivpm.setup.wrapper)
│       │   ├── build_ext.py    # IVPM BuildExt command (from ivpm.setup.build_ext)
│       │   ├── install_lib.py  # IVPM InstallLib command (from ivpm.setup.install_lib)
│       │   └── ivpm_data.py    # global state (from ivpm.setup.ivpm_data)
│       │
│       └── # --- CMake helpers ---
│           cmake/
│           ├── __init__.py
│           ├── cmake_builder.py  # extracted CMake logic from BuildExt.build_cmake()
│           └── skbuild_bridge.py # optional: thin wrapper around scikit-build-core
└── test/
    └── ...
```

### Three Integration Paths for Downstream Projects

#### Path 1: Legacy `setup.py` (zero-change migration)

Downstream projects change only the import:

```python
# Before
from ivpm.setup import setup

# After (backward-compatible shim remains in ivpm for one major version)
from ivpm_build.setup import setup
```

Everything else (kwargs, hooks, CMake, etc.) works identically.

#### Path 2: `pyproject.toml` + `setup.py` (hybrid, recommended migration step)

```toml
[build-system]
requires = ["ivpm-build", "setuptools>=64", "cython"]
build-backend = "setuptools.build_meta"

[tool.ivpm-build]
# extra data to bundle with the package
extra-data = [
    {pkg = "mypackage", src = "build/{libdir}/{libpref}mylib{dllext}", dst = "lib"}
]
# ext name remapping
ext-name-map = [
    {module = "mypackage.entry", name = "{dllpref}mypackage_entry{dllext}"}
]
# if CMake is present, run it
cmake = true
```

`setup.py` becomes minimal — mostly metadata and extension descriptors:

```python
from setuptools import Extension, setup, find_namespace_packages
from ivpm_build.setup import apply_ivpm_setup   # new helper; no longer replaces setup()

ext = Extension("mypackage.core", sources=["src/core.pyx"], language="c++")
apply_ivpm_setup(ext_modules=[ext])              # patches ext_modules in-place from ivpm deps
setup(name="mypackage", ext_modules=[ext], ...)
```

#### Path 3: Pure `pyproject.toml` backend (new projects, long-term goal)

```toml
[build-system]
requires = ["ivpm-build", "cython"]
build-backend = "ivpm_build.backend"

[project]
name = "mypackage"
version = "0.1.0"

[tool.ivpm-build]
cmake = true
ivpm-dep-pkgs = ["debug_mgr", "ciostream"]  # replaces ivpm_extdep_pkgs kwarg

[[tool.ivpm-build.extra-data]]
pkg = "mypackage"
src = "build/{libdir}/{libpref}mypackage{dllext}"
dst = "lib"

[[tool.ivpm-build.ext-name-map]]
module = "mypackage.entry"
name   = "{dllpref}mypackage_entry{dllext}"
```

The `ivpm_build.backend` module implements the PEP 517 interface by:

1. Reading `[tool.ivpm-build]` config from `pyproject.toml`
2. Querying `ivpm.pkg_info` registry to collect include/lib paths
3. Optionally running CMake before delegating to `setuptools.build_meta`
4. Using its own `BuildExt` / `InstallLib` subclasses to handle extra data and renames

Under the hood `ivpm_build.backend` wraps `setuptools.build_meta`, not reimplementing
the full PEP 517 surface. This gives us all of setuptools' maturity while adding IVPM value:

```python
# ivpm_build/backend.py (sketch)
from setuptools.build_meta import (
    get_requires_for_build_wheel,
    prepare_metadata_for_build_wheel,
    build_sdist,
)

def get_requires_for_build_wheel(config_settings=None):
    base = _setuptools.get_requires_for_build_wheel(config_settings)
    return base + _ivpm_build_requires()

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _apply_ivpm_config()   # inject into setuptools global state (same as wrapper.py today)
    return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    _apply_ivpm_config()
    return _setuptools.build_editable(wheel_directory, config_settings, metadata_directory)
```

---

## Migration Plan

### Phase 1 — Create `ivpm-build` package (no breaking changes)

- Create the `ivpm-build` package (new sibling repo or monorepo subdir under `fvutils/`)
- Copy `ivpm/setup/` → `ivpm_build/setup/` with minimal renames
- `ivpm_build.setup.setup()` is a drop-in for `ivpm.setup.setup()`
- Add `ivpm_build.backend` as a thin wrapper over `setuptools.build_meta`
- Add `[tool.ivpm-build]` config parsing (`config.py`)
- Publish `ivpm-build` to PyPI

### Phase 2 — Deprecate `ivpm.setup` shim

- In `ivpm`, replace `ivpm/setup/` with a shim that imports from `ivpm_build.setup`
  and emits a `DeprecationWarning`
- `ivpm`'s `pyproject.toml` gains `ivpm-build` as an optional/extra dep
- Update `ivpm.pyproject.toml`: `[project.optional-dependencies] build = ["ivpm-build"]`

### Phase 3 — Migrate downstream projects

Downstream projects update in one of two ways (their choice):

- **Minimal change**: `from ivpm_build.setup import setup`
- **Full migration**: switch to `build-backend = "ivpm_build.backend"` in `pyproject.toml`
  and remove or simplify `setup.py`

### Phase 4 — Remove `ivpm.setup` shim (major version bump)

- Drop the shim from `ivpm`; `ivpm` no longer depends on setuptools at runtime
- `ivpm` becomes a pure runtime package manager again

---

## What Stays in `ivpm`

- `ivpm.pkg_info` / `PkgInfoRgy` — queried by `ivpm_build` at build time
- `ivpm.pkg_info.pkg_info_loader` — loads `.pth`/`pkginfo` entry points (runtime)
- Everything in `ivpm.handlers`, `ivpm.cmds`, etc. — unaffected

`ivpm_build` depends on `ivpm` but `ivpm` does **not** depend on `ivpm_build`.

---

## Relationship to `scikit-build-core`

`scikit-build-core` is the modern standard for CMake-first Python extensions. For projects
that have pure CMake builds (no Cython, no mixed Python/C++), consider using
`scikit-build-core` directly with a thin `hatch_build.py` hook to perform the IVPM
package-path injection:

```toml
[build-system]
requires = ["scikit-build-core", "ivpm-build"]
build-backend = "scikit_build_core.build"
```

```python
# hatch_build.py (or a dedicated hook file)
from scikit_build_core.build import BuildHookInterface
from ivpm_build.pkginfo import collect_cmake_args

class IVPMHook(BuildHookInterface):
    def initialize(self, version, build_data):
        build_data["cmake_args"].extend(collect_cmake_args(self.config.data))
```

`ivpm_build.skbuild_bridge` will provide such a hook class, making IVPM-aware
scikit-build-core projects easy to configure.

---

## Open Questions / Future Work

1. **Monorepo vs. separate repo**: Should `ivpm-build` live in the `ivpm` repo
   (e.g., `packages/ivpm-build/`) or a separate `fvutils/ivpm-build` repo? A monorepo
   subpackage is simpler for coordinated development; separate repo gives cleaner version
   independence.

2. **Cython version pinning**: `ivpm-build` should not hard-depend on Cython; downstream
   projects declare it in `build-system.requires`. `ivpm-build.backend` may need to detect
   whether Cython is present to decide how to handle `.pyx` sources.

3. **Windows support**: The existing `ivpm.setup` code has Windows-specific paths.
   These should be tested and preserved in `ivpm_build`.

4. **scikit-build-core bridge maturity**: The `skbuild_bridge.py` is aspirational;
   it requires understanding scikit-build-core's hook API more deeply before shipping.

5. **Tooling for `ivpm_data` globals**: The use of module-level globals in `ivpm_data.py`
   is fragile (not thread-safe, not re-entrant). A future clean-up could replace this
   with a context object passed through the call chain.
