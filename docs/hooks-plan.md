# Project hooks — implementation, test and doc plan

The design is in [`hooks-design.md`](hooks-design.md). This file tracks the
work. Tick each box as it lands, and update **Status** at the end of each
step, so a fresh session can pick up from here.

## Status

- **Current step:** all steps are done, except a real Sphinx build of the
  docs (see step 9). Nothing is committed.
  - Full suite: 3455 passed, 23 skipped.
  - New tests: 33 unit tests in `test/unit/test_hooks.py` and 5 end-to-end
    tests in `test/unit/test_hooks_e2e.py`.
  - Updated tests: `test_handler_rgy_builtins.py` and `test_show.py`, for the
    new built-in handler.
- **Last updated:** 2026-11-13
- **Notes:**
  - Hooks are also skipped for `update --from <url>` (the manifest is not the
    project's own). This is enforced in `project_ops.update()` next to the
    toolchain-mode check.
  - `toolchain_support` is left SUPPORTED: UNSUPPORTED would print a skip
    note on every `ivpm install`, and toolchain mode already passes no hooks.
  - `resolve_venv_mode()` is now a module-level function in
    `package_handler_python.py`; the method delegates to it.
  - Hook output is shown unless the progress display is running
    (`suppress_output`). It is always captured; on failure the tail goes into
    the error, and the full output is logged at debug level.
  - Compatibility: an older ivpm rejects `hooks:` as an unknown package key,
    in a dependency's ivpm.yaml too. This is true of every new key.

## The format being built

```yaml
package:
  hooks:
    - name: egg-info            # optional; unique across the merged list
      event: post-update        # a string or a list of strings; only post-update for now
      python: |                 # Python source, run as <venv>/bin/python -c <source>
        from setuptools import setup
        setup(script_args=["egg_info", "-e", "src"])
```

Rules:
- Only the root manifest's hooks run. They run after `ivpm update` and
  `ivpm clone`, and not after `ivpm sync`.
- The source is left-justified (dedented) before it runs.
- Hooks run in the order they are declared, with the project root as the
  working directory.
- A hook that fails, fails the update.
- `--no-hooks` skips all of them.

## Facts from the code that shape the plan

These were checked against this tree. Each one changes something in the plan
or in the design doc.

1. **Variable syntax is `${{name}}`, not `${...}`.** The escape is `$${{`
   (`variables.py:30-32`), and an undeclared `${{x}}` is fatal. Python source
   almost never contains `${{`, so substitution is harmless. In the rare case
   it is needed (for example, a string literal that holds `${{`), you write
   `$${{`. The design doc says `${...}` and has to be corrected (step 0).
2. **Include merge appends the included file's entries after the local
   ones.** That is how `env` and `setup-deps` work
   (`ivpm_yaml_reader.py` `_merge_pkg`). The design doc says the included
   hooks come first. Recommendation: follow the existing rule, so local hooks
   run first and included hooks after, and correct the doc (step 0).
3. **The python handler creates the venv only when it sees Python packages
   or a `with: python:` block** (`package_handler_python.py:777-784`). A
   project whose only Python is a hook would get no venv. The handler has to
   count a `python:` hook as a reason to create the venv (step 4).
4. **Project-level settings reach handlers through
   `_apply_effective_settings`** (`project_ops.py:116`, called at about line
   357). That call sets them on both update-info objects. Hooks travel the
   same way.
5. **The python handler gates itself inside `on_root_post_load`.** It does
   not use `root_when`. The new hooks handler does the same: it returns early
   when there are no hooks.
6. **The manifest JSON schema** (`src/ivpm/share/ivpm.json`, `package-def`,
   `additionalProperties: false`) has to gain `hooks`. Otherwise editors flag
   it as unknown. `reference.rst` renders the schema with `.. jsonschema::`.
7. **`fatal(msg, loc)`** (`msg.py:123`) together with `_keyloc` /
   `getlocstr` give parse errors that point at the YAML line. Duplicate
   dep-set names already use the "@ loc ; previously defined @ loc" wording.
   Duplicate hook names should use the same wording.
8. **`clone` calls `ProjectOps(target_dir).update(...)` twice**
   (`cmd_clone.py:349,358`) and passes `args` through. The runner reads
   `getattr(args, "no_hooks", False)`, so clone works without new plumbing.
   It only needs the flag registered.

## Step 0 — Correct the design doc

- [x] In `hooks-design.md`, replace the `${...}` bullet with the real rule:
  `${{name}}` is expanded and `$${{` is the escape.
- [x] Change the include-merge order to local first, then included, to match
  `env` and `setup-deps`.

## Step 1 — Model (`src/ivpm/proj_info.py`)

- [x] Add a dataclass:
  ```python
  @dc.dataclass
  class HookSpec:
      events: List[str]          # always a list after parsing
      kind: str                  # "python"
      source: str                # dedented, with blank lines stripped from both ends
      name: Optional[str] = None
      srcinfo: object = None     # YAML location, for runtime error messages

      def label(self, index_in_event: int, event: str) -> str:
          return self.name if self.name else "%s hook #%d" % (event, index_in_event)
  ```
- [x] Add a constant, `HOOK_EVENTS = ("post-update",)`. It is the single
  list of valid events.
- [x] Add `self.hooks : List[HookSpec] = []` to `ProjInfo.__init__`, next to
  `setup_deps`.

## Step 2 — Parser (`src/ivpm/ivpm_yaml_reader.py`)

- [x] In `read()`, right after the `setup-deps` block (about line 486), add
  `if "hooks" in pkg.keys(): self._read_hooks(ret, pkg["hooks"], name, is_root)`.
- [x] `_read_hooks`:
  - If `is_root` is false, log at debug level ("hooks in dependency manifest
    %s are ignored") and return without checking anything. A dependency's
    malformed hooks must never break your update.
  - Each entry must be a mapping. If it is not, call `fatal(...)` anchored on
    the list, the same way dependency entries are checked.
  - Allowed keys are `name`, `event` and `python`. Any other key is fatal,
    with a message that lists the allowed keys and points at `_keyloc(entry, key)`.
  - `event` is required and is a string or a non-empty list of strings. Each
    value must be in `HOOK_EVENTS`; if not, the message lists the valid events.
  - There must be exactly one kind key. Today that means `python` is required.
    Write the check as "exactly one of `_HOOK_KINDS`" so that adding `exec`
    later is a single line.
  - `python` must be a string; a list, a mapping or null is fatal. Normalize
    it with `textwrap.dedent(src).strip("\n")`. If the result is empty, or
    still starts with whitespace, the error is: "hook python source has
    inconsistent indentation" / "is empty".
  - If present, `name` is a non-empty string matching
    `[A-Za-z0-9][A-Za-z0-9._-]*`. Keep this the same as package names so a
    later `--skip-hook <name>` needs no quoting. Duplicates are fatal: "Duplicate
    hook '%s' @ %s ; previously defined @ %s".
  - Do not compile the source at parse time. Python's syntax depends on the
    venv's version, which may differ from the version running ivpm. A syntax
    error shows up when the hook runs, as a normal hook failure.
- [x] Include merge (`_merge_pkg`): add `"hooks"` to `_MERGE_HANDLED_KEYS` and
  to the list-append loop (`for k in ("env", "setup-deps", "hooks")`). The
  duplicate-name check runs in `_read_hooks`, which sees the merged list, so
  duplicates across files are caught there with both locations.
  Variable substitution has already happened by then, because `${{...}}` is
  resolved before `read()` looks at the keys.

## Step 3 — Carry hooks to the handlers

- [x] `project_ops_info.ProjectUpdateInfo`: add a `hooks` field, a list that
  defaults to empty (use `dc.field(default_factory=list)` if the class is a
  dataclass).
- [x] `project_ops.update()`: pass `hooks=proj_info.hooks` in the existing
  `_apply_effective_settings(...)` call (about line 357). This is the root
  `proj_info` only.
- [x] Check every other path that builds a `ProjectUpdateInfo` for a root
  update, such as `cmd_install` (`is_root=True` at `cmd_install.py:122`).
  Decide whether `ivpm install` (toolchain mode) runs hooks. Recommendation:
  no. Toolchain mode has no project root (`project_root()` raises), so the
  runner skips when `update_info.install_mode` is not WORKSPACE.

## Step 4 — The python handler creates the venv for hooks

- [x] In `PackageHandlerPython.on_root_post_load`, extend the gate:
  ```python
  has_python_hooks = any(h.kind == "python" for h in (update_info.hooks or []))
  if not has_python_pkgs and not has_python_config and not has_python_hooks:
      return
  ```
- [x] Check that a project with no Python dependencies and one python hook
  still gets a venv with ivpm and setuptools in it. ivpm is injected from the
  site config. Note that setuptools is *not* guaranteed in a uv-created venv.
  The egg-info hook needs it, and today a project gets it only when
  `pyproject.toml` lists it in `[build-system] requires` and ivpm harvests
  that. Decide one of:
  - (a) the user lists `setuptools` in `deps`, and the docs say so; or
  - (b) leave it, because in the motivating case `[build-system] requires`
    already brings in setuptools.

  Recommendation: (a), documented in the how-to. ivpm should not guess what a
  hook imports.

## Step 5 — Runner: `src/ivpm/handlers/package_handler_hooks.py`

- [x] Add `class PackageHandlerHooks(PackageHandler)` with these class
  attributes:
  - `name = "hooks"`
  - `description = "Runs project hooks (hooks: in ivpm.yaml) at lifecycle events"`
  - `phase = HandlerPhase.FINALIZE`
  - `leaf_when = None`, `root_when = None`
  - `conditions_summary = "root: when the root ivpm.yaml declares hooks"`

  Also add `handler_info()`, with the CLI option listed (as the python
  handler does).
- [x] `on_root_post_load(update_info)`:
  1. Return early when any of these hold: `getattr(update_info.args,
     "no_hooks", False)`; no hooks have event `post-update`; toolchain mode.
     When hooks are skipped because of `--no-hooks`, call `note()` once to
     say how many were skipped.
  2. Find the interpreter at `os.path.join(update_info.deps_dir, "python")`.
     If the python handler resolved to SKIP, because of yaml `venv: false`,
     `--py-skip-install` or `skip_venv`:
     - if a venv already exists from an earlier run, use it;
     - otherwise stop with `HandlerFatalError` naming the hook and the setting
       ("hook 'egg-info' needs the project venv, but Python installation is
       disabled (with.python.venv: false)").

     Never fall back to `sys.executable`. To keep the reason text in one
     place, factor `_resolve_venv_mode` into a module-level function in
     `package_handler_python.py` that both handlers call.
  3. Build the environment: copy `os.environ`, set `VIRTUAL_ENV`, put
     `<venv>/bin` (or `Scripts` on Windows, the same check as
     `get_venv_python`) first on `PATH`, unset `PYTHONHOME`, and set
     `IVPM_PROJECT` and `IVPM_PACKAGES` the way `packages.envrc` defines
     them.
  4. For each hook, in declared order: `label = hook.label(i, "post-update")`;
     `with self.task_context(update_info, "hook.%d" % i, "hook: " + label) as task:`
     then call `run_installer([venv_python, "-c", hook.source],
     cwd=update_info.project_root(), env=env, task=task, quiet=<as for other
     handlers>)`.
  5. When the result is not ok, raise `HandlerFatalError` with "hook '<label>'
     (post-update) failed with exit code N", the YAML location from
     `hook.srcinfo`, and the output tail from `format_output_tail(result.lines)`.
     Stop at the first failure; later hooks do not run.
  6. Wrap the whole thing in `perf.span("hooks.post-update")` if the handler
     has access to perf, and each hook in its own span. Check how other
     handlers reach perf.
- [x] Register it in `pyproject.toml` under `[project.entry-points."ivpm.handlers"]`:
  `hooks = "ivpm.handlers.package_handler_hooks:PackageHandlerHooks"`. Check
  whether built-in handlers are also listed somewhere in
  `package_handler_rgy.py` for a source checkout with no installed entry
  points. If they are, add it there too.
- [x] Confirm the order: `FINALIZE` comes after `INSTALL` (python) and
  `INTEGRATE`. Check `test_handler_order.py` for a test that lists the
  built-in handlers and update it.

## Step 6 — CLI (`src/ivpm/__main__.py`)

- [x] Add `--no-hooks` (dest `no_hooks`, `store_true`) to `update_cmd`, with
  help text "Do not run hooks declared in ivpm.yaml".
- [x] Add the same flag to `clone_cmd`, because clone runs update.
- [x] If `test_cli_help.py` checks the flags or the help output, update it.

## Step 7 — Schema (`src/ivpm/share/ivpm.json`)

- [x] Add `defs/hook`: an object with `name` (string, pattern), `event`
  (string or array of string, enum `["post-update"]`) and `python` (string),
  with `required: ["event", "python"]` and `additionalProperties: false`.
  Give each field a `title` and `description`.
- [x] Add `"hooks": {"type": "array", "items": {"$ref": "#/defs/hook"}}` to
  `package-def`.

## Step 8 — Tests

### Unit: parsing (`test/unit/test_hooks.py`, new)

Parse with `IvpmYamlReader().read(io.StringIO(...), name, is_root=True)`, the
same way as `test_doc_surface._parse`.

- [x] A minimal hook: `event` is a string, `python` is a `|` block. Check
  `events == ["post-update"]`, `kind == "python"`, and the source is exact.
- [x] `event` as a list, `[post-update]`.
- [x] With a `name`, and without: the label falls back to `post-update hook #1`.
- [x] Dedent cases:
  - an indented quoted string `"  import os\n  print(1)"`;
  - a `|2` over-indented block;
  - leading and trailing blank lines;
  - relative indentation inside an `if` is kept.
- [x] Errors, one test each, checking the message and that the location
  names the right line:
  - an entry that is not a mapping;
  - a missing `event`;
  - an unknown event;
  - an empty event list;
  - no kind key;
  - an unknown key;
  - `python` that is not a string;
  - empty source;
  - inconsistent indentation;
  - a bad name;
  - a duplicate name.
- [x] Order is preserved across several hooks.
- [x] `is_root=False`: hooks, even malformed ones, are ignored and
  `proj_info.hooks == []`.
- [x] `${{var}}` inside the source is substituted, and `$${{` gives a literal
  `${{`.
- [x] A manifest with no `hooks` key parses exactly as before (`hooks == []`).

### Unit: include merge (in `test/unit/test_hooks.py`; `test_include.py`'s helper reads with `is_root=False`, which ignores hooks)

- [x] Hooks only in the included file are adopted.
- [x] Hooks in both files: local hooks first, then included ones.
- [x] The same name in both files: fatal, and the message names both
  locations.

### Unit: runner (`test/unit/test_hooks.py`)

Drive `PackageHandlerHooks.on_root_post_load` with a hand-built
`ProjectUpdateInfo` and a fake venv: a temporary directory with
`bin/python` symlinked to `sys.executable`. No real update is needed.

- [x] The hook runs with cwd equal to the project root, and `VIRTUAL_ENV` /
  `PATH` set. Check this with a hook that writes `os.getcwd()` and
  `os.environ` values to a file.
- [x] Two hooks run in declared order.
- [x] A hook that raises gives `HandlerFatalError`. The message has the label
  and exit code, and the second hook does not run.
- [x] `args.no_hooks = True`: nothing runs.
- [x] There is no venv and the mode is SKIP: `HandlerFatalError`, and the
  message names the setting.
- [x] Hooks with an event other than the current one are not run. This
  becomes meaningful once there is a second event; for now, assert that only
  `post-update` is selected.

### End-to-end (`test/unit/test_hooks_e2e.py`, new, based on `TestBase`)

- [x] **The egg-info case.** A root project with a setuptools
  `pyproject.toml`, a `src/demo/__init__.py`, `deps: [{name: setuptools,
  src: pypi}]`, and the egg-info hook. Run `ivpm_update()` and check that
  `src/demo.egg-info/PKG-INFO` exists. Mark it network-dependent in the same
  way the other venv tests are, by checking how existing e2e tests gate
  network access.
- [x] **No other Python.** A project whose only Python is a hook writing a
  marker file still gets a venv, and the hook runs.
- [x] **Failure.** A hook that runs `raise SystemExit(3)` makes
  `ivpm_update()` raise, and `package-lock.json` / `ivpm.json` are still
  written or not written, consistently. Check what the other FINALIZE
  failures do and match it.
- [x] **`--no-hooks`.** The marker file is not created.
- [x] **Sync does not run hooks.** Run update, delete the marker, run
  `ivpm_sync()`, and check the marker is still absent.

### Regression

- [x] Run the full suite (`make test`, output in a log file):
  - handler-order and CLI-help tests may list the handlers or flags;
  - `test_doc_surface` invariants: hooks are optional, and a manifest without
    them parses unchanged.

## Step 9 — Documentation

- [x] **New page `docs/source/hooks.rst`**, added to the toctree in `index.rst`
  next to `python_packages` / `workflows`. Sections:
  - What hooks are, and when they run. A table of events: `post-update` runs
    after `update` and `clone`, and not after `sync`.
  - Format: `name`, `event`, `python`, with the example.
  - How the code runs: the venv interpreter, `-c`, no shell, cwd is the
    project root, the environment variables, dedenting.
  - Order and failure: declared order, the first failure stops the update,
    and `--no-hooks`.
  - Only the root's hooks run, and why: dependencies' hooks are ignored for
    supply-chain safety, which is also Composer's rule.
  - `${{var}}` substitution inside source, and the `$${{` escape.
  - Includes: hooks append, and duplicate names are errors.
  - Recipe: "Generating `.egg-info` for the root project", which notes the
    `setuptools` dependency.
- [x] **`reference.rst`:**
  - add a "Hooks (`hooks`)" section rendering `#/defs/hook`, after
    "Submodules";
  - add `--no-hooks` to the `update` and `clone` option lists.
- [x] **`python_packages.rst`:** a short cross-reference from "Using the
  Virtual Environment" to the hooks recipe.
- [x] **`handlers.rst`:** list the built-in `hooks` handler and its phase,
  wherever the built-in handlers are listed.
- [x] **`docs/Changes.md`:** add a "**New: project hooks
  (`hooks:`).**" entry at the top, under the next version heading, in the
  existing style.
- [x] **The `ivpm` agent skill**
  (`src/ivpm/share/skills/...`, if it documents the ivpm.yaml keys): add `hooks`.
- [ ] Build the docs (`make html`) and check that the new jsonschema block
  renders. **Not done:** Sphinx is not installed in this venv. The four
  changed pages were checked with docutils (Sphinx-only directives stubbed
  out) and parse with no warnings.

## Order of work

0 → 1 → 2 (with its parsing tests) → 3 → 4 → 5 (with the runner tests) → 6 →
7 → end-to-end tests → 9 → the full regression run. Each step leaves the tree
passing its tests.

## Decisions (confirmed 2026-11-13)

1. Include order: local hooks before included ones, to match `env` and
   `setup-deps`. (The design doc now says this too.)
2. `ivpm install` (toolchain mode) does not run hooks.
3. setuptools is not added automatically. The how-to says to list it in
   `deps`.
4. When Python installation is disabled but a venv exists from an earlier
   run, hooks still run in it. With no venv, they are an error.
