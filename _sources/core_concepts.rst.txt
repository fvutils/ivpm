##############
Core Concepts
##############

This page explains the mental model behind IVPM.  Understanding these
concepts makes the rest of the documentation easier to navigate.

Project-Local Management
========================

IVPM stores all dependencies inside each project, making projects
self-contained and portable.  There is no global package database and no
shared state between projects.

A typical IVPM-enabled project has this structure::

    my-project/
    ├── ivpm.yaml                 # Package configuration
    ├── packages/                 # Dependencies directory
    │   ├── python/              # Python virtual environment (if needed)
    │   ├── dependency-1/        # Source package (editable)
    │   ├── dependency-2/        # Cached package (symlink, read-only)
    │   └── ...
    ├── src/                     # Your project source
    └── ...

The ``packages/`` directory is created by ``ivpm update`` and is typically
listed in ``.gitignore``.  Deleting it and re-running ``ivpm update``
recreates the entire environment from scratch.


The Update Pipeline
===================

When you run ``ivpm update``, IVPM executes a three-stage pipeline:

.. code-block:: text

    ┌───────────────────────────────────────────────────────┐
    │ Stage 1: Resolution                                   │
    │   Read ivpm.yaml, select dependency set,              │
    │   resolve sub-dependencies recursively                │
    └──────────────────────┬────────────────────────────────┘
                           │
    ┌──────────────────────▼────────────────────────────────┐
    │ Stage 2: Fetch                                        │
    │   Fetch each package by source type                   │
    │   (git, pypi, http, gh-rls, dir, file)                │
    │                                                       │
    │   As each package lands on disk, leaf handlers         │
    │   inspect it concurrently                             │
    └──────────────────────┬────────────────────────────────┘
                           │
    ┌──────────────────────▼────────────────────────────────┐
    │ Stage 3: Process                                      │
    │   Root handlers run sequentially (by phase number)    │
    │     - Python handler: create venv, install packages   │
    │     - Direnv handler: write packages.envrc            │
    │     - Skills handler: write SKILLS.md                 │
    │                                                       │
    │   Lock file written (packages/package-lock.json)      │
    └───────────────────────────────────────────────────────┘

**Stage 1 -- Resolution:**
IVPM reads ``ivpm.yaml``, selects the active dependency set (via ``-d`` flag
or ``default-dep-set``), and walks sub-package ``ivpm.yaml`` files
recursively to build a complete dependency graph.

**Stage 2 -- Fetch:**
Each package is fetched according to its *source type* -- ``git`` clones a
repository, ``pypi`` downloads from PyPI, ``http`` fetches an archive, and
so on.  Fetches run in parallel.  As each package becomes available on disk,
registered :doc:`handlers <handlers>` run their **leaf callbacks**
concurrently to detect and classify the package.

**Stage 3 -- Process:**
After all packages are fetched, handlers run their **root callbacks** on the
main thread.  The Python handler creates a virtual environment and installs
Python packages.  Other handlers generate configuration files.  Finally,
the lock file is written.

For details on the handler mechanism, see :doc:`handlers`.


Source Types and Content Types
==============================

Every package has two key attributes:

**Source type** -- how to fetch the package:

- ``git`` -- clone a Git repository
- ``pypi`` -- install from the Python Package Index
- ``http`` -- download an archive via HTTP/HTTPS
- ``gh-rls`` -- download from a GitHub Release
- ``dir`` -- symlink a local directory
- ``file`` -- use a local archive file

**Content type** -- what the package contains and how to process it:

- ``python`` -- a Python package (installed into the venv by the Python handler)
- ``raw`` -- data, HDL, or other files (placed in ``packages/`` with no further processing)

These attributes are independent: a ``git`` source can contain a ``python``
package or a ``raw`` package.  IVPM auto-detects both in most cases.

For complete attribute reference and auto-detection rules, see
:doc:`package_types`.


Dependency Sets
===============

**Dependency sets** are named collections of dependencies that let you
maintain different profiles for different scenarios:

.. code-block:: yaml

    package:
      name: my-project
      default-dep-set: default-dev

      dep-sets:
        - name: default           # Release: runtime dependencies only
          deps:
            - name: core-lib
              url: https://github.com/org/core-lib.git

        - name: default-dev       # Development: adds test tools
          uses: default
          deps:
            - name: pytest
              src: pypi

Common uses: separating dev from release dependencies, creating different
build-target profiles, and controlling which sub-dependencies get loaded.

For complete dependency set documentation, see :doc:`dependency_sets`.


Recursive Sub-Dependencies
==========================

When a fetched package has its own ``ivpm.yaml``, IVPM resolves its
dependencies recursively.  By default, sub-packages inherit the parent's
dependency set name.  You can override this per-package:

.. code-block:: yaml

    deps:
      - name: library-a
        url: https://github.com/org/library-a.git
        dep-set: default   # Use release deps even when parent uses default-dev

This prevents a third-party library's development tools from being pulled
into your project.


Lock File and Reproducibility
==============================

Every ``ivpm update`` writes ``packages/package-lock.json``, recording the
exact resolved identity of every fetched package (git commit hashes, release
tags, pip versions, HTTP ETags).  This file enables exact workspace
reproduction:

.. code-block:: bash

    # Reproduce an archived workspace
    $ ivpm update --lock-file ./ivpm.lock

For full details, see :doc:`package_lock`.


Next Steps
==========

- :doc:`handlers` -- How handlers process packages (Python, Direnv, Skills)
- :doc:`getting_started` -- Install IVPM and set up your first project
- :doc:`dependency_sets` -- Dependency set patterns and inheritance
- :doc:`package_types` -- All source types, content types, and attributes
