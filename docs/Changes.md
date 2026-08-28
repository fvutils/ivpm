
# 2.24.0
- `ivpm install --root-var NAME` names the variable `packages.envrc` exports for
  the tool directory (e.g. `TOOLS_ROOT`). `IVPM_PACKAGES` is still exported, as
  an alias of that name, so manifests referencing it keep resolving. The name is
  recorded in the lock and survives replay; `--root-var IVPM_PACKAGES` restores
  the bare default.
- Bug fixes in Python venv setup

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
