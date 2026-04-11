# Dep-Set Inheritance: Implementation Plan

## Problem Statement

Currently each dep-set in `ivpm.yaml` is fully independent. Users who want
`default-dev` to include everything in `default` plus extra dev packages must
manually duplicate the `default` entries. This is error-prone and hard to
maintain.

### Desired syntax

```yaml
dep-sets:
  - name: default
    deps:
      - name: jinja2
        src: pypi
      - name: pyyaml
        src: pypi

  - name: default-dev
    uses: default          # <── new field
    deps:
      - name: pytest
        src: pypi
      # jinja2 and pyyaml are inherited from 'default'
      # if a name appears in both, this dep-set wins
```

### Merge semantics

1. Start with a deep copy of the *base* dep-set's package list.
2. For every package in the *current* dep-set, insert or overwrite (current
   wins on name collision).
3. The resulting `PackagesInfo.packages` dict is the merged set.
4. Inheritance is resolved **after all dep-sets are parsed** so definition
   order does not matter.
5. Cyclic `uses` chains are detected and raise an error.

---

## Affected Files

| File | Change |
|------|--------|
| `src/ivpm/schema/ivpm.json` | Add `uses` property to `dep-set` object definition |
| `src/ivpm/packages_info.py` | Add `uses: Optional[str]` field |
| `src/ivpm/ivpm_yaml_reader.py` | Parse `uses` field; add post-parse resolution pass |
| `docs/source/dependency_sets.rst` | Document the new `uses` field with examples |
| `test/unit/test_dep_set_inheritance.py` | New test file covering the cases below |

---

## Detailed Changes

### 1. `src/ivpm/schema/ivpm.json`

Inside the `"dep-set"` object definition (currently lines 88–108), add a
`"uses"` property alongside `"name"`, `"default-dep-set"`, and `"deps"`:

```json
"uses": {
    "type": "string",
    "title": "Name of a dep-set in this file to use as a base. Non-overlapping packages are inherited; packages defined in this dep-set override those from the base."
}
```

Also relax the `"required"` constraint on `"deps"` (if/when one is added in
future) so that `deps` can be omitted when `uses` is present and no extra
packages are needed.  For now `deps` remains required (already the case —
validation just errors if absent, which existing code enforces in Python).

### 2. `src/ivpm/packages_info.py`

Add an optional `uses` attribute that records the unresolved base dep-set name.
This is needed temporarily during the parse / resolution pass:

```python
class PackagesInfo():
    def __init__(self, name):
        self.name = name
        self.uses: Optional[str] = None          # <── new
        self.packages: Dict[str, Package] = {}
        self.setup_deps: Dict[str, Set[str]] = {}
        self.options = {}
```

Update `copy()` to propagate `uses` (keeps the field consistent if a
`PackagesInfo` is cloned before resolution):

```python
def copy(self) -> 'PackagesInfo':
    ret = PackagesInfo(self.name)
    ret.uses     = self.uses
    ret.packages = self.packages.copy()
    ret.options  = self.options.copy()
    return ret
```

After inheritance is resolved, `uses` is kept on the object for
introspection/debugging but is not consulted again at runtime.

### 3. `src/ivpm/ivpm_yaml_reader.py`

#### 3a. Parse `uses` in `read_dep_sets()`

Current guard in `read_dep_sets()` rejects an entry if `"deps"` is absent.
Keep that guard but allow `deps` to be an empty list when `uses` is present.
The real change is to read and store `uses`:

```python
def read_dep_sets(self, info: 'ProjInfo', dep_sets):
    if not isinstance(dep_sets, list):
        raise Exception("...")

    for ds_ent in dep_sets:
        if not isinstance(ds_ent, dict):
            raise Exception("Dependency set is not a dict")
        if "name" not in ds_ent.keys():
            raise Exception("No name associated with dependency set")
        if "deps" not in ds_ent.keys():
            raise Exception("No 'deps' entry in dependency set")

        ds_name = ds_ent["name"]
        ds = PackagesInfo(ds_name)

        # ── new ──────────────────────────────────────────────────────
        if "uses" in ds_ent.keys():
            ds.uses = ds_ent["uses"]
        # ─────────────────────────────────────────────────────────────

        default_dep_set = ds_ent.get("default-dep-set")

        deps = ds_ent["deps"]
        if not isinstance(deps, list):
            raise Exception("deps is not a list")
        self.read_deps(ds, deps, default_dep_set)
        info.set_dep_set(ds.name, ds)

    # ── new: resolve inheritance after all sets are parsed ──────────
    self._resolve_dep_set_inheritance(info)
    # ────────────────────────────────────────────────────────────────
```

#### 3b. Add `_resolve_dep_set_inheritance()` helper

```python
def _resolve_dep_set_inheritance(self, info: 'ProjInfo'):
    """
    For every dep-set that declares a 'uses' base, merge the base's
    packages into it.  Current dep-set packages win on name collision.
    Detects cycles and raises on unknown base names.
    """
    dep_set_m = info.dep_set_m

    def resolve(name, visiting):
        ds = dep_set_m[name]
        if ds.uses is None:
            return                          # nothing to do
        if name in visiting:
            raise Exception(
                "Cyclic dep-set inheritance detected: %s"
                % " -> ".join(list(visiting) + [name])
            )
        base_name = ds.uses
        if base_name not in dep_set_m:
            raise Exception(
                "dep-set '%s' references unknown base dep-set '%s'"
                % (name, base_name)
            )
        # Resolve the base first (supports multi-level chains)
        visiting.add(name)
        resolve(base_name, visiting)
        visiting.discard(name)

        # Merge: base packages first, then current packages overwrite
        base_ds = dep_set_m[base_name]
        merged = base_ds.packages.copy()
        merged.update(ds.packages)         # current wins on collision
        ds.packages = merged

        # Also merge options
        merged_opts = base_ds.options.copy()
        merged_opts.update(ds.options)
        ds.options = merged_opts

    for ds_name in list(dep_set_m.keys()):
        resolve(ds_name, set())
```

Key design decisions in this implementation:
- `visiting` is a local set per top-level `resolve()` call, re-entered
  recursively, which is sufficient for cycle detection without a global
  "resolved" cache. A small optimisation (mark resolved sets so they are
  not traversed again) can be added later if dep-set counts grow large.
- The merge is done **in-place** on `ds.packages` so no other code needs
  to change — callers of `info.get_dep_set(name)` see the merged result
  transparently.

---

### 4. `docs/source/dependency_sets.rst`

Add a new section **"Dep-Set Inheritance with `uses`"** (suggested location:
after the existing "Defining Dependency Sets" section, before "Hierarchical
Dependency Sets").  Content to include:

- Motivation (avoiding duplication)
- Syntax example showing `uses`
- Merge/override semantics explanation
- Multi-level inheritance example (`default` → `default-dev` → `default-ci`)
- Note about cycle detection error
- Note that `uses` is resolved at parse time, so runtime performance is
  unaffected

---

### 5. `test/unit/test_dep_set_inheritance.py`

New test class `TestDepSetInheritance(TestBase)` with the following cases:

| Test name | What it checks |
|-----------|---------------|
| `test_basic_inheritance` | `default-dev` uses `default`; packages from both appear in resolved set |
| `test_override_wins` | Same package name in both dep-sets; current (child) definition is kept |
| `test_no_extra_deps` | `uses` with empty `deps` list — base packages are all present |
| `test_multi_level` | Three-level chain `a` → `b` → `c`; all packages visible at top |
| `test_order_independent` | Base dep-set defined *after* the child; still resolves correctly |
| `test_unknown_base_error` | `uses: nonexistent` raises a clear exception |
| `test_cycle_error` | `a uses b`, `b uses a` raises cycle-detection exception |
| `test_update_with_inheritance` | Full `ivpm_update()` run; packages from inherited dep-set are installed |
| `test_sync_with_inheritance` | `ivpm_sync()` sees merged dep-set correctly |

---

## Scope Boundaries (Out of Scope)

- `uses` on sub-package `dep-set:` references (the per-package `dep-set:`
  option that controls *which* dep-set to load from a sub-package).  This
  feature is about top-level dep-set definition only.
- Cross-file inheritance (using a dep-set defined in another project's
  `ivpm.yaml`).
- Wildcard/glob dep-set matching.

---

## No-Change Files

The following files do **not** need modification because inheritance is fully
resolved at parse time and the merged `PackagesInfo` is indistinguishable from
a hand-written one:

- `src/ivpm/project_ops.py` (`_getDepSet`, `update`, `sync`)
- `src/ivpm/project_sync.py`
- `src/ivpm/package_updater.py`
- `src/ivpm/package_lock.py`
- `src/ivpm/proj_info.py`
- `src/ivpm/package.py`
- All command files (`cmd_update.py`, `cmd_sync.py`, etc.)

---

## Implementation Order

1. `packages_info.py` — add `uses` field (trivial, no logic)
2. `ivpm_yaml_reader.py` — parse field + resolution helper (core logic)
3. `schema/ivpm.json` — schema update
4. `test/unit/test_dep_set_inheritance.py` — tests (drive correctness)
5. `docs/source/dependency_sets.rst` — documentation
