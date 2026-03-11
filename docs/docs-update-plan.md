# Documentation Update Plan

## Context

This plan covers updates to `docs/source/` RST files to:

1. Document the new `ivpm show` command
2. Fully describe the three built-in handlers (Python, Direnv, Skills)
3. Connect package types and sources to the handlers that process them
4. Point readers from static docs to `ivpm show` for live introspection

---

## Gap Analysis

### `reference.rst`
- **`ivpm show` is entirely absent.** The command list covers activate, build,
  cache, clone, init, pkg-info, share, snapshot, status, sync, update — but not
  `show`.
- No mention of `--version` / `-V`.

### `extending_ivpm.rst`
- Covers handler *architecture* (base class, conditions, threads, entry points)
  comprehensively.
- **Built-in handlers are named but never described.** The phrase "Built-in
  handlers (Python, Direnv, Skills)" appears exactly once, in the ordering
  section, with no details.
- No description of what each built-in does, when it activates, or what CLI
  options it contributes.

### `package_types.rst`
- Source types and package types are well documented.
- **No connection drawn between package types and handlers.** A reader learns
  what `type: python` does at the YAML level but not that a *Python handler*
  runs at install time and which CLI flags control it.
- No mention of `ivpm show` for live introspection.

### `reference.rst` (global options)
- Documents `--log-level` but not `--version` / `-V`.

---

## Planned Changes

### 1. `reference.rst` — Add `ivpm show` command

Add a new section after `sync` / before Global Options. Structure:

```
ivpm show
---------

Overview  (ivpm show with no sub-command)
  - shows all registries side by side
  - table: Source Types | Content Types | Handlers

Sub-commands
  source / src [<name>]   — list or detail a source type
  type [<name>]           — list or detail a content type
  handler [<name>]        — list or detail a handler

Flags
  --json        emit JSON instead of Rich/plain text
  --no-rich     plain text (no terminal colours)
  --schema      emit JSON Schema for the full registry (top-level flag)
```

For each sub-command: show the output format, note the `--json` option, and
give at least one example invocation.

Also add `--version` / `-V` to Global Options.

---

### 2. `extending_ivpm.rst` — Document built-in handlers

Add a new top-level section **"Built-in Handlers"** with three sub-sections.
For each handler document:

- What it does
- Activation conditions (`leaf_when` / `root_when`)
- Any `with:` parameters it reads from `ivpm.yaml`
- CLI options it registers (from `add_options()`)
- Output files it produces

#### Python Handler (`python`)

- **Leaf phase:** detects packages that are Python (has `pyproject.toml` /
  `setup.py` / `src: pypi`); records them; sets `pkg.pkg_type = "python"`.
- **Root phase:** activated by `HasType("python")`. Creates / updates the
  `packages/python` virtual environment; installs all detected packages via
  pip or uv.
- **`with:` parameters:** `editable` (bool, default true), `extras` (str or
  list).
- **CLI options (on `update` and `clone`):**
  - `--py-uv` / `--py-pip` — venv manager selection
  - `--skip-py-install` — skip install step
  - `--force-py-install` — force reinstall
  - `--py-prerls-packages` — allow pre-release packages
  - `--py-system-site-packages` — inherit system site-packages

#### Direnv Handler (`direnv`)

- **Leaf phase:** always runs; looks for `.envrc` or `export.envrc` in each
  package directory.
- **Root phase:** always runs (even if no `.envrc` files were found — it
  writes an empty/minimal file so `direnv allow` always works). Collects all
  discovered env files and generates `packages/packages.envrc`.
- **No `with:` parameters.**
- **No CLI options.**
- Output: `packages/packages.envrc` — a single file that sources all
  per-package `.envrc` / `export.envrc` files.

#### Skills Handler (`skills`)

- **Leaf phase:** always runs; looks for `SKILL.md` or `SKILLS.md` in each
  package.
- **Root phase:** always runs. Concatenates all discovered skill files and
  writes `packages/SKILLS.md` — a unified skills reference for AI agents.
- **No `with:` parameters.**
- **No CLI options.**
- Output: `packages/SKILLS.md`.

After the new section, add a callout:

> **Tip:** Run ``ivpm show handler`` to see all registered handlers (built-in
> and third-party) and their capabilities. Use ``ivpm show handler python``
> for full details on the Python handler, including all CLI options.

Also add a similar tip near the top of the existing "Registering a Handler via
Entry Points" section noting that `ivpm show handler` will confirm successful
registration.

---

### 3. `package_types.rst` — Link types to handlers

**After the Python type section:** Add a note explaining the handler
relationship:

> When a package has ``type: python`` (explicitly or via auto-detection), the
> **Python handler** installs it into ``packages/python/`` during the root
> phase. See :ref:`builtin-python-handler` in :doc:`extending_ivpm` for full
> details on installation options and CLI flags.

**After the Raw type section:** Add a note:

> Packages with ``type: raw`` are intentionally not processed by any built-in
> handler — they are simply placed in ``packages/<name>/`` and left as-is.
> This is useful for IP cores, data files, and pre-built binaries.

**At the bottom of the file (See Also section):** Add:

> - :doc:`extending_ivpm` — Built-in handler details and writing custom handlers
> - Use ``ivpm show source`` and ``ivpm show type`` to list all registered
>   sources and types with live documentation.

---

## Open Questions / Notes

- **`url` source type:** There is a `url` source (generic URL resolved by
  extension) in addition to `http`. `package_types.rst` currently documents
  `http` but not the generic `url` source. Should `url` be added to the source
  reference table? *(Likely yes — `ivpm show source url` already shows it.)*

- **Ordering in `reference.rst`:** The command list is currently alphabetical
  (activate → update). `show` falls between `share` and `snapshot` — confirm
  this is where it should go.

- **`ivpm --version`:** Currently undocumented everywhere. Should appear in
  Global Options or a new "Version" section in reference.rst.

- **`ivpm show --schema` output:** Should the schema output format be described
  in reference.rst, or is the `--json` + structure self-explanatory?

- **Direnv and Skills in `integrations.rst`:** These handlers produce files that
  are used by downstream tools (direnv, AI agents). Consider adding brief
  callouts in `integrations.rst` pointing to the built-in handler docs. For
  now, this is marked optional/stretch.

---

## File-by-File Summary

| File | Change Size | Priority |
|------|-------------|----------|
| `reference.rst` | Medium — new `ivpm show` section + `--version` in global opts | High |
| `extending_ivpm.rst` | Medium — new "Built-in Handlers" section + two cross-ref tips | High |
| `package_types.rst` | Small — 3 callout notes + See Also additions | Medium |
| `integrations.rst` | Optional — direnv + skills callouts | Low |
| `index.rst` | None needed | — |
