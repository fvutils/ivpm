#########
Reference
#########

Commands
========

.. argparse::
    :module: ivpm.__main__
    :func: get_parser
    :prog: ivpm


Command Details
===============

Activating the environment (direnv)
-----------------------------------

IVPM no longer provides an ``ivpm activate`` command.  Environment
activation is delegated to `direnv <https://direnv.net>`_: ``ivpm update``
generates ``packages/packages.envrc`` (Python venv ``PATH``, Node paths,
FuseSoC config, per-package ``export.envrc`` fragments, and any ``env:``
directives), which ``direnv`` loads.

**Examples:**

.. code-block:: bash

    # Authorize the generated envrc once; the environment then loads
    # automatically whenever you cd into the project
    $ direnv allow

    # Run a single command in the project environment
    $ direnv exec . python script.py
    $ direnv exec . pytest

**Windows:** ``direnv`` evaluates ``packages.envrc`` with bash, so a bash is
required even under PowerShell.  Use ``direnv`` + git-bash (Git-for-Windows);
for native PowerShell add ``Invoke-Expression "$(direnv hook pwsh)"`` to your
``$PROFILE``.

See :doc:`environment_paths` for the ``env:`` directive and generated
``packages.envrc``.

build
-----

Build Python packages with native extensions.

**Synopsis:**

.. code-block:: text

    ivpm build [-d <dep-set>] [-g|--debug]

**Options:**

``-d, --dep-set <name>``
    Use dependencies from specified dep-set (default: project's default)

``-g, --debug``
    Enable debug symbols in native extensions

**Examples:**

.. code-block:: bash

    # Build all packages
    $ ivpm build
    
    # Debug build
    $ ivpm build --debug
    
    # Specific dependency set
    $ ivpm build -d default-dev

**Behavior:**

- Finds all Python packages with native extensions
- Runs ``python setup.py build_ext``
- Installs built extensions
- Debug mode: sets ``DEBUG=1``, adds ``-g`` flag

cache
-----

Manage the IVPM package cache.

**Synopsis:**

.. code-block:: text

    ivpm cache <subcommand> [options]

**Subcommands:**

``init``
    Initialize a new cache directory

``info``
    Show cache statistics and contents

``clean``
    Remove old cache entries

cache init
~~~~~~~~~~

Initialize a cache directory.

**Synopsis:**

.. code-block:: text

    ivpm cache init [-s|--shared] [-f|--force] <cache_dir>

**Options:**

``-s, --shared``
    Set group inheritance (``chmod g+s``) for shared access

``-f, --force``
    Reinitialize existing directory

**Examples:**

.. code-block:: bash

    # Personal cache
    $ ivpm cache init ~/.cache/ivpm
    
    # Shared team cache
    $ sudo ivpm cache init --shared /shared/ivpm-cache
    $ sudo chown :devteam /shared/ivpm-cache

cache info
~~~~~~~~~~

Display cache information.

**Synopsis:**

.. code-block:: text

    ivpm cache info [-c|--cache-dir <dir>] [-v|--verbose]

**Options:**

``-c, --cache-dir <dir>``
    Cache directory (default: ``$IVPM_CACHE``)

``-v, --verbose``
    Show detailed version information

**Examples:**

.. code-block:: bash

    $ ivpm cache info
    $ ivpm cache info --verbose
    $ ivpm cache info --cache-dir /path/to/cache

cache clean
~~~~~~~~~~~

Remove old cache entries.

**Synopsis:**

.. code-block:: text

    ivpm cache clean [-c|--cache-dir <dir>] [-d|--days <n>]

**Options:**

``-c, --cache-dir <dir>``
    Cache directory (default: ``$IVPM_CACHE``)

``-d, --days <n>``
    Remove entries older than N days (default: 7)

**Examples:**

.. code-block:: bash

    $ ivpm cache clean
    $ ivpm cache clean --days 30
    $ ivpm cache clean --cache-dir /shared/cache --days 14

clone
-----

Create a new workspace from a Git repository.

**Synopsis:**

.. code-block:: text

    ivpm clone [options] <src> [workspace_dir]

**Arguments:**

``src``
    Git URL or local path to clone

``workspace_dir``
    Target directory (default: basename of src)

**Options:**

``--here``
    Set up the workspace in the current directory instead of a new
    subdirectory. Cannot be combined with an explicit ``workspace_dir``.
    This option is idempotent: if the current directory already contains a
    clone of ``src`` it is reused in place; if it is non-empty but not yet a
    repository, ``src`` is cloned into it (via ``git init``/``fetch``/
    ``checkout``); if it is empty, a plain clone is performed. If the
    directory already holds a git repository for a *different* source, the
    command fails rather than overwriting it.

``--ssh``
    Force SSH: rewrite an ``https://`` URL to ``git@host:path`` form

``-a, --anonymous``
    Force HTTPS: clone the URL as written (do not rewrite to SSH)

``--git-auth-order <list>``
    Comma-separated git auth order to try (``gh,ssh,https``); overrides
    ``IVPM_GIT_AUTH_ORDER`` and the config files for this invocation.
    See :doc:`git_integration` for how the transport is selected.

``-b, --branch <name>``
    Checkout branch; create if doesn't exist

``-d, --dep-set <name>``
    Dependency set for ``ivpm update``

``--py-uv``
    Use 'uv' for Python package management

``--py-pip``
    Use 'pip' for Python package management

``--py-system-site-packages``
    Inherit the base Python's system site-packages inside the created virtual
    environment.  By default the environment is **isolated** (system packages
    are not visible).  Pass this flag only when you intentionally need access
    to system-installed packages (e.g. hardware-specific Python bindings that
    cannot be installed via pip).

**Examples:**

.. code-block:: bash

    # Basic clone
    $ ivpm clone https://github.com/org/project.git
    
    # Custom directory
    $ ivpm clone https://github.com/org/project.git my-workspace

    # Set up the workspace in the current directory (idempotent)
    $ ivpm clone --here https://github.com/org/project.git

    # Specific branch
    $ ivpm clone -b develop https://github.com/org/project.git
    
    # Force HTTPS (as-written) clone with dep-set
    $ ivpm clone -a -d default https://github.com/org/project.git
    
    # Use uv for package management
    $ ivpm clone --py-uv https://github.com/org/project.git

**Behavior:**

1. Clones Git repository (or, with ``--here``, populates the current
   directory in place, reusing an existing clone if present)
2. Enters directory
3. Automatically runs ``ivpm update`` with specified options

Note: Since ``clone`` automatically runs ``update``, you don't need to run
``ivpm update`` separately after ``ivpm clone``.

destroy
-------

Remove a root project and all its imports (the inverse of ``clone``), or just
the imports (the inverse of ``update``). Gated against losing local work. See
:doc:`destroy` for the full guide.

**Synopsis:**

.. code-block:: text

    ivpm destroy [options] [wsdir]

**Arguments:**

``wsdir``
    Workspace directory to remove (required for a full destroy; ignored with
    ``--deps-only``).

**Options:**

``--deps-only``
    Remove only the imports/venv; keep the root project and ``ivpm.yaml``. Runs
    in place; no ``wsdir`` required.

``-p, --project-dir <dir>``
    Workspace root for ``--deps-only`` mode (default: current directory).

``-n, --dry-run``
    Report what would be removed and the gate verdict; change nothing.

``-f, --force``
    Delete even when packages hold local modifications or unpushed commits.

``-y, --yes``
    Skip the interactive confirmation prompt (required in non-interactive/CI
    contexts).

``-j, --jobs <n>``
    Number of parallel gate/teardown operations (default: CPU count).

``--no-rich``
    Plain-text output without the Rich live display.

``-v, --verbose``
    List the blocking files/commits in the report.

**Examples:**

.. code-block:: bash

    # Reset the current workspace's dependencies (keep the root)
    $ ivpm destroy --deps-only

    # Preview a full teardown
    $ ivpm destroy -n ../scratch-workspace

    # Remove an entire cloned workspace, no prompt
    $ ivpm destroy -y ../scratch-workspace

**Behavior:**

1. Validates the target is an IVPM workspace (refuses otherwise)
2. Gates every import (and, in full mode, the root) — refuses if any holds
   unrecoverable local work, unless ``--force``
3. Removes each import via its source provider (unlinking cache/symlink deps),
   then the venv, lock/state, and — in full mode — the deps directory and root

init
----

Create a new ``ivpm.yaml`` file.

**Synopsis:**

.. code-block:: text

    ivpm init [-v|--version <ver>] [-f|--force] <name>

**Arguments:**

``name``
    Package name

**Options:**

``-v, --version <ver>``
    Initial version (default: 0.0.1)

``-f, --force``
    Overwrite existing ``ivpm.yaml``

**Examples:**

.. code-block:: bash

    $ ivpm init my-project
    $ ivpm init my-project -v 1.0.0
    $ ivpm init my-project -f  # Overwrite existing

**Output:**

Creates ``ivpm.yaml`` with:

.. code-block:: yaml

    package:
      name: my-project
      version: "0.0.1"

perf
----

Inspect persisted performance records written by ``ivpm update`` (under
``deps/.ivpm/perf-<runid>.json``).  See :doc:`performance`.

**Synopsis:**

.. code-block:: text

    ivpm perf list [-p <project-dir>]
    ivpm perf show [runid] [--compact] [-p <project-dir>]
    ivpm perf export [runid] --format chrome [-o <file>] [-p <project-dir>]
    ivpm perf diff <A> <B> [-p <project-dir>]

**Subcommands:**

``list``
    List available records, newest first (runid, wall-clock, package count).

``show [runid]``
    Print the four-panel breakdown (long pole, hot spots, packages, waterfall)
    for a record; defaults to the newest.  ``--compact`` omits the waterfall.

``export [runid] --format chrome [-o <file>]``
    Emit Chrome Trace Event JSON for the record; load it in
    https://ui.perfetto.dev or ``chrome://tracing``.  Writes to stdout unless
    ``-o`` is given.

``diff <A> <B>``
    Compare two records (runids or ``perf-*.json`` paths): per-category
    self-time deltas, new/vanished phases, and the total wall-clock change.

**Examples:**

.. code-block:: bash

    # Show the most recent update's breakdown
    $ ivpm perf show

    # Open the timeline in Perfetto
    $ ivpm perf export -o trace.json

    # Compare the two most recent runs
    $ ivpm perf diff $(ivpm perf list | awk 'NR==3{print $1}') \
                     $(ivpm perf list | awk 'NR==2{print $1}')

pkg-info
--------

Query package information (paths, libraries, flags).

**Synopsis:**

.. code-block:: text

    ivpm pkg-info <type> [-k <kind>] <packages...>

**Arguments:**

``type``
    Information type: ``incdirs``, ``paths``, ``libdirs``, ``libs``, ``flags``

``packages``
    Package names to query

**Options:**

``-k, --kind <kind>``
    Qualifier for query type

**Examples:**

.. code-block:: bash

    # Get include directories
    $ ivpm pkg-info incdirs my-package
    
    # Get paths by kind
    $ ivpm pkg-info paths -k rtl my-package
    
    # Get library directories
    $ ivpm pkg-info libdirs package1 package2

**Use case:** Integration with build systems (CMake, Make, etc.)

share
-----

Return the IVPM share directory path.

**Synopsis:**

.. code-block:: text

    ivpm share [path ...]

**Arguments:**

``path``
    Optional sub-path within share directory

**Examples:**

.. code-block:: bash

    # Get share directory
    $ ivpm share
    /path/to/ivpm/share
    
    # Get CMake scripts path
    $ ivpm share cmake
    /path/to/ivpm/share/cmake

**Use case:** Integration with build systems needing IVPM files.

show
----

Inspect registered package sources, content types, and handlers — and view
the current project's resolved dependency graph.

**Synopsis:**

.. code-block:: text

    ivpm show [--json] [--no-rich] [--schema]
    ivpm show source  [--json] [--no-rich] [<name>]
    ivpm show src     [--json] [--no-rich] [<name>]   # alias for source
    ivpm show type    [--json] [--no-rich] [<name>]
    ivpm show handler [--json] [--no-rich] [<name>]
    ivpm show site-config [--json] [--no-rich] [<name>]
    ivpm show config      [--json] [--no-rich] [<name>]   # alias for site-config
    ivpm show deps    [-p DIR] [-d DEP-SET] [--tree] [--json] [--no-rich] [<name>]

**Sub-commands:**

``source`` / ``src`` *[name]*
    List all registered package source types, or show full details for a
    specific source (e.g. ``ivpm show source git``).

``type`` *[name]*
    List all registered content types (``python``, ``raw``), or show full
    details for a specific type.

``handler`` *[name]*
    List all registered package handlers, or show full details for a specific
    handler including activation conditions and CLI options.

``site-config`` / ``config`` *[name]*
    List all registered site configurations (the active one is flagged) and the
    effective settings the active config applies -- resolved cache directory,
    ``ivpm`` install arguments, git auth order, and the config files that were
    loaded. With *name*, show the detail for one config. See
    :doc:`extending_ivpm` for how to contribute a site config and how the active
    one is selected.

``deps`` *[name]*
    Show the resolved dependency graph for the current project (or the project
    at ``-p DIR``).  Without *name*, all packages are listed as a flat table
    (default) or tree (``--tree``). With *name*, shows full detail for a single
    package.  See :doc:`show_deps` for a detailed how-to guide.

**Options (show deps):**

``-p DIR`` / ``--project-dir DIR``
    Project root to inspect (default: current working directory).

``-d DEP-SET`` / ``--dep-set DEP-SET``
    Dep-set to load from the root ``ivpm.yaml`` (default: ``default-dev``).

``--tree`` / ``-t``
    Show the full dependency hierarchy instead of the flat table.

``--json``
    Emit machine-readable JSON.

``--no-rich``
    Plain text output without terminal colours or tables.

**Options (show / show source / type / handler / site-config):**

``--json``
    Emit JSON output instead of Rich/plain text. Useful for scripting.

``--no-rich``
    Emit plain text without terminal colours or tables.

``--schema``
    Emit a JSON Schema describing the complete registry (sources, types,
    handlers). Does not require a sub-command.

**Examples:**

.. code-block:: bash

    # --- show deps ---

    # Flat table of all resolved dependencies (default)
    $ ivpm show deps --no-rich
    Name      Version  Specifier  Source  URL
    --------  -------  ---------  ------  ---
    pyyaml    6.0.1    root       pypi
    requests  2.31.0   root       pypi

    # Dependency tree
    $ ivpm show deps --tree --no-rich
    my-project
    ├── pyyaml       6.0.1   (pypi)
    └── requests     2.31.0  (pypi)

    # Detail for a single package
    $ ivpm show deps pyyaml --no-rich
    Name:          pyyaml
    Specifier:     root
    Source:        pypi
    Version:       6.0.1
    Also requested by: (none)

    # Machine-readable flat list
    $ ivpm show deps --json | jq '.[].name'

    # --- show (registry) ---

    # Overview — show all registries at once
    $ ivpm show --no-rich

    # List registered source types
    $ ivpm show source --no-rich

    # List content types as JSON
    $ ivpm show type --json

    # Full detail for the Python handler
    $ ivpm show handler python --no-rich

    # List registered site configs + the active effective settings
    $ ivpm show site-config --no-rich

    # Dump complete registry schema
    $ ivpm show --schema

**Use case:** ``ivpm show deps`` answers *"what is actually installed and who
asked for it?"* — handy for auditing transitive dependencies, understanding
ownership conflicts, and integrating with downstream tooling via JSON output.
The other ``show`` sub-commands discover what sources, types, and handlers are
active in the current environment, including any third-party extensions.

snapshot
--------

Create a self-contained snapshot of the project.

**Synopsis:**

.. code-block:: text

    ivpm snapshot [-p <project-dir>] [-r|--rls-deps] <snapshot_dir>

**Arguments:**

``snapshot_dir``
    Output directory for snapshot

**Options:**

``-p, -project-dir <dir>``
    Project directory (default: current)

``-r, --rls-deps``
    Use release deps (``default``) instead of dev deps

**Examples:**

.. code-block:: bash

    $ ivpm snapshot /tmp/my-snapshot
    $ ivpm snapshot --rls-deps /tmp/release-snapshot
    $ ivpm snapshot -p /path/to/project /tmp/snapshot

**Output:**

Creates directory with:

- Project source
- All dependency sources
- ``python_pkgs.txt`` (list of Python packages)
- Updated ``ivpm.yaml`` with exact versions

**Use case:** Archival, reproducible builds, offline distribution

status
------

Check status of Git dependencies.

**Synopsis:**

.. code-block:: text

    ivpm status

**Examples:**

.. code-block:: bash

    $ ivpm status

**Output:**

For each Git package:

- Package name
- Current branch
- Modified files
- Untracked files
- Commits ahead/behind remote

**Use case:** See which dependencies have uncommitted changes.

sync
----

Synchronize Git dependencies with upstream.

**Synopsis:**

.. code-block:: text

    ivpm sync

**Examples:**

.. code-block:: bash

    $ ivpm sync

**Behavior:**

For each Git package on a branch:

1. ``git fetch origin``
2. ``git merge origin/<branch>``

Skips:

- Packages on tags (immutable)
- Packages on specific commits (immutable)
- Packages with uncommitted changes (safety)

After sync completes, ``packages/package-lock.json`` is updated to reflect
the new commit hashes of all synced packages.  See :doc:`package_lock`.

update
------

Fetch dependencies and initialize environment.

**Synopsis:**

.. code-block:: text

    ivpm update [options]

**Options:**

``-p, --project-dir <dir>``
    Project directory (default: current)

``-d, --dep-set <name>``
    Use specified dependency set

``-j, --jobs <n>``
    Parallel package fetches (default: CPU count)

``--ssh``
    Force SSH: rewrite ``https://`` git URLs to ``git@host:path`` form

``-a, --anonymous-git``
    Force HTTPS: clone git URLs as written (do not rewrite to SSH)

``--git-auth-order <list>``
    Comma-separated git auth order to try (``gh,ssh,https``); overrides
    ``IVPM_GIT_AUTH_ORDER`` and the config files for this invocation.

``--skip-py-install``
    Skip Python package installation

``--force-py-install``
    Force Python package reinstallation

``--py-prerls-packages``
    Allow pre-release Python packages

``--py-uv``
    Use 'uv' for package management

``--py-pip``
    Use 'pip' for package management

``--py-system-site-packages``
    Inherit the base Python's system site-packages inside the created virtual
    environment.  By default the environment is **isolated** (system packages
    are not visible).  Pass this flag only when you intentionally need access
    to system-installed packages.

``--lock-file <path>``
    Reproduce workspace from a ``package-lock.json`` file.  ``ivpm.yaml``
    is not read for packages; the lock file supplies the complete package
    list at pinned resolved versions.  See :doc:`package_lock`.

``--from <path-or-url>``
    Drive the update from an external manifest — a local path, a ``file://``
    URL, or an ``http(s)://`` URL — instead of the cwd ``ivpm.yaml``.  Resolved
    dependencies land in the current directory (under the deps directory).  The
    external manifest is **not** copied locally; instead the resolved
    ``package-lock.json`` records a ``source_manifest`` pointing back at it (see
    :doc:`package_lock`).  It is an error to use ``--from`` when the target
    directory already has its own ``ivpm.yaml`` (a workspace has exactly one
    driving manifest), or together with ``--lock-file``.  Remote (URL) manifests
    may not use ``include:``.  Combine with ``-d`` to pick a dependency set.

``--deps-dir <dir>``
    Directory to populate, overriding the manifest's ``deps-dir`` (default:
    ``packages``).  Useful with ``--from`` to place an external manifest's
    resolved workspace under a chosen name without editing the upstream
    manifest.

``--refresh-all``
    Re-fetch all packages regardless of the existing ``package-lock.json``
    state.  Use when you want to pull upstream changes without changing
    ``ivpm.yaml`` specs.

``--force``
    Suppress safety errors during refresh (e.g. uncommitted local changes)
    and implies ``--refresh-all``.

``--timing``, ``--profile``
    After the update, print a breakdown of where time was spent (parse, hash
    resolution, queue-wait, clone, cache store/materialize, venv/pip).  A
    machine-readable record is written to ``deps/.ivpm/perf-<runid>.json`` every
    run regardless of this flag; see :doc:`performance` and ``ivpm perf``.

.. code-block:: bash

    # Basic update
    $ ivpm update
    
    # Specific dependency set
    $ ivpm update -d default
    
    # Force HTTPS (as-written) git clones
    $ ivpm update -a
    
    # Parallel downloads
    $ ivpm update -j 8
    
    # Skip Python install
    $ ivpm update --skip-py-install
    
    # Force Python reinstall
    $ ivpm update --force-py-install
    
    # Reproduce exact workspace from a committed lock file
    $ ivpm update --lock-file ./ivpm.lock

    # Install a dependency set from a published catalog into the cwd
    $ ivpm update --from https://example.com/acme/ivpm.yaml -d gui-tools

    # Place the resolved workspace under a chosen directory
    $ ivpm update --from ./catalog.yaml --deps-dir vendor

    # Re-fetch all packages (pull upstream changes)
    $ ivpm update --refresh-all

    # Print a timing breakdown after the update
    $ ivpm update --timing

**Behavior:**

1. Read ``ivpm.yaml`` (or ``--lock-file`` if provided)
2. Select dependency set
3. Fetch missing dependencies (skip up-to-date packages per lock file)
4. Resolve sub-dependencies recursively
5. Create Python virtual environment (if needed)
6. Install Python packages
7. Write/update ``packages/package-lock.json``

Global Options
==============

These options apply to all commands:

``--version``, ``-V``
    Print the IVPM version and exit.

``--log-level <level>``
    Set logging level: ``INFO``, ``DEBUG``, ``WARN``, ``NONE`` (default)

**Examples:**

.. code-block:: bash

    $ ivpm --log-level DEBUG update
    $ ivpm --log-level INFO status

Environment Variables
=====================

IVPM_CACHE
----------

Path to the package cache directory.

.. code-block:: bash

    export IVPM_CACHE=~/.cache/ivpm

Used by caching system. See :doc:`caching`.

IVPM_PERF_KEEP
--------------

Number of performance records to retain under ``deps/.ivpm/`` (default: 20).
Older records are pruned after each update; set to ``0`` to disable pruning.
See :doc:`performance`.

.. code-block:: bash

    export IVPM_PERF_KEEP=50

IVPM_PROJECT
------------

Exported into ``packages/packages.envrc`` (loaded by direnv) as the project
root directory.

Available to ``env:`` directives as ``${IVPM_PROJECT}``.

IVPM_PACKAGES
-------------

Exported into ``packages/packages.envrc`` (loaded by direnv) as the packages
directory.

Available to ``env:`` directives as ``${IVPM_PACKAGES}``.

GITHUB_TOKEN
------------

GitHub API token for higher rate limits.

.. code-block:: bash

    export GITHUB_TOKEN=ghp_your_token_here

Useful for GitHub Releases and API queries.


YAML File Format
================

JSON Schema
-----------

vars
~~~~

Optional mapping of variable names to default values.  Variables can
be referenced as ``${{name}}`` in any scalar value elsewhere in the
file.  Override from the command line with ``-Dname=value``.

.. code-block:: yaml

   package:
     name: my-project
     vars:
       wacfg: default
       cl:    7716052
     dep-sets:
       - name: default
         deps:
           - name: my_env
             src: cbwa
             wacfg: ${wacfg}

See :doc:`variables` for full details.


IVPM provides a JSON Schema for ``ivpm.yaml`` files that enables IDE autocompletion
and validation. To use it, add a ``$schema`` reference at the top of your file:

.. code-block:: yaml

    $schema: https://fvutils.github.io/ivpm/ivpm.schema.json
    
    package:
      name: my-project
      version: "0.1.0"

The schema is available at:

- **Primary:** https://fvutils.github.io/ivpm/ivpm.schema.json
- **Legacy (backwards compatibility):** https://fvutils.github.io/ivpm/ivpm.json

Most modern editors (VS Code, IntelliJ, Vim with LSP) will automatically provide
validation and autocompletion when the ``$schema`` field is present.

Package Definition
------------------

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/package-def

Dependency Set
--------------

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/dep-set

Package Dependency
------------------

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/package-dep

Environment Specification
--------------------------

A single ``env:`` directive (emitted into ``packages.envrc`` for direnv).

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/env-spec


Common Patterns
===============

Pattern 1: Multi-Environment Project
-------------------------------------

.. code-block:: yaml

    package:
      name: versatile-project
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            - name: runtime-lib
              url: https://github.com/org/runtime.git
              tag: v1.0
              cache: true
        
        - name: default-dev
          deps:
            - name: runtime-lib
              url: https://github.com/org/runtime.git
            - name: pytest
              src: pypi
            - name: coverage
              src: pypi
        
        - name: ci
          deps:
            - name: runtime-lib
              url: https://github.com/org/runtime.git
              tag: v1.0
              cache: true
              anonymous: true
            - name: pytest
              src: pypi

Pattern 2: Monorepo Structure
------------------------------

.. code-block:: yaml

    package:
      name: monorepo
      
      dep-sets:
        - name: default-dev
          deps:
            # Shared libraries
            - name: common-lib
              url: file://${IVPM_PROJECT}/../common-lib
              src: dir
            
            # External deps
            - name: requests
              src: pypi

Pattern 3: Platform-Specific Dependencies
------------------------------------------

.. code-block:: yaml

    package:
      name: cross-platform
      
      dep-sets:
        - name: default-dev
          deps:
            - name: common-tool
              url: https://github.com/org/tool
              src: gh-rls
              cache: true
            
            - name: pytest
              src: pypi

**The gh-rls automatically selects platform-specific binaries.**

See Also
========

- :doc:`getting_started` - Basic command usage
- :doc:`workflows` - Common command workflows
- :doc:`git_integration` - Git command details
- :doc:`caching` - Cache command usage
