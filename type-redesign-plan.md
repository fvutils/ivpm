# Package Type Redesign Plan

## Problem Statement

The current `type:` / `with:` mechanism has two gaps:

1. **Single-type limitation** – each package entry can carry only one type (e.g. cannot express
   "this package is both Python and TypeScript").
2. **No self-description** – a package cannot declare its own type in its own `ivpm.yaml`.
   The depending project must always repeat that information.

## Goals

1. Allow `type:` to be a **string**, a **single-key dict** (type name → options), or a **list** of
   either form.
2. The inline-dict form replaces `with:` completely; `with:` support is removed.
3. Allow `package: { type: … }` at the root of a package's own `ivpm.yaml` so the package can
   self-declare its content type(s).
4. When a dependency has its own `ivpm.yaml` the declared type(s) are discovered automatically;
   the depending project can still override or augment them.

---

## New `type:` Syntax

### String (unchanged)
```yaml
type: python
```

### Inline dict – single type with options (replaces `with:`)
```yaml
type:
  python:
    extras: [tests, docs]
    editable: false
```

### List of types (new – multiple types)
```yaml
type:
  - python
  - typescript
```

### List with per-type options (new)
```yaml
type:
  - python:
      extras: [tests]
  - typescript
```

### Package self-description (new)
```yaml
# package's own ivpm.yaml
package:
  name: my-lib
  type: python          # or any of the forms above
```

---

## Internal Representation

### Normalised list of `(type_name, options_dict)` pairs

All four external forms are normalised at parse time into:

```python
List[Tuple[str, dict]]   # e.g. [("python", {"extras": ["tests"]})]
```

A helper `parse_type_field(value) -> List[Tuple[str, dict]]` handles the conversion:

```
str              →  [(value, {})]
dict {k: v}      →  [(k, v or {})]
list of str/dict →  above rules applied per element
```

### `Package` dataclass changes

| Field | Old | New |
|-------|-----|-----|
| `type_data` | `Optional[TypeData]` | `List[TypeData]` (empty list = no explicit type) |
| `pkg_type`  | `PackageType` enum (legacy) | retained as-is for now; populated from first matching entry |

---

## Changes by File

### `src/ivpm/package.py`
- Change `type_data: Optional[TypeData] = None` → `type_data: List[TypeData] = field(default_factory=list)`
- Add `self_types: List[Tuple[str, dict]] = field(default_factory=list)` – raw type spec read from
  the package's own `ivpm.yaml` (filled during dep resolution, not YAML reading).

### `src/ivpm/ivpm_yaml_reader.py`

**Dependency-list reading** (currently lines 219-231):

Replace the `type:` + `with:` block with a unified `_parse_type_field()` call:

```python
if "with" in d:
    fatal("Package '%s': 'with:' is no longer supported; use inline type options instead")

if "type" in d:
    raw = _parse_type_field(d["type"])
    ct_rgy = PkgContentTypeRgy.inst()
    for type_name, opts in raw:
        if not ct_rgy.has(type_name):
            fatal("Package '%s': unknown type '%s'" % (pkg.name, type_name))
        pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, si))
```

**Package root reading** (currently only reads `name`, `description`, …):

```python
if "type" in package_section:
    pkg.self_types = _parse_type_field(package_section["type"])
```

`_parse_type_field()` is a module-level helper implementing the normalisation described above.

### `src/ivpm/pkg_content_type.py`
- `PkgContentType.create_data(with_opts, si)` signature stays the same; callers now pass the
  per-type options dict directly (previously the `with:` mapping, now the inline dict).
- No API change needed; the `with:` removal is purely a parser concern.

### `src/ivpm/pkg_content_type_rgy.py`
- No changes required.

### `src/ivpm/dep_resolver.py` (or equivalent resolver)
- After cloning/downloading a dependency, if the package contains an `ivpm.yaml` with
  `self_types`, merge them with any caller-specified `type_data`:
  - Precedence: **caller-specified types take priority** over self-declared types.
  - If the caller specifies no types, use self-declared types.
  - If both, union the lists (caller types first, then any self-declared types not already present
    by type name).
- This is the "auto-discovery" path; it fires during `update`/`sync` when the package's
  `ivpm.yaml` is read recursively.

### `src/ivpm/handlers/package_handler_python.py`
- Change all `isinstance(pkg.type_data, PythonTypeData)` checks to:
  ```python
  _get_type_data(pkg, PythonTypeData)  # returns first matching entry or None
  ```
  where `_get_type_data` is a small helper added in `package.py`:
  ```python
  def get_type_data(pkg, cls):
      for td in pkg.type_data:
          if isinstance(td, cls):
              return td
      return None
  ```
- The existing logic (editable flag, extras) continues to work; it just uses the helper instead of
  direct isinstance checks.

---

## Type Merging Rules (package root vs. caller)

| Caller specifies | Package self-declares | Result |
|------------------|-----------------------|--------|
| Yes | Yes | Caller wins; self-declared types whose name is not in caller list are appended |
| Yes | No | Caller types used |
| No | Yes | Self-declared types used |
| No | No | Legacy auto-detection (unchanged) |

---

## Removal of `with:`

- Remove parsing of `with:` in `ivpm_yaml_reader.py`.
- Emit a fatal error if `with:` is encountered: `"'with:' is no longer supported; use inline type options: type: { python: { … } }"`.
- Remove `with:` examples from all docs and templates.
- `PkgContentType.create_data()` interface does not change (it already accepts a plain dict).

---

## Test Changes

### `test/unit/test_pkg_content_type.py`
- Update all YAML fixtures: replace `with:` blocks with the inline-dict form.
- Add tests for list-of-types parsing: single type, two types, mixed str/dict.
- Add test: `with:` present → fatal error.
- Add test: `type:` at `package:` root → `self_types` populated.

### `test/unit/test_with_params.py`
- Rename to `test_type_params.py` (optional, low priority).
- Replace `with:` fixtures with inline-dict form.
- Add tests for a package with two types (e.g. `python` + `raw`).

### New: `test/unit/test_package_self_type.py`
- Test that `package: { type: python }` in a dep's `ivpm.yaml` causes the dep to be registered
  as a Python package when no caller-side type is specified.
- Test precedence: caller type overrides self-declared type.
- Test merge: caller specifies `python`, package self-declares `typescript` → both present.

---

## Documentation Changes

### `docs/pkg-content-type-design.md`
- Update YAML examples throughout to remove `with:` and show the new inline-dict form.
- Add a "Multiple types" section with examples.
- Add a "Package self-description" section explaining `package: { type: … }`.
- Update the "Open Issues" section to mark resolved items.

### `README.md` / `docs/` reference docs (if any)
- Update any `type:` / `with:` usage examples.

---

## Implementation Order

1. `_parse_type_field()` helper + unit test (isolated, no side effects).
2. Update `Package` dataclass (`type_data` list, `self_types`).
3. Update `ivpm_yaml_reader.py` – dep list parsing (new `type:` forms, `with:` removal).
4. Update `ivpm_yaml_reader.py` – package root `type:` parsing.
5. Update `package_handler_python.py` + `get_type_data()` helper.
6. Implement self-type merging in dep resolver.
7. Update all tests.
8. Update docs.
