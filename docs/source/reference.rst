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

activate
--------

Activate the project-local Python virtual environment.

**Synopsis:**

.. code-block:: text

    ivpm activate [-c <command>] [-p <project-dir>] [args ...]

**Options:**

``-c <command>``
    Execute a command in the activated environment and exit

``-p, --project-dir <dir>``
    Specify project directory (default: current directory)

``args``
    Arguments passed to shell or command

**Examples:**

.. code-block:: bash

    # Start interactive shell
    $ ivpm activate
    
    # Run single command
    $ ivpm activate -c "python script.py"
    $ ivpm activate -c "pytest"
    
    # Different project directory
    $ ivpm activate -p /path/to/project -c "pytest"

**Behavior:**

- Sources ``packages/python/bin/activate``
- Sets ``VIRTUAL_ENV`` and modifies ``PATH``
- Applies environment variables from ``env-sets``
- Without ``-c``, starts an interactive shell
- With ``-c``, runs command and exits

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

    ivpm cache info [-c|--cache-dir <dir>] [-v|--verbose] [--backend <name>]

**Options:**

``-c, --cache-dir <dir>``
    Cache directory (default: ``$IVPM_CACHE``)

``-v, --verbose``
    Show detailed version information

``--backend {auto,filesystem,gha,none}``
    Backend to query (default: ``auto``)

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

    ivpm cache clean [-c|--cache-dir <dir>] [-d|--days <n>] [--backend <name>]

**Options:**

``-c, --cache-dir <dir>``
    Cache directory (default: ``$IVPM_CACHE``)

``-d, --days <n>``
    Remove entries older than N days (default: 7)

``--backend {auto,filesystem,gha,none}``
    Backend to clean (default: ``auto``)

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

``-a, --anonymous``
    Clone anonymously over HTTPS (no SSH)

``-b, --branch <name>``
    Checkout branch; create if doesn't exist

``-d, --dep-set <name>``
    Dependency set for ``ivpm update``

``--py-uv``
    Use 'uv' for Python package management

``--py-pip``
    Use 'pip' for Python package management

**Examples:**

.. code-block:: bash

    # Basic clone
    $ ivpm clone https://github.com/org/project.git
    
    # Custom directory
    $ ivpm clone https://github.com/org/project.git my-workspace
    
    # Specific branch
    $ ivpm clone -b develop https://github.com/org/project.git
    
    # Anonymous clone with dep-set
    $ ivpm clone -a -d default https://github.com/org/project.git
    
    # Use uv for package management
    $ ivpm clone --py-uv https://github.com/org/project.git

**Behavior:**

1. Clones Git repository
2. Enters directory
3. Automatically runs ``ivpm update`` with specified options

Note: Since ``clone`` automatically runs ``update``, you don't need to run
``ivpm update`` separately after ``ivpm clone``.

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

``-a, --anonymous-git``
    Clone Git repos anonymously (HTTPS)

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

``--lock-file <path>``
    Reproduce workspace from a ``package-lock.json`` file.  ``ivpm.yaml``
    is not read for packages; the lock file supplies the complete package
    list at pinned resolved versions.  See :doc:`package_lock`.

``--refresh-all``
    Re-fetch all packages regardless of the existing ``package-lock.json``
    state.  Use when you want to pull upstream changes without changing
    ``ivpm.yaml`` specs.

``--force``
    Suppress safety errors during refresh (e.g. uncommitted local changes)
    and implies ``--refresh-all``.

.. code-block:: bash

    # Basic update
    $ ivpm update
    
    # Specific dependency set
    $ ivpm update -d default
    
    # Anonymous Git clones
    $ ivpm update -a
    
    # Parallel downloads
    $ ivpm update -j 8
    
    # Skip Python install
    $ ivpm update --skip-py-install
    
    # Force Python reinstall
    $ ivpm update --force-py-install
    
    # Reproduce exact workspace from a committed lock file
    $ ivpm update --lock-file ./ivpm.lock

    # Re-fetch all packages (pull upstream changes)
    $ ivpm update --refresh-all

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

IVPM_PROJECT
------------

Set automatically by IVPM to project root directory.

Available in ``env-sets`` as ``${IVPM_PROJECT}``.

IVPM_PACKAGES
-------------

Set automatically by IVPM to packages directory.

Available in ``env-sets`` as ``${IVPM_PACKAGES}``.

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

Environment Set
---------------

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/env-set

Environment Specification
--------------------------

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

