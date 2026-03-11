# IVPM `show` Command — Design Plan

## Problem Statement

IVPM has two distinct extension registries — **package sources** (`PkgTypeRgy`) and **package
content types** (`PkgContentTypeRgy`) — plus **package handlers** (`PackageHandlerRgy`).
There is currently no way for users (or AI agents) to discover what sources, types, or
handlers are registered, what parameters they accept, or what those parameters mean.

This plan defines a new `ivpm show` command with sub-commands that expose this
self-describing capability at the CLI level.

---

## Proposed Command Surface

```
ivpm show source                    # list all registered package sources
ivpm show source <name>             # detailed info on one source
ivpm show type                      # list all registered content types
ivpm show type <name>               # detailed info on one type
ivpm show handler                   # list all registered handlers
ivpm show handler <name>            # detailed info on one handler
```

> **Future**: `ivpm show package <pkg>` to inspect an individual package in the
> current workspace (from `package-lock.json`). Out of scope for this change but
> the architecture should accommodate it.

---

## Current State (what exists)

| Registry | Class | Stored info today |
|---|---|---|
| Package sources | `PkgTypeRgy` | `(factory_fn, description_str)` tuples; description is a bare string |
| Content types | `PkgContentTypeRgy` | `PkgContentType` instances; `get_json_schema()` already defined on base class |
| Handlers | `PackageHandlerRgy` | class objects; `name`, `description`, `phase`, `leaf_when`, `root_when` ClassVars |

`PkgContentType.get_json_schema()` is the most mature self-description facility.
`PackageHandler` already has `name` and `description` ClassVars.
`PkgTypeRgy` stores only a bare string description — this must be augmented.

---

## Required Changes

### 1. Self-description protocol for package sources (`PkgTypeRgy`)

**Today:** `src2fact_m[name] = (factory_fn, description_str)`

**Problem:** The description is a bare string with no parameter documentation. The factory
function itself (e.g. `PackageGit.create`) carries all the parameter knowledge inside
`process_options()`, but that knowledge is not queryable.

**Decision: unified dataclass approach** (see Resolved Decisions section).

All three registries use shared base dataclasses from `ivpm/show/info_types.py`:

```python
# show/info_types.py  (new file)
import dataclasses as dc
from typing import List, Optional

@dc.dataclass
class ParamInfo:
    name: str
    description: str
    required: bool = False
    default: Optional[str] = None
    type_hint: str = "str"   # "str" | "bool" | "int" | "url"

@dc.dataclass
class RegistryEntryInfo:
    name: str
    description: str
    params: List[ParamInfo] = dc.field(default_factory=list)
    notes: str = ""
    origin: str = "built-in"  # "built-in" | entry-point name
```

`PkgSourceInfo` subclasses `RegistryEntryInfo` with no additional fields.
Add a classmethod `source_info() -> PkgSourceInfo` to each `Package*` class.

---

### 2. `PkgTypeRgy` registry changes

Currently stores `(factory_fn, description_str)`. Change to store
`(factory_fn, PkgSourceInfo)`. The `register()` call in `_load()` can be updated to
pass an info object instead of a bare string.

```python
def register(self, src: str, f: Callable, info: 'PkgSourceInfo'):
    ...
    self.src2fact_m[src] = (f, info)

def getSourceInfo(self, src: str) -> 'PkgSourceInfo':
    return self.src2fact_m[src][1]

def getAllSourceInfo(self) -> List['PkgSourceInfo']:
    return [v[1] for v in self.src2fact_m.values()]
```

---

### 3. `PackageHandler` self-description

`PackageHandler` already has `name` and `description` ClassVars (the latter is often
`None`). Extend with:

```python
class PackageHandler:
    name:        ClassVar[Optional[str]] = None
    description: ClassVar[Optional[str]] = None
    phase:       ClassVar[int]  = 0

    # NEW: human-readable parameter list
    @classmethod
    def handler_info(cls) -> 'HandlerInfo':
        """Return self-description for 'ivpm show handler <name>'."""
        from ..show.handler_info import HandlerInfo
        return HandlerInfo(
            name=cls.name or "unknown",
            description=cls.description or "",
            phase=cls.phase,
        )
```

`HandlerInfo` subclasses `RegistryEntryInfo` with handler-specific fields:

```python
@dc.dataclass
class HandlerInfo(RegistryEntryInfo):
    phase: int = 0
    conditions: str = ""       # human-readable summary of leaf_when / root_when
    cli_options: List[str] = dc.field(default_factory=list)
    # e.g. ["update: --py-uv", "update: --py-pip", "update: --py-system-site-packages"]
```

Each concrete handler class overrides `handler_info()` to add its specific parameters.

---

### 4. `PkgContentType` self-description (already partial)

`PkgContentType.get_json_schema()` is already defined and implemented on
`PythonContentType`. What's missing is a human description of each parameter.

`ContentTypeInfo` subclasses `RegistryEntryInfo` with no extra fields. Add
`content_type_info() -> ContentTypeInfo` to `PkgContentType`. The existing
`get_json_schema()` method is retained as a secondary output derived from the
`ContentTypeInfo` dataclass (for the schema-generation feature, see item 11 in
Resolved Decisions).

---

### 5. New files

```
src/ivpm/
  show/
    __init__.py
    info_types.py        # RegistryEntryInfo, ParamInfo, PkgSourceInfo, ContentTypeInfo, HandlerInfo
    cmd_show.py          # CmdShow dispatcher + argparse setup
    show_source.py       # renders source list / detail
    show_type.py         # renders content-type list / detail
    show_handler.py      # renders handler list / detail
```

---

### 6. `cmd_show.py` — new command

Pattern mirrors `cmd_cache.py` (nested sub-parser):

```python
class CmdShow:
    def __call__(self, args):
        sub = args.show_cmd
        if sub == "source":
            ShowSource()(args)
        elif sub == "type":
            ShowType()(args)
        elif sub == "handler":
            ShowHandler()(args)
```

Registered in `__main__.py`. `show_subparser.required = False` so that `ivpm show`
with no sub-command renders all three categories:

```python
show_cmd = subparser.add_parser("show",
    help="Show registered package sources, types, and handlers")
show_subparser = show_cmd.add_subparsers(dest="show_cmd")
show_subparser.required = False   # ivpm show alone → show all categories

show_source_cmd = show_subparser.add_parser("source",
    aliases=["src"],
    help="List registered package sources")
show_source_cmd.add_argument("name", nargs="?",
    help="Show details for this source (omit to list all)")
show_source_cmd.add_argument("--json", action="store_true",
    help="Emit JSON instead of rich/text output")
show_source_cmd.add_argument("--no-rich", action="store_true",
    help="Plain-text output without Rich formatting")

show_type_cmd = show_subparser.add_parser("type",
    help="List registered content types")
show_type_cmd.add_argument("name", nargs="?",
    help="Show details for this type (omit to list all)")
show_type_cmd.add_argument("--json", action="store_true")
show_type_cmd.add_argument("--no-rich", action="store_true")

show_handler_cmd = show_subparser.add_parser("handler",
    help="List registered package handlers")
show_handler_cmd.add_argument("name", nargs="?",
    help="Show details for this handler (omit to list all)")
show_handler_cmd.add_argument("--json", action="store_true")
show_handler_cmd.add_argument("--no-rich", action="store_true")

show_cmd.add_argument("--schema", action="store_true",
    help="Emit a JSON Schema for ivpm.yaml covering all sources and types")

show_cmd.set_defaults(func=CmdShow())
```

---

### 7. Rich output format

Use `rich` (already a dependency).

#### List view (`ivpm show source`)

```
┌────────────┬──────────────────────┬───────────────────────────────┐
│ Source     │ Description          │ Key Parameters                │
├────────────┼──────────────────────┼───────────────────────────────┤
│ git        │ Git repository       │ url, branch, tag, commit      │
│ pypi       │ Python Package Index │ version, extras               │
│ gh-rls     │ GitHub Release       │ url, version, file            │
│ http       │ HTTP/HTTPS archive   │ url, sha256                   │
│ dir        │ Local directory      │ url (file://), link           │
│ file       │ Local file           │ url (file://)                 │
│ url        │ Generic URL          │ url                           │
└────────────┴──────────────────────┴───────────────────────────────┘
```

#### Detail view (`ivpm show source git`)

```
Source: git
Description: Git repository (full clone by default; supports caching and read-only modes)

Parameters:
  url        (required)  Repository URL (https:// or git@...)
  branch                 Branch to check out (default: repo default branch)
  tag                    Tag to pin to (disables sync)
  commit                 Specific commit SHA to pin to
  depth                  Shallow-clone depth (integer)
  cache      bool        Cache this package (true/false/omit for editable)
  anonymous  bool        Clone via HTTPS instead of SSH

Notes:
  When cache: true, IVPM uses a shared cache and creates a read-only
  symlink in packages/. When cache: false, a shallow clone is made
  without symlink. Omitting cache results in a full editable clone.
```

---

## Resolved Decisions

These items were open issues; decisions have been made and are incorporated into the design.

1. **Naming: `source` vs `src`** — **Decision: support both as aliases.** The canonical
   sub-command name is `source` (human-friendly); `src` is accepted as an alias.
   argparse `aliases=["src"]` on the sub-parser handles this transparently.
   Internal registry names (`src_type`, `src2fact_m`) are unchanged.

2. **`show type` vs `show content-type`** — **Decision: use `type`.** Matches the
   `type:` YAML field users write every day. The `source`/`type` split is clear enough:
   sources describe where a package comes from; types describe what IVPM does with it.

3. **Unification of self-description formats** — **Decision: unify around dataclasses.**
   `PkgContentType.get_json_schema()` exists but is the only JSON-schema user today;
   `PkgContentType` is relatively new and has no external consumers of the schema.
   Replace / supplement it with a `content_type_info()` method returning a
   `ContentTypeInfo` dataclass (parallel to `PkgSourceInfo` and `HandlerInfo`).
   `get_json_schema()` can be retained as a secondary derived output (computed from the
   dataclass) for the schema-generation use case (see item 11 below), but the
   dataclass is the source of truth.

   Shared base dataclasses to introduce:
   ```python
   # ivpm/show/info_types.py  (new, shared by all three registries)
   @dc.dataclass
   class ParamInfo:
       name: str
       description: str
       required: bool = False
       default: Optional[str] = None
       type_hint: str = "str"   # "str" | "bool" | "int" | "url"

   @dc.dataclass
   class RegistryEntryInfo:
       name: str
       description: str
       params: List[ParamInfo] = dc.field(default_factory=list)
       notes: str = ""
       origin: str = "built-in"  # "built-in" | entry-point name (see item 13)
   ```
   `PkgSourceInfo`, `ContentTypeInfo`, and `HandlerInfo` each subclass or alias
   `RegistryEntryInfo`, adding any domain-specific fields (e.g. `phase` for handlers,
   `conditions` for handler activation logic).

4. **Third-party plugin backward compat** — `PkgTypeRgy.register()` will accept either
   a `str` (old API, auto-wrapped into a minimal `PkgSourceInfo`) or a `PkgSourceInfo`
   object. Third-party plugins passing a bare string continue to work; they just get
   no parameter documentation.

5. **`leaf_when` / `root_when` conditions** — Each handler adds a `conditions_summary`
   ClassVar string (plain English, e.g. `"leaf: always; root: only when Python packages
   present"`). The `show handler` detail view renders this string. The callable objects
   themselves are not exposed in output.

6. **`ivpm show` with no sub-command** — Shows a compact summary: one table per
   category (sources, types, handlers), each with name + description columns. Same
   output as running all three list commands in sequence. argparse `required=False` on
   the nested sub-parser; `CmdShow.__call__` detects missing `show_cmd` and renders all.

7. **`--json` flag** — Included in scope. All `show` sub-commands accept `--json` and
   emit a JSON array of `RegistryEntryInfo` objects (serialized via `dataclasses.asdict`).
   This enables programmatic consumption by scripts and AI agents.

8. **`--no-rich` flag** — Included, following the `sync` / `status` precedent. When
   set (or when stdout is not a TTY), output falls back to plain-text tables.

## Confirmed Scope Additions

These were "overlooked opportunities" that are now confirmed in scope:

9. **`ivpm show package <name>` (future, not this change)** — Architecture must not
   block this. `RegistryEntryInfo` and the rendering layer should be designed so that
   a future `PackageInstanceInfo` (from the lock file) can slot in naturally.

10. **`skill.md` update** — **Prioritized.** Updating `src/ivpm/share/skill.md` moves
    to step 2 of the implementation order (right after the data model), so AI agents
    can use `ivpm show` for introspection from the moment the command ships.

11. **Schema generation** — **In scope as `ivpm show --schema`.** When `--schema` is
    passed to `ivpm show` (top-level, not a sub-command), emit a complete JSON Schema
    for `ivpm.yaml` derived from all registered source and content-type `RegistryEntryInfo`
    objects. This is valuable for IDE integrations, validation tools, and front-ends.
    The `get_json_schema()` method on `PkgContentType` becomes a helper that converts
    a `ContentTypeInfo` dataclass → JSON Schema fragment; the top-level schema combines
    all fragments.

12. **`ivpm list` alias** — **Deferred.** No strong motivation; argparse aliases are
    easy to add later if users request it.

13. **Extension-point origin** — `RegistryEntryInfo.origin` stores `"built-in"` for
    core registrations and the entry-point name (e.g. `"mypkg.ext"`) for plugins.
    `ivpm show source` displays an "Origin" column (hidden if all entries are built-in).

14. **Handler CLI options (`add_options()`)** — `HandlerInfo` gains an
    `cli_options: List[str]` field. Each handler's `handler_info()` override populates
    this with the sub-command names and option names it registers (e.g. `["update:
    --py-uv", "update: --py-pip"]`). The `show handler <name>` detail view lists these.

---

## File Change Summary

| File | Change |
|---|---|
| `show/__init__.py` | **NEW** |
| `show/info_types.py` | **NEW** — `RegistryEntryInfo`, `ParamInfo`, `PkgSourceInfo`, `ContentTypeInfo`, `HandlerInfo` |
| `show/cmd_show.py` | **NEW** — `CmdShow` dispatcher; handles `ivpm show` (all), `--schema` |
| `show/show_source.py` | **NEW** — list/detail/JSON rendering for sources |
| `show/show_type.py` | **NEW** — list/detail/JSON rendering for content types |
| `show/show_handler.py` | **NEW** — list/detail/JSON rendering for handlers |
| `pkg_types/pkg_type_rgy.py` | **EXTEND** — `register()` accepts `str` or `PkgSourceInfo`; add `getSourceInfo()`, `getAllSourceInfo()` |
| `pkg_types/package_url.py` | **EXTEND** — add `source_info()` classmethod (base URL + cache params) |
| `pkg_types/package_git.py` | **EXTEND** — override `source_info()` with full param docs |
| `pkg_types/package_pypi.py` | **EXTEND** — override `source_info()` |
| `pkg_types/package_gh_rls.py` | **EXTEND** — override `source_info()` |
| `pkg_types/package_http.py` | **EXTEND** — override `source_info()` |
| `pkg_types/package_dir.py` | **EXTEND** — override `source_info()` |
| `pkg_types/package_file.py` | **EXTEND** — override `source_info()` |
| `handlers/package_handler.py` | **EXTEND** — add `handler_info()` classmethod; add `conditions_summary` ClassVar |
| `handlers/package_handler_python.py` | **EXTEND** — override `handler_info()` with params + `conditions_summary` + `cli_options` |
| `handlers/package_handler_direnv.py` | **EXTEND** — override `handler_info()` |
| `handlers/package_handler_skills.py` | **EXTEND** — override `handler_info()` |
| `pkg_content_type.py` | **EXTEND** — add `content_type_info()` method; `get_json_schema()` derived from it |
| `__main__.py` | **EXTEND** — register `show` subcommand with nested parser; `src` alias; `--schema`; `--json`; `--no-rich` |
| `share/skill.md` | **UPDATE** — document `ivpm show` for AI agents (prioritized) |

---

## Implementation Order

1. `show/info_types.py` — shared dataclasses (no dependencies, pure data)
2. `share/skill.md` — update AI agent skill docs (**prioritized**)
3. `pkg_types/pkg_type_rgy.py` — registry changes to accept `PkgSourceInfo`
4. `source_info()` classmethods on all `Package*` classes (mechanical; start with `package_git.py` as the richest example)
5. `pkg_content_type.py` — add `content_type_info()` to `PkgContentType` and concrete types; derive `get_json_schema()` from it
6. `handlers/package_handler.py` — add `handler_info()`, `conditions_summary` ClassVar; concrete handlers follow
7. `show/cmd_show.py` + `show/show_source.py` — wire up `ivpm show source`
8. `show/show_type.py` + `show/show_handler.py` — wire up `type` and `handler`
9. `__main__.py` — register the `show` command (with `src` alias, `--schema`, `--json`, `--no-rich`)
10. Schema generation — `ivpm show --schema` combining all source + type info
11. Tests

---

## Testing Considerations

- Unit-test each `source_info()` to confirm params match actual `process_options()` keys
- Unit-test `content_type_info()` round-trips to `get_json_schema()` correctly
- Smoke-test `ivpm show` (no sub-command — all three tables)
- Smoke-test `ivpm show source`, `ivpm show src` (alias), `ivpm show type`, `ivpm show handler`
- Smoke-test `ivpm show source git` detail view
- Smoke-test `ivpm show source --json` produces valid JSON
- Smoke-test `ivpm show --schema` produces valid JSON Schema
- Smoke-test with a 3rd-party plugin registered (string-description compat path)
- Test `--no-rich` plain-text fallback
