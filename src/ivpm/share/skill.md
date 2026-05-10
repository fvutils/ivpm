---
name: ivpm
description: IVPM (Integrated View Package Manager) is a project-local polyglot package manager that fetches dependencies from diverse sources and assembles unified project views. Use when the user is working with IVPM-enabled projects, needs to manage dependencies, or needs to work with ivpm.yaml files.
---

# IVPM Agent Skill

IVPM is a project-local package manager that excels at managing projects where dependencies are co-developed.

## When to Use This Skill

Use this skill when:
- Working with projects that have an `ivpm.yaml` file
- Managing software dependencies (Python, Git, archives)
- Setting up development environments
- Syncing or updating project dependencies
- Creating project snapshots

## Quick Reference

### Essential Commands

```bash
# Clone and setup a project (automatically runs 'update' after cloning)
ivpm clone <git-url>

# Update dependencies (only needed after manual git clone, or to refresh deps)
ivpm update

# Activate the Python virtual environment
ivpm activate

# Run a command in the virtual environment
ivpm activate -c "<command>"

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

The `ivpm.yaml` file defines project dependencies. Add `$schema` for IDE autocompletion:

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

## Common Workflows

### Daily Development
```bash
cd my-project
ivpm activate
# Work in virtual environment
pytest
exit
```

### Adding a Dependency
1. Edit `ivpm.yaml` to add the dependency
2. Run `ivpm update`
3. Verify with `ivpm activate -c "python -c 'import new_package'"`

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
│   ├── dependency-1/   # Git/source packages
│   └── ...
└── src/                # Your project source
```

## Key Concepts

- **Dependency Sets**: Named collections of dependencies (e.g., `default` for release, `default-dev` for development)
- **Package Types**: `python` (installed to venv) or `raw` (placed in packages/)
- **Source Types**: `git`, `pypi`, `http`, `file`, `dir`, `gh-rls`
- **Caching**: Add `cache: true` to dependencies for read-only symlinked copies

## Tips

- Add `packages/` to `.gitignore`
- Use `ivpm update --force-py-install` to reinstall Python packages
- Use `ivpm update -a` for anonymous (HTTPS) Git clones
- Use `ivpm update -v` or `ivpm sync -v` for detailed transcript output in CI (non-TTY) environments
- Use `ivpm snapshot <dir>` to create reproducible archives

## Environment Variables

- `IVPM_CACHE`: Path to package cache directory
- `IVPM_PROJECT`: Automatically set to project root
- `IVPM_PACKAGES`: Automatically set to packages directory
- `GITHUB_TOKEN`: For higher GitHub API rate limits

## Introspecting Registered Extensions

Use `ivpm show` to discover what package sources, content types, and handlers are
available in the current IVPM installation (including any third-party plugins).

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
- **Specifier**: which package *first* declared a dependency (`root` = top-level project).  IVPM uses first-specifier-wins, so if both root and a sub-project declare the same package, only the root's version is installed and the sub-project's request is silently dropped.
- **Shadowed**: in tree view, a package that was already claimed by an ancestor appears as a shadowed (greyed) leaf.
- **lock_available**: when `packages/package-lock.json` exists, resolved versions and commit hashes are shown; otherwise only declared info is available.

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
| `ivpm activate` | Activate Python virtual environment |
| `ivpm status` | Check status of Git dependencies |
| `ivpm sync` | Sync Git packages with upstream |
| `ivpm build` | Build Python packages with native extensions |
| `ivpm cache` | Manage package cache |
| `ivpm show` | Introspect registered sources, types, and handlers |
| `ivpm show deps` | View the resolved project dependency graph |
| `ivpm snapshot` | Create self-contained project copy |
| `ivpm share` | Get IVPM share directory path |
| `ivpm pkg-info` | Query package paths/libraries |
