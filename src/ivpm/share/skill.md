---
name: ivpm
description: IVPM (IP and Verification Package Manager) is a lightweight project-local package manager for managing software dependencies. Use when the user is working with IVPM-enabled projects, needs to manage dependencies, or needs to work with ivpm.yaml files.
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
- Use `ivpm snapshot <dir>` to create reproducible archives

## Environment Variables

- `IVPM_CACHE`: Path to package cache directory
- `IVPM_PROJECT`: Automatically set to project root
- `IVPM_PACKAGES`: Automatically set to packages directory
- `GITHUB_TOKEN`: For higher GitHub API rate limits

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
| `ivpm snapshot` | Create self-contained project copy |
| `ivpm share` | Get IVPM share directory path |
| `ivpm pkg-info` | Query package paths/libraries |
