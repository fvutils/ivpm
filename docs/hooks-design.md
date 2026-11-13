# Project hooks (run Python after update) — review and design

Status: design agreed so far: `hooks:` is a list of entries, each with an
optional `name`, an `event` (the first one is `post-update`) and a `python:` body holding Python
source that is left-justified before it runs. Implemented; see
[`hooks-plan.md`](hooks-plan.md) for status.

## Goal

Let a project's `ivpm.yaml` name Python code that ivpm runs at fixed points
in its own lifecycle. The first case is to create the root project's
`.egg-info` once `ivpm update` finishes. Today that is done by hand:

    packages/python/bin/python -c 'from setuptools import setup; setup()' egg_info -e src

The code must run with the project's venv interpreter, called directly, with
no shell in between.

## What exists today

There is no way to do this now. What I found:

| Area | What exists | Can it run a user task? |
|---|---|---|
| `ivpm.yaml` reader (`src/ivpm/ivpm_yaml_reader.py`) | Reads `name`, `with`, `env`, `deps`/`dep-sets`, `setup-deps`, `paths`, `submodules`, `include`, … | No. There is no `events`, `tasks`, `scripts` or `hooks` key. |
| Handler lifecycle (`handlers/package_handler.py`) | `on_root_pre_load`, `on_leaf_post_load`, `on_root_post_load`, `build`; phases `prepare → environment → install → integrate → finalize` (`handlers/handler_phases.py`) | Only for handler code that is installed as a Python entry point (`ivpm.handlers`). There is no hook a project's YAML can declare. |
| `UpdateEvent` / `UpdateEventDispatcher` (`update_event.py`) | Progress events sent to the TUI | No. These are notifications for the UI, not actions. |
| Python handler | Creates `packages/python` and installs the deps | It never installs the root project, so no `.egg-info` appears for it. That is why you need this task. |
| `utils.get_venv_python(python_dir)` | Finds `bin/python` or `Scripts/python` | Can be reused as is. |
| `installer_run.run_installer(cmd, env, cwd, task, quiet)` | Runs a command from an argv list with no shell. It keeps all output even when quiet and reports progress to the TUI | Can be reused as is. |

So the building blocks are there (finding the venv python, running a command
safely, phase ordering, TUI tasks). What is missing is the manifest key, the
model that holds it, and the code that runs it.

## Naming: what other tools call this

| Tool | Container key | Lifecycle names | Runs dependencies' entries? |
|---|---|---|---|
| Composer (PHP), `composer.json` | `scripts` | `post-install-cmd`, `post-update-cmd`, … (the docs call these "events") | No. Only the root package's scripts run. |
| npm, `package.json` | `scripts` | `preinstall`, `postinstall`, `prepare` | Yes. This is a long-running supply-chain problem, and pnpm 10 now blocks them by default. |
| PDM, `[tool.pdm.scripts]` | `scripts` (the docs call them "hook scripts") | `post_install`, `pre_lock`, `post_lock`, … | No |
| Hatch, `[tool.hatch.envs.*]` | `pre-install-commands`, `post-install-commands` | (the event is in the key name) | No |
| devcontainer.json | `postCreateCommand`, `postStartCommand` | (the event is in the key name) | n/a |
| Git, Bower | `hooks` | `post-checkout`, `post-merge` / `postinstall` | n/a |
| pixi, just, cargo-make | `tasks` | none. Tasks are run by name, not at lifecycle points. | n/a |
| GitHub Actions | `on:` | the trigger is the event; the work is in `jobs` | n/a |

What this shows:

- **`scripts`** is the most common choice among package managers (npm,
  Composer, PDM). In each of them, the same table also holds named scripts the
  user runs by hand (`npm run x`, `composer x`, `pdm run x`).
- **`hooks`** clearly means "runs automatically at a point in the lifecycle".
  It stays distinct from named tasks a user runs.
- **`events`** usually names the *trigger* (Composer's docs, GitHub Actions'
  `on:`), not the container holding the commands.
- **`tasks`** means work the user runs by name. It is the wrong word for this.

## Design

```yaml
package:
  hooks:
    - name: egg-info
      event: post-update
      python: |
        from setuptools import setup
        setup(script_args=["egg_info", "-e", "src"])
```

- **`hooks:` is a list.** Each entry names its `event` and its body. This is
  the inverse of keying `hooks` by event name. It matches the rest of
  ivpm.yaml (`deps` and `dep-sets` are lists of dicts) and pre-commit's
  `hooks:` list with `stages:`. What it gains:
  - one hook can serve several events (`event: [post-update, post-sync]`,
    once there is more than one event);
  - per-hook fields (`name` now, and later `if` or `description`) have an
    obvious place to go;
  - hooks run in the order they are written, and merging `include:`d files is
    just appending lists.

  The cost is that every entry repeats its event, which is small.
- **The selector key is `event`, not `type` or `on`.**
  - `type:` already means something else in ivpm.yaml (a package's content
    type, on deps and `package`). It would also be read as the hook's kind
    (python vs. something else).
  - `on:` is what GitHub Actions uses, but PyYAML, which ivpm uses, reads YAML
    1.1, where a bare `on` key loads as the boolean `True`. Checked:
    `yaml.safe_load('on: x')` gives `{True: 'x'}`.
- **`name` is optional.** It is a label for people: it is the TUI task title,
  and it names the hook in logs and in failure messages ("hook 'egg-info'
  (post-update) failed: ..."). A hook without a name shows as
  `<event> hook #<n>`, where `n` is its 1-based position among that event's
  hooks. A name must be unique across the merged list, including hooks pulled
  in by `include:`. A duplicate is a parse error that gives both locations.
  Unique names let a later `ivpm update --skip-hook <name>` or
  `ivpm hook run <name>` refer to one hook without changing the schema.
- **`event` is a string or a list of strings.** **`post-update`** is the only
  event for now. It runs once `ivpm update` (and so also `ivpm clone`) has
  finished installing everything.
- **`python: <string>` is Python source code, not a command line.** ivpm runs
  it as `[<venv>/bin/python, "-c", <source>]` through `run_installer`, with no
  shell. The interpreter is always `get_venv_python(<deps-dir>/python)`.
- **Left-justify the source before running it.** Python rejects code whose
  first line is indented (`IndentationError: unexpected indent`). A YAML `|`
  block already removes its own indentation, but code also arrives as quoted
  strings, as `|2`-style blocks with extra indentation, from included files,
  or with leading blank lines. So the parser normalizes the source with
  `textwrap.dedent(source).strip("\n")` and stores that. `dedent` removes only
  the whitespace prefix that every line shares, so relative indentation (the
  body of an `if` or `def`) is kept. If the result still starts with
  whitespace, the lines' indentation does not match up. Report that at parse
  time with the YAML location, not as a Python traceback during the update.
  Add unit tests for an indented quoted string, an over-indented block, and
  leading blank lines.
- **The working directory is the project root.** The egg-info example needs
  this, because `setup()` reads `pyproject.toml` from the current directory.
- **Only the root manifest's hooks run.** Hooks in a dependency's `ivpm.yaml`
  are ignored, with a debug message, so fetching a dependency never runs code
  someone else chose. Composer works the same way.
- **Each entry has `event`, an optional `name`, and exactly one kind key.** Only `python` exists
  for now. A missing `event`, no kind key or two kind keys, or an unknown key
  is an error that points at the YAML line (`_keyloc`).
- **Keep it minimal for now.** There is no `cwd`, `env`, `ignore-errors`,
  `description` or file form. Add them only when a real use
  needs them. A later file form could be `python: {file: scripts/x.py}`.
- **Variable substitution:** the reader expands `${{name}}` in every string,
  including Python source (`variables.py:30-32`). An undeclared name is fatal.
  Python code rarely contains `${{`. If it needs a literal `${{`, write `$${{`.

### Running egg_info from Python

`setuptools.setup()` accepts `script_args`, which replaces the command line.
So `egg_info` needs no `sys.argv` tricks:

```python
from setuptools import setup
setup(script_args=["egg_info", "-e", "src"])
```

Checked in this repo's venv (setuptools 84.2.1) against a small
`pyproject.toml` project with a `src/` layout. It exited with 0, printed no
deprecation warnings, and created `src/demo.egg-info`.

## What it would take

1. **Model** (`proj_info.py`). Add a `HookSpec` dataclass (`name: Optional[str]`,
   `events: List[str]`, `kind`, `source`, and the YAML location for errors) and `ProjInfo.hooks: List[HookSpec]`, kept in the order
   they were declared. The runner picks out
   `[h for h in hooks if "post-update" in h.events]`.
2. **Parser** (`ivpm_yaml_reader.py`, next to the `setup-deps` code around
   line 486). Parse `hooks` only when `is_root`. Check event names (only
   `post-update` for now), check the entry shape, and require `python` to be a
   string. Report errors with `_keyloc` locations.
3. **Include merge** (`ivpm_yaml_reader.py` around 630–680). Join the lists:
   the local file's hooks come first and the included file's hooks follow,
   the same way `env` and `setup-deps` merge.
4. **Runner.** Add a new built-in handler,
   `handlers/package_handler_hooks.py`, with `phase = FINALIZE`, registered
   in `pyproject.toml` under `ivpm.handlers`. In `on_root_post_load` it runs
   each hook in order inside `self.task_context(...)` using
   `run_installer([venv_python, "-c", source], cwd=<project root>, env=...,
   task=..., quiet=...)`. Making it a handler means the barrier between phases
   guarantees the venv is fully installed before it runs. It also gets the
   same failure handling and output capture as the other handlers
   (`HandlerFatalError`, `format_output_tail`). A hook that fails fails the
   update.
5. **Environment.** Start from `os.environ`. Set `VIRTUAL_ENV`, put
   `<venv>/bin` first on `PATH`, and set `IVPM_PROJECT` and `IVPM_PACKAGES`.
6. **Edge cases.**
   - `with: python: venv: false` (`VenvMode.SKIP`) and a `python:` hook is
     declared: stop with an error. Do not fall back to the system python.
   - The install failed: the update already failed in the install phase, so
     the hook never runs.
   - `ivpm sync` does not run root post-load handlers, so `post-update` does
     not run on sync. Add a separate `post-sync` event later if it is needed.
7. **CLI.** Add `ivpm update --no-hooks`.
8. **Tests.** Unit tests for parsing: a valid hook, a non-string `python`, an
   unknown kind, an unknown event, a missing `event`, `event` given as a
   list, a hook with and one without a name, a duplicate name (including one
   that comes in through `include:`), and hooks in a dependency being ignored.
   An end-to-end test where a setuptools root project ends up with
   `src/<name>.egg-info` after `ivpm update`, and where a hook that raises
   fails the update.
9. **Docs.** A schema entry in `docs/source/reference.rst`, a how-to in
   `python_packages.rst` ("Generating egg-info for the root project"), and an
   entry in `docs/Changes.md`.

## Open questions

- **Run on every update, or only when needed?** `egg_info` is cheap, so the
  plan is to run it on every update for now.
- **A different fix for the motivating case:** a
  `with: python: install-self: true` setting (`pip install -e . --no-deps`)
  would produce the metadata with no hook at all.
