# Design: Package Content-Type Extension Mechanism

## Problem Statement

IVPM packages have two distinct axes:

1. **Source type** (`src:`): *how* the package is fetched — `git`, `http`, `pypi`, `dir`, `gh-rls`, etc. Managed by `PkgTypeRgy`, extensible via factory registration.
2. **Content type** (`type:`): *what* IVPM does with the package after fetching — currently `python` or `raw`. Currently just an enum (`PackageType` in `package.py`), with no parameter mechanism and no extension point.

The motivation is: a package can be a **git-sourced Python package** (`src: git, type: python`) and the user wants to pass type-specific parameters to the Python handler (e.g., install as non-editable, with PEP 508 extras). There is currently no validated, typed way to express or pass such per-package parameters.

Additionally:
- A single package may have **multiple content types** (e.g. a package with both Python and TypeScript content).
- A package should be able to **self-declare its type** in its own `ivpm.yaml` so that downstream projects do not need to repeat the information.

---

## Terminology Clarification

The name `PackageType` in the codebase currently refers to an enum (`Raw`, `Python`, `Unknown`) and `PkgTypeRgy` manages **source** types. Both names are confusing. This design introduces the term **content type** for the processing concept, and uses `PkgContentType` for the new objects.

| Concept | YAML key | Current code | New code |
|---|---|---|---|
| Source type | `src:` | `PkgTypeRgy`, `Package.src_type` | unchanged |
| Content type | `type:` | `PackageType` enum, `Package.pkg_type` | `PkgContentTypeRgy`, `TypeData` list on `Package.type_data` |

---

## YAML Representation

The `type:` key accepts four forms.  The old `with:` sub-key has been **removed** — options are now inline inside the type dict.

### String (no options)
```yaml
- name: mylib
  url: https://github.com/example/mylib.git
  src: git
  type: python
```

### Inline dict (single type with options)
```yaml
- name: mylib
  url: https://github.com/example/mylib.git
  src: git
  type:
    python:
      extras: [tests, docs]
      editable: false
```

### List of strings (multiple types, no options)
```yaml
- name: mylib
  url: https://github.com/example/mylib.git
  src: git
  type:
    - python
    - typescript
```

### List with per-type options
```yaml
- name: mylib
  url: https://github.com/example/mylib.git
  src: git
  type:
    - python:
        extras: [tests]
    - typescript
```

Rules:
- All four forms are normalised by `parse_type_field()` into `List[Tuple[str, dict]]`.
- Unknown keys inside the options dict are a **validation error** (not silently ignored).
- The old `with:` sub-key is **no longer supported** — a fatal error is emitted if it is present.

### Package self-description

A package can declare its own type(s) in its own `ivpm.yaml` under the `package:` root:

```yaml
# package's own ivpm.yaml
package:
  name: my-lib
  type: python          # or any of the forms above
```

This populates `ProjInfo.self_types` when the package's `ivpm.yaml` is read during dep resolution. See **Type Merging Rules** below.

---

## Classes

### `TypeData` (base dataclass)

```python
@dc.dataclass
class TypeData:
    # Set to the type name by PkgContentType.create_data().
    type_name: str = dc.field(default="", init=False)
```

Each content type defines its own subclass carrying validated, typed fields.

### `PkgContentType` (abstract base)

```python
class PkgContentType:
    @property
    def name(self) -> str: ...

    def create_data(self, opts: dict, si) -> TypeData:
        """Validate opts dict and return a populated TypeData.

        Must set data.type_name = self.name before returning.
        Raises fatal() on unknown or invalid keys.
        """

    def get_json_schema(self) -> dict:
        """Return a JSON Schema dict for the options dict."""
        return {"type": "object", "additionalProperties": False, "properties": {}}
```

### `PkgContentTypeRgy` (registry singleton)

```python
class PkgContentTypeRgy:
    def register(self, content_type: PkgContentType): ...
    def has(self, name: str) -> bool: ...
    def get(self, name: str) -> PkgContentType: ...
    def names(self) -> list: ...
    @classmethod
    def inst(cls) -> 'PkgContentTypeRgy': ...
```

Built-in types registered at startup: **`python`**, **`raw`**.

### Built-in: `PythonTypeData` / `PythonContentType`

```python
@dc.dataclass
class PythonTypeData(TypeData):
    extras: list = None    # PEP 508 extras, e.g. ["tests", "docs"]
    editable: bool = None  # None → use handler default (True for source pkgs)
```

Options accepted:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `extras` | str or list | `None` | PEP 508 extras to install |
| `editable` | bool | `None` (→ True) | Install with `-e` flag |

### Built-in: `RawTypeData` / `RawContentType`

```python
@dc.dataclass
class RawTypeData(TypeData):
    pass  # no parameters
```

`type: raw` suppresses Python auto-detection for a git/dir package — it is fetched but not pip-installed.

---

## `Package` Dataclass Fields

| Field | Type | Description |
|-------|------|-------------|
| `type_data` | `List[TypeData]` | Validated TypeData objects from the dep-list `type:` field. Empty = no explicit type (auto-detection may apply). |
| `self_types` | `List[Tuple[str, dict]]` | Raw (type_name, opts) pairs read from the package's own `ivpm.yaml`. Populated by `package_updater` after the dep is fetched. |
| `pkg_type` | `PackageType` enum | Legacy field; set from the first type name for backward compat. |

Helper:
```python
def get_type_data(pkg, cls):
    """Return the first TypeData in pkg.type_data that is an instance of cls, or None."""
```

---

## `ProjInfo` Self-Types

`ProjInfo.self_types: list` holds the raw `parse_type_field()` result from `package: { type: … }` in the project's `ivpm.yaml`. It is `[]` for packages that do not declare their own type.

---

## Type Merging Rules

After a dependency is fetched, `package_updater` reads the dep's `ivpm.yaml` (if present) and merges `proj_info.self_types` into `pkg.type_data`:

| Caller specifies | Package self-declares | Result |
|------------------|-----------------------|--------|
| Yes | Yes | Caller types kept; self-declared types whose name is **not** in caller list are appended |
| Yes | No | Caller types used |
| No | Yes | Self-declared types applied |
| No | No | Legacy auto-detection (unchanged) |

Self-declared type names not in the registry are silently skipped.

---

## Changes to `IvpmYamlReader`

In `read_deps()`, after `pkg = PkgTypeRgy.inst().mkPackage(...)`:

```python
# Fatal if legacy 'with:' is present
if "with" in d:
    fatal("'with:' is no longer supported; use inline options: type: { python: { … } }")

if "type" in d:
    raw = parse_type_field(d["type"])
    ct_rgy = PkgContentTypeRgy.inst()
    for type_name, opts in raw:
        if not ct_rgy.has(type_name):
            fatal("Unknown type '%s'" % type_name)
        pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, si))
```

In `read()`, under the `package:` root:

```python
if "type" in pkg:
    ret.self_types = parse_type_field(pkg["type"])
```

---

## Changes to `PackageHandlerPython`

`process_pkg()` uses `get_type_data()`:

```python
elif get_type_data(pkg, PythonTypeData) is not None:
    self.src_pkg_s.add(pkg.name)
    add = True
```

`_write_requirements_txt()`:

```python
td = get_type_data(pkg, PythonTypeData)
editable = True
if td is not None and td.editable is not None:
    editable = td.editable
extras = td.extras if td is not None else None
```

---

## Interaction with Existing `extras` on `PackagePyPi`

`PackagePyPi` already has `extras` and `version` fields set directly from the flat YAML opts. Both paths remain supported:

```yaml
# Legacy flat form (still works)
- name: langchain
  src: pypi
  extras: [litellm]

# Explicit type form
- name: langchain
  src: pypi
  type:
    python:
      extras: [litellm]
```

`type_data.extras` takes priority over `pkg.extras` when both are present.

---

## Extension Point

Third-party packages can register additional content types:

```python
from ivpm.pkg_content_type_rgy import PkgContentTypeRgy
PkgContentTypeRgy.inst().register(MyCustomContentType())
```

---

## Open Issues

### 1. `PackageType` Enum Removal

The `PackageType` enum in `package.py` predates this design and is redundant with the new registry. It should be removed in a future major version.

### 2. `PkgTypeRgy` Naming

`PkgTypeRgy` manages **source** types but its name sounds like it manages package (content) types. Consider renaming to `PkgSrcRgy` in a future major version.

### 3. Auto-detection vs Explicit `type:`

`PackageHandlerPython.process_pkg()` auto-detects Python packages by looking for `setup.py`/`pyproject.toml` after fetching. Auto-detection is preserved as the fallback for packages without an explicit `type:`. Self-declared type (via `package: { type: python }` in the dep's `ivpm.yaml`) is the recommended replacement.

### 4. Entry Points for Third-Party Content Types

`PkgContentTypeRgy._load()` currently hard-codes built-in types. Future: scan `entry_points(group="ivpm.content_types")`.

### 5. Schema Generation

The `ivpm.json` schema is maintained by hand. With pluggable content types, a `ivpm schema generate` command (calling `get_json_schema()` on each registered type) would keep it in sync.

---

## Summary of File Changes

| File | Change |
|---|---|
| `src/ivpm/pkg_content_type.py` | `TypeData` gets `type_name` field; `create_data()` sets it; `parse_type_field()` added |
| `src/ivpm/pkg_content_type_rgy.py` | No changes |
| `src/ivpm/package.py` | `type_data` → `List[TypeData]`; add `self_types`; add `get_type_data()` helper |
| `src/ivpm/proj_info.py` | Add `self_types: list` field |
| `src/ivpm/ivpm_yaml_reader.py` | `parse_type_field()` used for all `type:` forms; `with:` emits fatal; package root `type:` → `ret.self_types` |
| `src/ivpm/package_updater.py` | After `pkg.update()`, merge `proj_info.self_types` into `pkg.type_data` |
| `src/ivpm/handlers/package_handler_python.py` | Use `get_type_data()` throughout |
