# Agents Handler Design

## Overview

The current `PackageHandlerSkills` (entry point: `skills`) is replaced by a new
`PackageHandlerAgents` (entry point: `agents`).

**Key behavioural changes:**

| Aspect | Old (skills) | New (agents) |
|---|---|---|
| Handler name / entry-point key | `skills` | `agents` |
| Output | Single aggregated `packages/SKILLS.md` | Relative symlinks from `.agents/skills/<pkg>` → skill directory (copy fallback on Windows) |
| Claude support | None | Optional: also populate `.claude/skills/<pkg>` when `claude: true` |
| Skill-path discovery | Root of each dep only | Root + optional additional paths declared by the dep in its own `ivpm.yaml` |
| Frontmatter required | Yes (name + description) | Yes (name + description) – same rule |


## Output Directory Structure

The unit of output is the **directory containing SKILL.md**, not just the file
itself.  A skill directory may also contain `scripts/`, `references/`, and
`assets/` subdirectories that must remain accessible alongside the skill file.

On platforms that support symlinks (Linux, macOS), the handler creates
**relative symlinks** from the target skills directory into each skill source
directory:

```
<project-root>/
├── .agents/
│   └── skills/
│       ├── dep-a -> ../../packages/dep-a          # relative symlink to skill dir
│       └── dep-b -> ../../packages/dep-b
├── .claude/                                        # only when claude: true
│   └── skills/
│       ├── dep-a -> ../../packages/dep-a
│       └── dep-b -> ../../packages/dep-b
└── packages/
    ├── dep-a/
    │   ├── SKILL.md
    │   ├── scripts/        # companion dirs accessible via symlink
    │   └── assets/
    └── dep-b/
        └── SKILL.md
```

For non-root skill paths (e.g. `subdir/skills/SKILL.md`), the symlink target
is the **directory containing that SKILL.md** — `packages/dep-a/subdir/skills`
— and the link name uses a suffix for disambiguation:
`.agents/skills/dep-a-1 -> ../../packages/dep-a/subdir/skills`.

### Symlink fallback (platforms without symlink support)

On platforms where `os.symlink` is unavailable or raises `NotImplementedError`
/ `OSError` (primarily Windows), the handler falls back to a **directory copy**:

- `SKILL.md` is always copied.
- The following companion subdirectories are copied if present alongside
  `SKILL.md`: `scripts/`, `references/`, `assets/`.

The detection logic is:

```python
def _symlinks_supported(dest_parent: str) -> bool:
    """Return True if the filesystem at dest_parent supports symlinks."""
    probe = os.path.join(dest_parent, ".ivpm_symlink_probe")
    try:
        os.symlink(".", probe)
        os.remove(probe)
        return True
    except (OSError, NotImplementedError):
        return False
```

This is probed once per `on_root_post_load` call and cached for that run.

### Link / copy naming

| # skill paths from this package | Link / copy dir name |
|---|---|
| 1 | `<package-name>` |
| >1 | `<package-name>-1`, `<package-name>-2`, … |

### Stale-entry cleanup

At the start of `on_root_post_load`, all symlinks (and copy-dirs on fallback
platforms) previously written by IVPM inside `.agents/skills/` and
`.claude/skills/` are removed before new ones are created.  Non-IVPM-managed
entries in those directories are left alone.

To distinguish managed entries, a manifest file `.agents/skills/.ivpm` is
written listing the names created during the last run.  On the next run the
manifest is read, only those names are removed, and then the manifest is
rewritten.

Directories (`.agents`, `.agents/skills`, `.claude`, `.claude/skills`) are
created if they do not already exist.


## Agents Skill Spec (shared schema)

The `agents:` value — wherever it appears — always has the same structure:

```yaml
agents:
  skills:
    - SKILL.md                  # literal path (from package root)
    - subdir/SKILL.md
    - skills/**/SKILL.md        # glob pattern; ** requires recursive=True
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `skills` | list of strings | `null` | Glob patterns (or literal paths) relative to the package root. Each entry is expanded with `glob.glob(pattern, root_dir=pkg.path, recursive=True)`. When absent/null, the auto-probe is used instead (priority 3). |

All entries are expanded as globs.  Literal paths are valid degenerate patterns.
A pattern that matches zero files emits a warning and is otherwise silently skipped.

The `agents:` spec is **identical** whether it appears:

* on a dep entry in the consumer's `ivpm.yaml` (priority 1 override), or
* under `package.with.agents:` in a dep's own `ivpm.yaml` (priority 2 self-declaration).

The only additional keys that are valid in a particular context are documented
in that context's section below.


## Root-Project Configuration (`package.with.agents:`)

The root project controls global handler behaviour via `package.with.agents:`
in its `ivpm.yaml`:

```yaml
package:
  name: my-project
  with:
    agents:
      claude: false   # true → also populate .claude/skills/  (default: false)
```

This config is forwarded to handlers via
`ProjectUpdateInfo.handler_configs['agents']` through the existing
`_read_with_section` / `handler_configs` mechanism.

Additional keys valid **only** at the root-project level:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `claude` | bool | `false` | When `true`, mirror every skill entry to `.claude/skills/` in addition to `.agents/skills/` |


## Skill-Path Discovery — Priority Order

There are three sources of skill-path information, checked in priority order
(highest first):

| Priority | Source | Covers |
|---|---|---|
| 1 | `agents:` key on the dep entry in the **consumer's** `ivpm.yaml` | Any dep, including non-IVPM projects with no `ivpm.yaml` |
| 2 | `package.with.agents.skills:` in the **dep's own** `ivpm.yaml` | IVPM-aware deps that self-declare skill locations |
| 3 | Auto-probe: `SKILLS.md` then `SKILL.md` at the package root | Any dep that follows the default convention |

When a higher-priority source is found it is used exclusively; lower-priority
sources are not consulted.


## Per-Dep Override in the Consumer (`agents:` dep key)

The consumer specifies skills for a dependency using the shared **Agents Skill
Spec** directly on the dep entry:

```yaml
package:
  name: my-project
  dep-sets:
    - name: default-dev
      deps:
        - name: external-lib          # non-IVPM project — no ivpm.yaml
          url: https://github.com/org/external-lib.git
          agents:
            skills:
              - docs/SKILL.md
              - tools/helper/SKILL.md

        - name: marketplace           # wildcard example
          url: https://github.com/org/marketplace.git
          agents:
            skills:
              - skills/**/SKILL.md

        - name: ivpm-aware-dep        # overrides dep's own ivpm.yaml declaration
          url: https://github.com/org/ivpm-aware-dep.git
          agents:
            skills:
              - custom/SKILL.md
```

### Reading the dep-spec `agents:` key

The `agents:` key is stored on the `Package` object as a new
`agents_config: Optional[dict]` field.

```python
# package.py — new field on Package
agents_config: Optional[dict] = None   # set from dep-entry 'agents:' key
```

```python
# ivpm_yaml_reader.py — in read_deps, after pkg is created
if "agents" in d.keys():
    pkg.agents_config = dict(d["agents"])
```


## Per-Dependency Self-Declaration (`package.with.agents:` in a dep's own `ivpm.yaml`)

An IVPM-aware dependency can declare its exported skills using the same shared
**Agents Skill Spec** under `package.with.agents:`:

```yaml
package:
  name: my-dep
  with:
    agents:
      skills:
        - SKILL.md                  # root-level (explicit)
        - subdir/SKILL.md           # non-root literal path
        - subdir/agents/**/SKILL.md # glob: all SKILL.md under subdir/agents/
```

This is only consulted when `pkg.agents_config` is absent (priority 2).

### Reading the dep's own `ivpm.yaml`

The handler reads the dependency's `ivpm.yaml` during `on_leaf_post_load` via
`ProjInfo.mkFromProj(pkg.path)`.  The `handler_configs.get('agents', {})` dict
on the resulting `ProjInfo` yields the dep-local agents config.


## Prerequisite Infrastructure

### 1. `ProjectUpdateInfo` – project root field

The handler needs to write to `<project-root>/.agents/` which is one level
*above* `deps_dir`.  Add a `project_dir` field to `ProjectUpdateInfo` so the
handler does not have to guess at the directory layout:

```python
# project_ops_info.py
@dc.dataclass
class ProjectUpdateInfo(ProjectOpsInfo):
    ...
    project_dir: Optional[str] = None   # NEW: set to ProjectOps.root_dir
```

In `project_ops.py`, populate it alongside `deps_dir`:

```python
handler_update_info = ProjectUpdateInfo(
    args, deps_dir,
    project_dir=self.root_dir,   # NEW
    project_name=proj_info.name,
    ...
)
```

### 2. Handler state persistence via `ivpm.json`

Any handler may need to persist small amounts of state between runs (e.g. a
list of entries it created, so it can clean them up on the next run).  Rather
than each handler writing its own dot-file, `ivpm.json` gains a general
`"handlers"` section with per-handler namespaced sub-objects.

```json
{
  "dep-set": "default-dev",
  "handlers": {
    "agents": { "agents_skills": ["dep-a", "dep-b"], "claude_skills": ["dep-a"] },
    "direnv": { "envrc_hash": "abc123" }
  }
}
```

**`PackageHandler` base class** — new hook (parallel to `get_lock_entries`):

```python
def get_state_entries(self) -> dict:
    """Return handler-specific state to persist in ivpm.json['handlers'][name].
    Called after on_root_post_load(). Default returns {}.
    """
    return {}
```

**`PackageHandlerList`** — aggregates contributions:

```python
def get_state_entries(self) -> dict:
    result = {}
    for h in self.handlers:
        entries = h.get_state_entries()
        if entries and h.name:
            result[h.name] = entries
    return result
```

**`ProjectUpdateInfo`** — carries previously-saved state into the run:

```python
handler_state: dict = dc.field(default_factory=dict)
# Populated from ivpm.json["handlers"] before on_root_pre_load is called.
# Handlers read update_info.handler_state.get("agents", {}) in on_root_pre_load.
```

**`project_ops.py`** — wire both ends:

```python
# Before on_root_pre_load: load previous state
ivpm_json = _read_ivpm_json(deps_dir)          # existing helper
handler_update_info.handler_state = ivpm_json.get("handlers", {})

# After on_root_post_load: collect new state and write
state_contributions = pkg_handler.get_state_entries()
ivpm_json["handlers"] = state_contributions
_write_ivpm_json(deps_dir, ivpm_json)           # replaces the current inline write
```


## `PackageHandlerAgents` – Implementation Sketch

The key internal data structure: we accumulate **skill directories** (the
directory containing each SKILL.md) rather than individual file paths.

```python
# src/ivpm/handlers/package_handler_agents.py

_COMPANION_DIRS = ("scripts", "references", "assets")

@dc.dataclass
class PackageHandlerAgents(PackageHandler):
    name               = "agents"
    description        = "Links/copies skill directories from dependencies into .agents/skills/ (and optionally .claude/skills/)"
    leaf_when          = None
    root_when          = None
    phase              = 0

    # Accumulated: pkg_name -> list of absolute paths to skill *directories*
    skill_dirs: Dict[str, List[str]] = dc.field(default_factory=dict)
    # State from previous run (loaded from ivpm.json via handler_state)
    _prev_state: dict = dc.field(default_factory=dict, init=False, repr=False)

    def reset(self):
        self.skill_dirs = {}

    def on_root_pre_load(self, update_info: ProjectUpdateInfo):
        self.reset()
        self._prev_state = update_info.handler_state.get("agents", {})

    def on_leaf_post_load(self, pkg: Package, update_info):
        if not hasattr(pkg, "path") or pkg.path is None:
            return
        if getattr(pkg, "src_type", None) == "pypi":
            return

        dep_skill_patterns = self._get_skill_patterns(pkg)

        if dep_skill_patterns is not None:
            found = []
            for pattern in dep_skill_patterns:
                matches = glob.glob(pattern, root_dir=pkg.path, recursive=True)
                if not matches:
                    _logger.warning(
                        "Package %s: skill pattern '%s' matched no files", pkg.name, pattern)
                    continue
                for match in sorted(matches):
                    skill_file = os.path.join(pkg.path, match)
                    if self._validate_frontmatter(skill_file, pkg.name):
                        found.append(os.path.dirname(skill_file))
        else:
            found = []
            for candidate in ("SKILLS.md", "SKILL.md"):
                skill_file = os.path.join(pkg.path, candidate)
                if os.path.isfile(skill_file):
                    if self._validate_frontmatter(skill_file, pkg.name):
                        found.append(pkg.path)
                    break

        if found:
            with self._lock:
                self.skill_dirs[pkg.name] = found

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        project_dir = update_info.project_dir or os.path.dirname(update_info.deps_dir)
        agents_cfg  = update_info.handler_configs.get("agents", {}) or {}
        do_claude   = bool(agents_cfg.get("claude", False))

        targets = [os.path.join(project_dir, ".agents", "skills")]
        if do_claude:
            targets.append(os.path.join(project_dir, ".claude", "skills"))

        # Clean up entries from the previous run before writing new ones
        self._remove_managed(project_dir, self._prev_state)

        if not self.skill_dirs:
            return

        for tgt in targets:
            os.makedirs(tgt, exist_ok=True)

        use_symlinks = _symlinks_supported(targets[0])

        new_agents_names: List[str] = []
        new_claude_names: List[str] = []
        for pkg_name, dirs in sorted(self.skill_dirs.items()):
            for idx, skill_dir in enumerate(dirs, start=1):
                dest_name = pkg_name if len(dirs) == 1 else "%s-%d" % (pkg_name, idx)
                new_agents_names.append(dest_name)
                if do_claude:
                    new_claude_names.append(dest_name)
                for tgt in targets:
                    dest = os.path.join(tgt, dest_name)
                    if use_symlinks:
                        rel_target = os.path.relpath(skill_dir, tgt)
                        os.symlink(rel_target, dest)
                    else:
                        _copy_skill_dir(skill_dir, dest)

        total = sum(len(v) for v in self.skill_dirs.values())
        note("Populated .agents/skills/ with %d skill(s) (%s)" % (
            total, "symlinks" if use_symlinks else "copies"))

    def get_state_entries(self) -> dict:
        """Persist the list of created entries so the next run can clean them up."""
        entries = {}
        if self.skill_dirs:
            agents_names = []
            for pkg_name, dirs in sorted(self.skill_dirs.items()):
                for idx in range(1, len(dirs) + 1):
                    name = pkg_name if len(dirs) == 1 else "%s-%d" % (pkg_name, idx)
                    agents_names.append(name)
            entries["agents_skills"] = agents_names
            # claude_skills is the same set when enabled; handler_configs are
            # not available here, so store agents_names unconditionally and let
            # _remove_managed decide which dirs to clean based on what exists.
            entries["claude_skills"] = agents_names
        return entries

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _remove_managed(project_dir: str, prev_state: dict):
        """Remove symlinks/copies written by the previous run."""
        for key, subdir in (("agents_skills", ".agents/skills"),
                             ("claude_skills", ".claude/skills")):
            names = prev_state.get(key, [])
            if not names:
                continue
            skills_dir = os.path.join(project_dir, subdir)
            for name in names:
                entry = os.path.join(skills_dir, name)
                if os.path.islink(entry):
                    os.unlink(entry)
                elif os.path.isdir(entry):
                    shutil.rmtree(entry)

    def _get_skill_patterns(self, pkg) -> Optional[List[str]]:
        """Return skill glob patterns using priority order, or None to fall back to auto-probe.

        Priority:
          1. pkg.agents_config['skills']  — set from dep-entry 'agents:' in consumer ivpm.yaml
          2. dep's own ivpm.yaml with.agents.skills
          3. None  → caller falls through to auto-probe

        Each entry in the returned list is a glob pattern (or a literal path, which
        is a degenerate glob pattern).  Expansion is done with glob.glob() by the
        caller.
        """
        # Priority 1: consumer-specified override on the dep entry
        dep_agents = getattr(pkg, "agents_config", None) or {}
        if dep_agents.get("skills") is not None:
            return [str(p) for p in dep_agents["skills"]]

        # Priority 2: dep's own ivpm.yaml
        if not os.path.isfile(os.path.join(pkg.path, "ivpm.yaml")):
            return None
        try:
            from ..proj_info import ProjInfo
            info = ProjInfo.mkFromProj(pkg.path)
        except Exception:
            return None
        if info is None:
            return None
        cfg = info.handler_configs.get("agents", {}) or {}
        skills_list = cfg.get("skills", None)
        return [str(p) for p in skills_list] if skills_list is not None else None

    def _validate_frontmatter(self, path: str, pkg_name: str) -> bool:
        fields = _parse_frontmatter(path)
        if not fields:
            _logger.warning("Package %s: %s has missing/malformed frontmatter; skipping", pkg_name, path)
            return False
        if not fields.get("name") or not fields.get("description"):
            _logger.warning("Package %s: %s frontmatter missing 'name' or 'description'; skipping", pkg_name, path)
            return False
        return True


def _symlinks_supported(dest_parent: str) -> bool:
    """Return True if the filesystem at dest_parent supports symlinks."""
    probe = os.path.join(dest_parent, ".ivpm_symlink_probe")
    try:
        os.symlink(".", probe)
        os.remove(probe)
        return True
    except (OSError, NotImplementedError):
        return False


def _copy_skill_dir(src_dir: str, dest_dir: str):
    """Fallback copy: SKILL.md / SKILLS.md plus companion directories."""
    os.makedirs(dest_dir, exist_ok=True)
    for fname in ("SKILL.md", "SKILLS.md"):
        src = os.path.join(src_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_dir, fname))
    for companion in _COMPANION_DIRS:
        src = os.path.join(src_dir, companion)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest_dir, companion))
```

`_parse_frontmatter` is carried over verbatim from the current skills handler.


## Entry-Point Registration (`pyproject.toml`)

```toml
[project.entry-points."ivpm.handlers"]
python  = "ivpm.handlers.package_handler_python:PackageHandlerPython"
direnv  = "ivpm.handlers.package_handler_direnv:PackageHandlerDirenv"
agents  = "ivpm.handlers.package_handler_agents:PackageHandlerAgents"
```

The `skills` entry is **removed**.


## `ivpm.yaml` Schema

The JSON schema (`src/ivpm/schema/ivpm.json`) needs two additions, both
referencing the same **skill-spec definition**.

### Shared `$defs/agentsSkillSpec`

```json
"agentsSkillSpec": {
  "type": "object",
  "properties": {
    "skills": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Glob patterns (relative to package root) for SKILL.md files. Expanded with glob.glob(recursive=True). Absent = auto-probe."
    }
  },
  "additionalProperties": false
}
```

### `package.with.agents` (root-project config)

Extends `agentsSkillSpec` with the project-level `claude` key:

```json
"agents": {
  "allOf": [{ "$ref": "#/$defs/agentsSkillSpec" }],
  "properties": {
    "claude": { "type": "boolean", "default": false,
                "description": "When true, also mirror skill entries to .claude/skills/" }
  }
}
```

### `deps[*].agents` (per-dep override on a dep entry)

Uses `agentsSkillSpec` directly — no extra keys:

```json
"agents": { "$ref": "#/$defs/agentsSkillSpec" }
```

This enforces that the `skills:` format is identical in both locations.

`_KNOWN_PACKAGE_KEYS` in `ivpm_yaml_reader.py` does not need updating; the
`agents` dep-entry key is added explicitly to the parser.  Handler-level keys
under `package.with:` are already validated dynamically via handler-name
discovery in `_read_with_section`.


## Test Plan

New test file: `test/unit/test_agents.py`

| Test | Scenario |
|---|---|
| `test_agents_dir_created` | Single dep with root SKILL.md → `.agents/skills/<pkg>` symlink created pointing at dep dir |
| `test_symlink_is_relative` | Symlink target is a relative path (not absolute) |
| `test_skill_md_fallback` | Dep has only SKILL.md (not SKILLS.md) → still picked up |
| `test_claude_false_default` | `claude:` absent → `.claude/` not created |
| `test_claude_true` | `claude: true` → both `.agents/skills/` and `.claude/skills/` populated |
| `test_declared_skill_paths` | Dep's ivpm.yaml lists non-root path → that directory is linked |
| `test_declared_paths_override_probe` | Dep declares `skills:` → default probe is not used |
| `test_missing_declared_path_warns` | Declared path does not exist → warning, package skipped |
| `test_multiple_skill_files` | Dep declares two paths → links named `<pkg>-1`, `<pkg>-2` |
| `test_no_skills_no_dir` | No deps with skills → `.agents/` not created |
| `test_bad_frontmatter_warns` | Malformed frontmatter → warning, package skipped |
| `test_multiple_packages` | Two deps each with SKILL.md → both appear in `.agents/skills/` |
| `test_stale_links_removed` | Second `ivpm update` after removing a dep → old symlink/dir removed |
| `test_companion_dirs_copied_on_fallback` | On copy fallback: `scripts/`, `references/`, `assets/` are copied alongside SKILL.md |
| `test_symlink_skill_md_accessible` | SKILL.md is readable through the created symlink |

| `test_dep_spec_skill_patterns` | Dep entry with glob pattern `skills/**/SKILL.md` — all matches linked |
| `test_dep_spec_pattern_no_match_warns` | Glob pattern with no matches → warning logged, no link |
| `test_dep_spec_overrides_self` | Dep entry `agents:` takes priority over dep's own `ivpm.yaml with.agents.skills` |
| `test_dep_spec_no_ivpm_yaml` | Non-IVPM dep (no `ivpm.yaml`) with `agents:` in consumer dep-entry — works without error |

Notes on symlink testing:
- Use `os.path.islink()` to assert a symlink was created
- Use `os.readlink()` and check the result is a relative path (does not start with `/`)
- Use `os.path.isfile(os.path.join(link, "SKILL.md"))` to verify the link resolves
- The copy-fallback path is exercised by monkey-patching `_symlinks_supported` to return `False`

Test fixtures needed (in `test/unit/data/`):

- `agents_leaf1/` — `SKILLS.md` with valid frontmatter (reuse or alias `skills_leaf1/`)
- `agents_leaf2/` — `SKILL.md` with valid frontmatter (reuse or alias `skills_leaf2/`)
- `agents_with_assets/` — `SKILL.md` + `scripts/`, `assets/` subdirs
- `agents_multi_skill/` — `ivpm.yaml` declaring two paths; both `SKILL.md` and
  `subdir/SKILL.md` present
- `agents_glob_tree/` — a tree of `skills/<A>/SKILL.md`, `skills/<B>/SKILL.md`
  to exercise `skills/**/SKILL.md` patterns
- `agents_bad_frontmatter/` — `SKILL.md` with malformed frontmatter


## Files to Create / Modify

| File | Action |
|---|---|
| `src/ivpm/handlers/package_handler.py` | **Edit** – add `get_state_entries() -> dict` hook |
| `src/ivpm/handlers/package_handler_list.py` | **Edit** – aggregate `get_state_entries()` across handlers |
| `src/ivpm/handlers/package_handler_agents.py` | **Create** new handler |
| `src/ivpm/handlers/package_handler_skills.py` | **Delete** |
| `src/ivpm/package.py` | **Edit** – add `agents_config: Optional[dict]` field to `Package` |
| `src/ivpm/ivpm_yaml_reader.py` | **Edit** – read `agents:` dep-entry key into `pkg.agents_config` |
| `src/ivpm/project_ops_info.py` | **Edit** – add `project_dir` and `handler_state` fields to `ProjectUpdateInfo` |
| `src/ivpm/project_ops.py` | **Edit** – populate `project_dir` and `handler_state`; persist `get_state_entries()` into `ivpm.json` |
| `pyproject.toml` | **Edit** – replace `skills` entry point with `agents` |
| `src/ivpm/schema/ivpm.json` | **Edit** – add `agents` property to dep-entry and to `package.with` |
| `test/unit/test_agents.py` | **Create** new test file |
| `test/unit/test_skills.py` | **Delete** |
| `test/unit/data/agents_leaf1/` etc. | **Create** new fixtures |

## Open Questions

1. **`.agents` at project root vs inside `packages/`** — this design places
   `.agents/` at `<project-root>/`, which means the handler needs `project_dir`
   (see above).  Confirm this is correct.

2. **Link-name collision** — if two packages share the same name (edge case)
   the second will silently overwrite the first.  A warning could be added.

3. **`share/skill.md`** — the IVPM own skill file at
   `src/ivpm/share/skill.md` is currently not installed anywhere by the handler.
   Should it be included in the root project's `.agents/skills/ivpm`?
   (Probably not — it describes IVPM itself, useful to the human developer but
   not necessarily to an agent running inside the project.)
