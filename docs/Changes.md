
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
