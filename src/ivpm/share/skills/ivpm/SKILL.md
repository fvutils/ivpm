---
name: ivpm
description: IVPM (Integrated View Package Manager) is a project-local polyglot package manager that fetches dependencies from diverse sources (git, PyPI, HTTP archives, GitHub releases, local dirs) and assembles unified project views (a Python venv, FuseSoC map, agent skills, merged direnv). Use when the user is working with IVPM-enabled projects, needs to manage dependencies, or needs to work with ivpm.yaml files.
---

# IVPM Agent Skill

IVPM is a project-local package manager that excels at managing projects where
dependencies are co-developed. One `ivpm.yaml` file declares dependencies from
diverse sources; one `ivpm update` materializes them under `packages/` and
assembles unified *views* (a Python virtual environment, a FuseSoC library map,
an agent-skills directory, a merged direnv file).

## When to Use This Skill

Use this skill when:
- Working with projects that have an `ivpm.yaml` file
- Managing software dependencies (Python, Git, archives, GitHub releases)
- Setting up development environments
- Syncing or updating project dependencies
- Patching or caching dependencies
- Creating project snapshots

## Quick Reference

### Essential Commands

```bash
# Clone and setup a project (automatically runs 'update' after cloning)
ivpm clone <git-url>

# Update dependencies (only needed after manual git clone, or to refresh deps)
ivpm update

# Load the project environment (delegated to direnv). Authorize once:
direnv allow

# Run a command in the project environment
direnv exec . <command>

# Check status of Git dependencies
ivpm status

# Sync Git dependencies with upstream
ivpm sync
```

### Creating a New Project

```bash
mkdir my-project && cd my-project
ivpm init my-project -v 0.1.0
# Edit ivpm.yaml to add dependencies
ivpm update
```

## ivpm.yaml Configuration

The `ivpm.yaml` file defines project dependencies. Add `$schema` for IDE
autocompletion:

```yaml
$schema: https://fvutils.github.io/ivpm/ivpm.schema.json

package:
  name: my-project
  version: "0.1.0"
  default-dep-set: default-dev

  dep-sets:
    - name: default
      deps:
        # Runtime dependencies only
        - name: requests
          src: pypi

    - name: default-dev
      deps:
        # Runtime + development dependencies
        - name: requests
          src: pypi
        - name: pytest
          src: pypi
```

## Dependency Types

### PyPI Packages
```yaml
- name: numpy
  src: pypi
  version: ">=1.20"
```

### Git Repositories
```yaml
- name: my-library
  url: https://github.com/org/my-library.git
  branch: main    # or tag: v1.0, or commit: abc123
```

### Local Development (symlinked)
```yaml
- name: co-developed
  url: file:///path/to/local/project
  src: dir
```

### HTTP Archives
```yaml
- name: data-pack
  url: https://example.com/data.tar.gz
```

### GitHub Releases (platform-specific binaries)
```yaml
- name: tool
  url: https://github.com/org/tool
  src: gh-rls
  version: ">=1.0"
```

## Caching

Caching avoids re-fetching shared dependencies and saves disk. It is enabled by
pointing `IVPM_CACHE` at a cache directory; per dependency, the `cache:` flag
selects the mode.

```bash
export IVPM_CACHE=~/.cache/ivpm
ivpm cache init ~/.cache/ivpm     # one-time: create the cache directory
```

Per-dependency modes:

```yaml
- name: gtest
  url: https://github.com/google/googletest.git
  cache: true     # cached, read-only, symlinked into packages/, shared
```

- `cache: true` — stored in `$IVPM_CACHE`, symlinked **read-only** into
  `packages/`, shared across projects (git: shallow checkout at the resolved
  commit).
- `cache: false` — never cached. For **git** this is an **editable** clone (full
  history unless `depth:` is set) — same as omitting `cache`, just guaranteed
  not to consult the cache. For **archive** sources it downloads and unpacks
  **read-only**.
- *omitted* (default) — not cached; git produces a full **editable** clone (the
  common case for co-developed deps).

Cache management:

```bash
ivpm cache info               # packages, versions, sizes ($IVPM_CACHE or --cache-dir)
ivpm cache info --verbose     # also list individual versions
ivpm cache clean --days 30    # remove entries not modified in 30 days
ivpm update --no-cache        # disable the cache for a single run
```

## Patching Dependencies

Apply patch files to a **git** dependency without forking it. Patches are
applied deterministically and recorded, so re-running `ivpm update` is
idempotent.

```yaml
- name: somelib
  url: https://github.com/foo/somelib.git
  patches:
    - patches/fix.patch                 # string form (strip: 1, applied at root)
    - file: patches/feature.patch        # mapping form
      strip: 1
      directory: src                     # apply within a sub-directory
      tool: git                          # 'git' or 'patch' (default: auto-detect)
```

Notes:
- Patch file paths are resolved relative to the `ivpm.yaml` that declares them;
  keep patch files alongside the project.
- Patches apply in declaration order; the first that fails aborts the update.
- Cached patched deps get a base entry plus a `<base>+patch.<id>` variant.
- If you edit a patched checkout *and* change the declared patch set, `ivpm
  update` refuses to clobber your edits and stops with an error. To
  re-establish: remove the package directory and re-run `ivpm update`.
- Patching currently applies to **git** deps only (archive support is pending).

## Deps-Source (derivative workspaces)

A *deps-source* is a sibling workspace's `packages/` directory, consulted
*before* the cache and any remote fetch. The motivating use case is LLM
benchmarking and other derivative workspaces: pull a subset of packages from a
"golden" workspace instead of re-fetching them.

```bash
# Materialize matching packages from a parent workspace (symlink by default)
ivpm update --deps-source /shared/golden/packages

# Trust same-named dirs without lock verification; copy instead of symlink
ivpm update --deps-source /shared/golden/packages --trust-deps-source \
            --deps-source-mode=copy

# Environment-variable form (colon-separated, like PATH)
export IVPM_DEPS_SOURCE=/shared/golden/packages:/shared/baseline/packages
ivpm update
```

Git worktrees automatically reuse the main worktree's `packages/` as a
deps-source (disable with `--no-worktree-deps-source`). `ivpm status` marks such
packages `(deps-source)` or `(auto: worktree)`.

## Common Workflows

### Daily Development
```bash
cd my-project
direnv allow          # once; env then loads automatically on cd
# Work in the project environment
pytest
```

### Adding a Dependency
1. Edit `ivpm.yaml` to add the dependency
2. Run `ivpm update`
3. Verify with `direnv exec . python -c "import new_package"`

### Updating Git Dependencies
```bash
ivpm status          # Check for changes
ivpm sync            # Pull latest from upstream
```

### Switching Dependency Sets
```bash
ivpm update -d default      # Release dependencies
ivpm update -d default-dev  # Development dependencies
```

## Project Structure

After `ivpm update`, your project will have:
```
my-project/
├── ivpm.yaml           # Package configuration
├── packages/           # Dependencies directory
│   ├── python/         # Python virtual environment
│   ├── package-lock.json  # Resolved versions / commit hashes
│   ├── dependency-1/   # Git/source packages
│   └── ...
└── src/                # Your project source
```

## Key Concepts

- **Dependency Sets**: Named collections of dependencies (e.g., `default` for
  release, `default-dev` for development)
- **Package Types**: `python` (installed to venv) or `raw` (placed in packages/)
- **Source Types**: `git`, `pypi`, `http`, `file`, `dir`, `gh-rls`
- **Caching**: `cache: true` for read-only shared copies (see *Caching* above)
- **Patching**: `patches:` to apply patch files to git deps (see *Patching*)

## Tips

- Add `packages/` to `.gitignore`
- Use `ivpm update --force-py-install` to reinstall Python packages
- Use `ivpm update -a` for anonymous (HTTPS) Git clones
- Use `ivpm update -v` or `ivpm sync -v` for detailed transcript output in CI
  (non-TTY) environments
- Use `ivpm snapshot <dir>` to create reproducible archives

## Environment Variables

- `IVPM_CACHE`: Path to package cache directory (enables caching)
- `IVPM_DEPS_SOURCE`: Colon-separated parent `packages/` dirs to consult first
- `IVPM_PROJECT`: Automatically set to project root
- `IVPM_PACKAGES`: Automatically set to packages directory
- `GITHUB_TOKEN`: For higher GitHub API rate limits

## Introspecting Registered Extensions

Use `ivpm show` to discover what package sources, content types, and handlers
are available in the current IVPM installation (including any third-party
plugins).

```bash
# Show all three categories at once
ivpm show

# List registered package sources (where packages come from)
ivpm show source          # or: ivpm show src
ivpm show source git      # detailed view of one source

# List registered content types (what IVPM does with a package after fetching)
ivpm show type
ivpm show type python     # detailed view

# List registered handlers (post-fetch processing hooks)
ivpm show handler
ivpm show handler python  # detailed view

# Flags available on all show sub-commands:
ivpm show source --json       # machine-readable JSON output
ivpm show source --no-rich    # plain-text (no Rich formatting)

# Generate a JSON Schema for ivpm.yaml (useful for IDE integrations):
ivpm show --schema
```

The `--json` flag is especially useful for programmatic introspection:
```bash
ivpm show source --json | python3 -c "import sys,json; [print(s['name'], s['description']) for s in json.load(sys.stdin)]"
```

## Inspecting Project Dependencies

Use `ivpm show deps` to view the **resolved** dependency graph for the current
project.  It reads `packages/package-lock.json` (when present) and the
`ivpm.yaml` files of installed sub-packages.

```bash
# Flat table of all resolved packages (default) — each package appears once
ivpm show deps

# Hierarchical tree showing who depends on whom
ivpm show deps --tree

# Full detail for a single package
ivpm show deps pyyaml

# Plain-text output (no Rich formatting)
ivpm show deps --no-rich
ivpm show deps --tree --no-rich

# Machine-readable JSON (flat list)
ivpm show deps --json

# Machine-readable JSON (tree)
ivpm show deps --tree --json

# Inspect a different project or dep-set
ivpm show deps -p /path/to/project
ivpm show deps -d ci
```

Key concepts:
- **Specifier**: which package *first* declared a dependency (`root` = top-level
  project).  IVPM uses first-specifier-wins, so if both root and a sub-project
  declare the same package, only the root's version is installed and the
  sub-project's request is silently dropped.
- **Shadowed**: in tree view, a package that was already claimed by an ancestor
  appears as a shadowed (greyed) leaf.
- **lock_available**: when `packages/package-lock.json` exists, resolved
  versions and commit hashes are shown; otherwise only declared info is
  available.

Useful `jq` recipes:
```bash
# All dependency names
ivpm show deps --json | jq '.[].name'

# Transitive deps (not declared by root)
ivpm show deps --json | jq '[.[] | select(.specifier != "root") | .name]'

# Git deps with their commit hashes
ivpm show deps --json | jq '[.[] | select(.commit != null) | {name, commit}]'
```

## All Commands

| Command | Description |
|---------|-------------|
| `ivpm clone` | Clone a project and automatically run update |
| `ivpm init` | Create initial ivpm.yaml |
| `ivpm update` | Fetch dependencies and create venv |
| `direnv allow` | Load the project environment (IVPM delegates env to direnv) |
| `ivpm status` | Check status of Git dependencies |
| `ivpm sync` | Sync Git packages with upstream |
| `ivpm build` | Build Python packages with native extensions |
| `ivpm cache` | Manage package cache (`init`, `info`, `clean`) |
| `ivpm show` | Introspect registered sources, types, and handlers |
| `ivpm show deps` | View the resolved project dependency graph |
| `ivpm snapshot` | Create self-contained project copy |
| `ivpm share` | Get IVPM share directory path |
| `ivpm pkg-info` | Query package paths/libraries |
