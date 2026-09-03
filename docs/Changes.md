
# 2.28.0
- Enhancements to Python/Node package-installation error reporting

# 2.26.0
- **The selected dep-set now survives a bare `ivpm update`, and is reported.**
  The dep-set was recovered only from `<deps-dir>/ivpm.json` — regenerated
  state that is not written when an update fails partway — so a workspace that
  lost it silently reverted to the manifest's default dep-set on the next bare
  `ivpm update`, rewriting the lock with whatever that default names. The
  resolved dep-set(s) are now also recorded in `package-lock.json` and
  recovered from there, and a run that adopts the recorded selection says so:
  `note: Using dep-set dev, recorded in .../package-lock.json`. Changing the
  selection still requires `-d <name>` *and* `--force`, as before.
- **`ivpm update` now applies a changed dependency spec.** Bumping `commit:`,
  `tag:`, `branch:` or `url:` in `ivpm.yaml` and re-running `ivpm update` used
  to leave the existing clone untouched: the change was reported as advisory
  drift and skipped. It is now re-materialized to match the manifest.
  - A dependency holding local work is never silently discarded. Uncommitted
    edits, unpushed commits, a local-only branch, a stash or patched-tree drift
    stop the update with the tree untouched, naming what is in the way; pass
    `--force` to discard it. This is the same safety gate `ivpm destroy` uses.
  - Previously the lock was also rewritten with the *new* requested commit
    beside the *old* resolved one — recording a checkout that never happened,
    which silenced the drift warning on every subsequent run.
  - Applies to cached and deps-source dependencies too. Those materialize as a
    symlink, and a symlinked package was previously not spec-checked at all, so
    a `cache: true` dependency stayed on whatever it first resolved to.
- `--refresh-all` and `--force` now work on `ivpm update`. Both were accepted by
  the CLI, forwarded through `ProjectOps.update`, and then never read, so
  neither re-fetched anything despite their help text. `ivpm install
  --refresh-all` was inert for the same reason and is also fixed.
- **A `commit:` pin now produces a detached HEAD, and `ivpm status` shows it.**
  Pinning was applied with `git reset --hard`, which moves the *branch* pointer
  and left the clone on an ordinary branch sitting behind its upstream —
  indistinguishable from a dependency that tracks that branch. `ivpm status`
  displayed the branch name; sync fast-forwarded off the pin. A pinned package
  now reports `pinned:<sha>` (which outranks a tag that happens to point at the
  same commit) and `upstream:—`, since a pin has no upstream to be ahead of.
  The destroy gate was already written for this — its comment reads *"a pinned
  commit/tag leaves a detached HEAD"* — so the clone step was the outlier.
  - The destroy/refresh gate now also catches commits made *while* detached.
    Those are reachable from nothing but HEAD and are destroyed by a re-clone,
    but `@{u}` does not resolve on a detached HEAD, so the ahead/behind check
    could not see them. Asked of the commit graph directly instead
    (`rev-list HEAD --not --remotes`).
- **`ivpm sync` no longer moves a commit-pinned dependency.** A `commit:` pin
  states which commit the dependency must be at; sync was fast-forwarding it to
  the branch tip anyway, silently contradicting the manifest. Such packages are
  now `SKIPPED` with reason `pinned to commit <sha>`, matching how tag pins are
  already handled. (The detached-HEAD guard did not cover this: `ivpm update`
  applies a commit pin with `git reset --hard`, which moves the branch pointer
  rather than detaching, so a pinned clone sits on an ordinary branch behind its
  upstream.) Sync also could not previously *see* the pin — it builds packages
  from lock entries, which spell it `commit_requested`, while only the manifest
  spelling `commit` was read.
- A `tag:` pin is now honored when cloning. `_clone_to_dir` passed `-b` for
  `branch:` and reset to `commit:`, but ignored `tag:` entirely, silently
  leaving the clone on the remote's default branch.
- **Errors now render once, at the bottom of the output.** While a live progress
  display owns the screen, error and fatal diagnostics are held back and
  rendered after it tears down, so the reason a command failed is the last
  thing on screen rather than the first thing that scrolled away.
  - A fetch failure was being reported twice in full — once by the code that
    hit it and again by the batch driver re-quoting it — so a single bad URL
    printed git's output and the auth hint two times over. The driver now adds
    only what the first report lacks (which dependency entry failed, and its
    `file:line`) and re-raises the original exception, so the reason is still
    carried in the exception text callers read.
  - `ivpm destroy` now defers errors like `update` and `sync` do; its live gate
    and teardown tables previously pushed any diagnostic off the top.
  - `ivpm sync` and `ivpm destroy` flush deferred errors themselves once their
    results table is on screen, instead of relying solely on the top-level
    handler — so a caller driving those commands directly can no longer lose
    the diagnostics.
- **Switching dep-sets is possible again.** A workspace records the dep-set it
  was installed with in `<deps-dir>/ivpm.json`, and asking for a different one
  failed with `Attempting to update with a different dep-set than previously
  used` — with no way past it, since `--force` was not consulted. `--force` now
  performs the switch (the same escape-hatch role it plays for the refresh
  safety errors), and the refusal names the requested set, the installed set,
  the deps-dir, and both ways forward.
- **A shrinking lock no longer orphans packages silently.** `package-lock.json`
  is what `ivpm status` reports from, so a package the lock stops naming
  becomes invisible even though its directory is still on disk. That happened
  with no diagnostic whenever the resolved dep-set shrank — most starkly when
  it resolved to *no* packages, which rewrote the lock to `packages: {}` and
  left a fully-populated deps-dir behind reporting `0 package(s)`. `ivpm
  update` now lists the packages it dropped from the lock but left on disk.
- Correct an editable-install ordering bug

# 2.25.0
- Add support for 'install' command that creates a standalone deps-dir
- `ivpm install --root-var NAME` names the variable `packages.envrc` exports for
  the tool directory (e.g. `TOOLS_ROOT`). `IVPM_PACKAGES` is still exported, as
  an alias of that name, so manifests referencing it keep resolving. The name is
  recorded in the lock and survives replay; `--root-var IVPM_PACKAGES` restores
  the bare default.
- Bug fixes in Python venv setup
- Update support for Node environments to support both build and tool installs

# 2.23.0
- Add support for keeping sub-dependencies nested

# 2.22.0
- Environment variables are now declared as a standard `with:` clause, available
  at both the package and dep-set level:
  `with: { env: [{name: FOO, value: bar}] }`. A dep-set's `env` directives are
  appended to (never replace) the package-level ones and are emitted last into
  `packages.envrc`, so they win for `value:`/`path:` while `path-prepend`/
  `path-append` accumulate. To vary the environment across dep-sets, factor the
  shared directives into a base dep-set and `uses:` it -- there is deliberately
  no way for a dep-set to clear or replace what it inherits.
  - `with.env` also list-appends across `include:`, matching the top-level
    `env:` rule. Previously a `with:` block's lists were local-wins, which would
    have silently discarded an include's environment directives.
  - Not to be confused with `with.node.env`, a boolean controlling whether the
    Node handler patches `packages.envrc`.
- The top-level `package: env:` key is **deprecated**. It still works -- it is
  folded into `with.env` -- but `ivpm update` now warns when the root manifest
  uses it; it will be removed in a future release. Dependency manifests do not
  warn, since their `env:` is not the user's to fix.

# 2.21.0
- IVPM now consumes [Agent Plugins 1.0](https://agent-plugins.org/specification),
  the vendor-neutral standard for packaging Agent Skills and MCP server
  configuration. Plugins shipped by a dependency are discovered (`plugin.json`
  at a package root, under `plugins/*/`, via `with.agents.plugins`, a dep
  entry, or the `agent.plugins` Python entry-point group), validated against
  the specification, and projected into the workspace.
  - A new `.agents/plugins/<name>` directory links each plugin whole.
  - Each tool receives a plugin in the form it can consume. Claude Code has a
    plugin mechanism, so it gets the plugin *installed* at
    `.claude/skills/<name>/` with a generated `.claude-plugin/plugin.json`;
    tools that understand only skills get each skill linked individually as
    `<plugin>-<skill>`. Codex needs no mirror at all -- it already scans
    `.agents/skills` up to the repository root.
  - No per-tool *plugin* directories are created: no client reads a
    project-local plugin-root directory.
  - MCP servers declared by a plugin are **opt-in** (`mcp: true` under
    `package.with.agents`, default false). Review what a dependency would wire
    up first with `ivpm show plugins --mcp`, which prints environment-variable
    names but never their values.
  - New `ivpm show plugins` command: list, detail, `--check` (a conformance
    gate that exits non-zero, usable in CI), and `--mcp`.
  - The existing loose-`SKILL.md` mechanism and the `agent.skills` entry-point
    group are unchanged and not deprecated.
  - See the new *Agent Plugins* documentation page.

# 2.20.0
- `ivpm clone` now explains a failed clone instead of just reporting a git
  exit code: the source and effective clone URL (including any `git-url-map`
  rewrite and where the rule came from), how the locator was interpreted
  (e.g. `abc:def` is scp-style SSH, not a path), the transport and why it was
  chosen, the exact git command, how the failure was detected, git's own
  output, and targeted hints.
- Updated how remapped URLs are displayed during update
- Ensure build dependencies for Python projects are installed prior to 
  package installation

# 2.19.0
- Dep-sets may now declare their own `with:` block to override package-level
  handler configuration (Python venv mode, Node manager, plugin-handler
  settings) for that set. When a dep-set is the selected install target, its
  `with:` refines the package-level one (dep-set wins per key; unset keys fall
  back). It inherits through `uses:` and merges left-to-right across a
  multi-dep-set selection. See the *Dependency Sets* docs.
- Git URL remapping via the config files. A new `git-url-map` key (in the user
  and site `config.yaml`, or the `IVPM_GIT_URL_MAP` env var) rewrites git URLs
  on the fly before auth/ssh resolution — e.g. redirect `https://github.com/ORG`
  to a `file:///repos/ORG` mirror. Patterns match by path element, support `*`
  (within a segment) and `**` (across segments) wildcards with `\1..\n` captures,
  and resolve most-specific-wins (by path depth). See the *Git Integration* docs.

# 2.18.0
- `ivpm clone` now supports **pluggable clone providers**. The source of the
  root workspace is extensible: a provider claims a URL by dedicated scheme
  (`myvcs://…`) or by pattern (`https://myserver/…`), with a dedicated scheme
  taking precedence and an ambiguous match reported as an error. Providers can
  declare their own command-line options (e.g.
  `ivpm clone myvcs://repo -branch abc -node xyz`). Register a provider via
  the `ivpm.clone_providers` entry-point group; see the *Clone Providers* docs.
  The existing git behavior is unchanged: git is the default provider and the
  fallback for generic URLs. The git-specific flags (`--ssh`, `--anonymous`,
  `--git-auth-order`) now belong to the git provider and are listed by
  `ivpm show clone-providers git`; they remain accepted on `ivpm clone` during a
  deprecation window. New: `ivpm clone --provider NAME` and
  `ivpm show clone-providers`.
- `ivpm status` now reports the **root project** in addition to its Git
  dependencies, described by the clone provider that produced the workspace.
  `ivpm clone` records the root's clone-provider type in the lock file (a new
  additive top-level `root` block, preserved across `ivpm update`); when the
  workspace was not created by `ivpm clone`, IVPM probes the installed providers
  to recognize the root on disk. The root line is omitted (never an error) when
  the type cannot be determined. Clone providers gain two optional hooks —
  `probe()` and `root_status()` — to participate; see the *Clone Providers* docs.

# 2.17.0
- Enhance performance-monitoring features. Runs now record stats to a file for later review
- Environment management is now fully delegated to `direnv`. The `ivpm activate`
  command has been removed: `ivpm update` generates `packages/packages.envrc`
  (now including `IVPM_PROJECT` alongside `IVPM_PACKAGES`), which `direnv` loads.
  The `env:` directive is retained but re-sinked — its `value`/`path`/
  `path-append`/`path-prepend` actions are emitted as `direnv` directives into
  `packages.envrc` (project directives last, so they win over packages) rather
  than applied by `ivpm activate`. The never-implemented `env-sets` key is
  removed. The Windows `.bat`/`.ps1` activation scripts are removed; the
  supported Windows setup is `direnv` + git-bash (`direnv hook pwsh` for native
  PowerShell).

# 2.16.0
- New `ivpm destroy` command: tears down a workspace (root + imports) or, with
  `--deps-only`, just the imports — the inverse of `clone`/`update`. A delegated
  safety gate refuses to remove imports holding unrecoverable local work
  (modified/untracked/unpushed/stash/local-only branch/patched-tree drift) and
  reports every blocker; `--force` overrides, `--dry-run` previews, `-v` lists
  the evidence. Teardown is source-specific: cache-backed and deps-source
  symlinks are unlinked (never recursed into), and handlers remove their derived
  artifacts (venv, `node_modules`) via a new `on_destroy()` hook. Refuses to
  destroy a non-workspace or the current directory/an ancestor. Both the gate and
  the teardown run in parallel (`-j/--jobs`, default CPU count) with a live Rich
  progress display (or `--no-rich` plain-text fallback).
- Support for patching imported packaged
- Support recursive ivpm.yaml processing in .tar and gh-rls packages
- `ivpm cache clean` now prunes by *last use* instead of *first cached*. Each
  cache entry gets a sidecar (`<version>.meta.json`) recording `stored` and
  `last_linked`; `last_linked` is refreshed whenever a version is symlinked
  into a workspace (including re-runs of `ivpm update` on an already-linked
  dep), so a version shared by live workspaces is no longer evicted by age
  alone. Pre-existing (sidecar-less) entries fall back to directory mtime.
  Adds `ivpm cache clean --dry-run`; `ivpm cache info --verbose` now shows
  `stored`/`last linked`.

# 2.14.0
- Extended support for remote manifest support
- A `src: ivpm.yaml` dep-set factory's `dep-set:` may now name a list of
  dep-sets to merge (e.g. `dep-set: [core, extras, dev]`). Later-listed
  dep-sets override earlier ones on package-name collision, and each leaf
  records the specific dep-set it came from.

# 2.13.1
- Added a new extension point for applying site configs

# 2.13.0
- Internal: the site configuration now returns a *cache provider* for the
  session (`SiteConfig.get_cache_provider`) instead of a bare cache location.
  A disabled cache is a real null provider, and one provider serves every
  dependency. The `Cache` class is renamed to `DirectoryCacheStore`, with a
  deprecated `Cache` alias kept for one release. No change to user-facing cache
  modes (`cache: true/false`/unspecified), the on-disk cache layout, or
  `IVPM_CACHE` semantics. Site configs may override `get_cache_provider` for
  per-dependency routing; overriding only `get_default_cache_dir()` keeps
  working unchanged.
- Added support for a remote ivpm.yaml file
- Updated the extension mechanism to allow providers to report version/provenance


# 2.11.0
- Correct error-handling issue leading to silent exit of '1'

# 2.10.0
- Add support for multi-file ivpm.yaml descriptions
- Add support for git worktrees

# 2.9.0
- Add 'show deps' sub-command

# Unreleased
- Rename: IVPM now stands for "Integrated View Package Manager"
  (previously "IP and Verification Package Manager"). The `ivpm`
  command, package name, config filenames, and all import paths are
  unchanged.

# 2.8.0
- Add FuseSoc integration

# 2.7.0
- Change cache to default to 'on'
- Only create Python venv if Python packages are present
- Provide a hook to control site-specific ivpm install and cachec location

# 2.6.0
- Add direnv support: IVPM now generates a `packages.envrc` file at the
  project root after `update`. It contains `source_env` entries for every
  sub-package that provides an `export.envrc` or `.envrc` file, ordered by
  dependency (leaves first). `export.envrc` is preferred over `.envrc` when
  both exist.

# 2.5.0
- Change `update` behavior to use the first dep-set by default
  instead of searching for 'default-dev'.

# 2.4.0
- Add parallel fetch for packages

# 2.3.0
- Add support for package cache

# 2.2.0
- Add 'clone' command that automates setting up a workspace

# 2.1.0
- Have `uv` use non-isolated builds for Python packages. This enables source 
  binary package builds to find headers in other editable packages.

- Pull-through dep-set of super, vs defaulting to 'default' for sub-deps
- Store meta-data about packages in the packages directory (check/default dep-set)
  - Should have a 'default dep-set' setting
- Each package should be able to report out its full configuration post-update / post-sync
- Each package should be called on a 'snapshot' operation to clean up irrelevant data (eg .git)
- Should be able to specify manifest file via -f (-f last_tapeout.ivpm.yaml)
- Only reinstall Python packages if a change is detected (?)
- Replace git-update with sync
- Replace git-status with status
