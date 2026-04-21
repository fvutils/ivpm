################
Package Handlers
################

What Are Handlers?
==================

A **handler** is a Python class that observes packages as they are fetched by
``ivpm update`` (or ``ivpm clone``) and performs processing work.  Handlers are
how IVPM turns a collection of fetched files into a working development
environment -- creating virtual environments, generating configuration files,
and aggregating metadata.

Every handler participates in up to two phases of the update pipeline:

**Leaf phase** (per-package, concurrent)
    Called once for each package, on a worker thread, as soon as that package
    is available on disk.  Leaf callbacks are lightweight -- they inspect the
    package content, detect what kind of package it is, and accumulate state
    for later.  Because they run concurrently, writes to shared handler state
    must be synchronized (the base class provides a lock).

**Root phase** (once per run, main thread)
    Called after *all* packages have been fetched.  Root callbacks see the
    full accumulated state from the leaf phase and perform heavier work:
    creating virtual environments, installing packages, writing generated
    files, etc.  Root callbacks run sequentially, ordered by each handler's
    ``phase`` number (lower runs first).

Both phases are optional -- a handler may implement only the one(s) it needs.

IVPM discovers handlers through Python `entry points`_ (the
``ivpm.handlers`` group), so any installed package can contribute handlers
without modifying IVPM itself.

.. _entry points: https://packaging.python.org/en/latest/specifications/entry-points/


How Handlers Fit into the Update Pipeline
==========================================

When you run ``ivpm update``, IVPM executes these stages:

.. code-block:: text

    ivpm.yaml
        |
        v
    1. Resolution ---- read dep-sets, resolve sub-dependencies recursively
        |
        v
    2. Fetch ---------- fetch each package by source type (git, pypi, http, ...)
        |                  |
        |            [leaf handlers inspect each package concurrently]
        |
        v
    3. Process -------- root handlers run in phase order
        |                  phase 0: python, direnv, agents (built-in)
        |                  phase N: any third-party handlers
        |
        v
    4. Lock file ------ handlers contribute entries, lock file written

The ``PackageHandlerList`` dispatcher manages this flow: it forwards each
fetched package to every handler's leaf callback (filtered by ``leaf_when``
conditions), accumulates the full package list, then calls each handler's
root callback (filtered by ``root_when`` conditions) in phase order.


Built-in Handlers
=================

IVPM ships three built-in handlers, all at ``phase = 0``.  They are
registered via entry points in IVPM's own ``pyproject.toml`` and run on every
``update`` and ``clone`` invocation.

.. tip::

   Run ``ivpm show handler`` to see all registered handlers (built-in and
   third-party) with live documentation.  Use ``ivpm show handler python``
   for full per-handler detail including CLI options.

.. _handler-python:

Python Handler (``python``)
----------------------------

Manages the project-local Python virtual environment at ``packages/python/``.

**Purpose**

Detects Python packages across the dependency tree, creates a virtual
environment (using pip or uv), and installs all detected packages --
source packages in editable mode, PyPI packages as binaries.  Installation
order is determined by topological sort of inter-package dependencies.

**Leaf phase**

Runs for every package.  Detection rules:

- ``src: pypi`` -- always a Python package
- Has ``setup.py``, ``setup.cfg``, or ``pyproject.toml`` -- detected as Python
- Explicit ``type: python`` in ``ivpm.yaml`` -- detected as Python

Detected packages are tagged with ``pkg.pkg_type = "python"`` and recorded
for the root phase.

**Root phase**

Runs when at least one Python package was detected *or* when the project has
``with.python`` configuration.  Steps:

1. Create the ``packages/python/`` virtual environment (if absent)
2. Install setup-deps first (if any)
3. Install IVPM itself into the venv
4. Install PyPI packages
5. Install source packages in topological order (editable by default)

**Configuration (``ivpm.yaml``)**

Project-level settings under ``package.with.python``:

.. code-block:: yaml

    package:
      name: my-project
      with:
        python:
          venv: uv                    # uv | pip | true (auto) | false (skip)
          system-site-packages: false # inherit system packages
          pre-release: false          # allow pre-release packages

Per-package options via the ``type:`` field:

.. code-block:: yaml

    deps:
      # Non-editable install
      - name: stable-lib
        url: https://github.com/org/stable-lib.git
        type: { python: { editable: false } }

      # With PEP 508 extras
      - name: my-lib
        url: https://github.com/org/my-lib.git
        type: { python: { extras: [tests, docs] } }

**CLI options** (on ``update`` and ``clone``):

- ``--py-uv`` -- use uv instead of pip
- ``--py-pip`` -- force pip (overrides uv auto-detection)
- ``--skip-py-install`` -- skip Python package installation entirely
- ``--force-py-install`` -- force re-installation of all Python packages
- ``--py-prerls-packages`` -- allow pre-release packages
- ``--py-system-site-packages`` -- create the venv with system site-packages visible

**Lock file contribution**

After installation, the Python handler queries the venv for all installed
package versions (via ``pip list``) and writes them under the
``python_packages`` key in ``packages/package-lock.json``.

**Output:** ``packages/python/`` -- the project-local virtual environment.

See :doc:`python_packages` for full Python workflow details.


.. _handler-direnv:

Direnv Handler (``direnv``)
----------------------------

Collects per-package environment files and assembles them into a single
``packages/packages.envrc``.

**Purpose**

Many packages export environment variables via ``.envrc`` or
``export.envrc`` files (used by `direnv <https://direnv.net/>`_).  The
direnv handler discovers these files and generates one combined envrc file
that sources them all in the correct dependency order.

**Leaf phase**

Runs for every non-PyPI package.  Checks for ``.envrc`` or ``export.envrc``
in the package root directory.  If found, the package is recorded for the
root phase.

**Root phase**

Runs when at least one package with an envrc file was found.  Steps:

1. Build a dependency map among envrc-providing packages
2. Topologically sort them (dependencies before dependents)
3. Write ``packages/packages.envrc`` with ``source_env`` lines in order

**Configuration:** None.  No ``with:`` parameters, no CLI options.

**Output:** ``packages/packages.envrc``

**Usage:** Add the following to your project-level ``.envrc``:

.. code-block:: bash

    source_env packages/packages.envrc


.. _handler-agents:

Agents Handler (``agents``)
----------------------------

Creates symlinks (or copies) to per-package skill files for AI coding agents.

**Purpose**

Packages can provide skill files (``SKILL.md``) that describe capabilities or
instructions for AI agents.  The agents handler discovers these skill files and
creates organized symlinks in ``.agents/skills/`` and optionally ``.claude/skills/``
for use by AI tools.

**Skill File Format**

Each skill file must contain YAML frontmatter with at least ``name:`` and
``description:`` fields:

.. code-block:: markdown

    ---
    name: my-skill
    description: One-line description of what this skill does.
    ---

    Body of the skill document...

Optional frontmatter fields: ``license``, ``compatibility``, ``allowed-tools``.

Packages with missing or malformed frontmatter are skipped with a warning.

**Leaf phase**

Runs for every non-PyPI package.  Discovers skill files using one of three methods
(in priority order):

1. **Consumer-specified paths** (highest priority)
   
   The importing project specifies skill paths in the dep entry:
   
   .. code-block:: yaml
   
       deps:
         - name: my-package
           url: https://github.com/org/my-package.git
           agents:
             skills:
               - skills/**/SKILL.md
               - docs/SKILL.md

2. **Package-declared paths**
   
   The package itself specifies skill paths in its ``ivpm.yaml``:
   
   .. code-block:: yaml
   
       handlers:
         agents:
           skills:
             - skills/**/SKILL.md
             - docs/SKILL.md

3. **Auto-probe** (lowest priority, fallback)
   
   No explicit paths declared — automatically checks for ``SKILL.md`` in the
   package root.  If found and valid, it is used.

Skill paths support glob patterns (e.g., ``skills/**/SKILL.md``) and are
evaluated relative to the package directory.

**Root phase**

Runs when at least one valid skill file was found.  Steps:

1. Create ``.agents/skills/`` directory
2. Create ``.claude/skills/`` directory if ``claude: true`` OR if ``.claude/``
   already exists
3. For each skill, create a relative symlink (or copy as fallback) with the
   package name as the link name
4. If a package has multiple skills, name them as ``<package>-1``, ``<package>-2``, etc.
5. Remove stale entries from previous runs

**Configuration (``ivpm.yaml``)**

Project-level settings under ``package.with.agents``:

.. code-block:: yaml

    package:
      name: my-project
      with:
        agents:
          claude: true          # Create .claude/skills/ in addition to .agents/skills/

Per-package skill paths via handler config:

.. code-block:: yaml

    handlers:
      agents:
        skills:
          - skills/**/SKILL.md
          - docs/SKILL.md

Or via consumer dep-entry:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        agents:
          skills:
            - skills/SKILL.md

**CLI options:** None.  Configuration is via ``ivpm.yaml``.

**Symlink behavior**

- **Symlink support**: Creates relative symlinks from ``.agents/skills/`` to skill
  directories within the package.
- **Fallback**: On platforms without symlink support, falls back to copying the
  ``SKILL.md`` file and any companion directories (``scripts/``, ``references/``,
  ``assets/``).
- **`.claude` directory**: Always populates if ``claude: true``. Also populates
  automatically if ``.claude/`` directory already exists (useful for projects
  that have manually created it).
- **Stale cleanup**: Removes entries from previous runs before writing new ones.

**Output:**

- ``.agents/skills/<package>`` — symlink(s) to skill directories
- ``.claude/skills/<package>`` — same, if ``claude: true`` or if ``.claude/`` exists


Handler Summary
===============

.. list-table::
   :header-rows: 1
   :widths: 12 30 25 25 15

   * - Handler
     - Purpose
     - Leaf Detection
     - Root Action
     - Output
   * - ``python``
     - Python venv and package install
     - ``setup.py`` / ``pyproject.toml`` / ``src: pypi``
     - Creates venv, installs packages
     - ``packages/python/``
   * - ``direnv``
     - Environment file aggregation
     - ``.envrc`` / ``export.envrc``
     - Writes combined envrc
     - ``packages/packages.envrc``
   * - ``agents``
     - Skill file discovery and symlinking
     - ``SKILL.md`` (auto or declared)
     - Creates symlinks to skills
     - ``.agents/skills/``, ``.claude/skills/``


Discovering Handlers
====================

Use ``ivpm show`` to inspect registered handlers:

.. code-block:: bash

    # List all handlers
    $ ivpm show handler

    # Details for a specific handler
    $ ivpm show handler python

    # JSON output for scripting
    $ ivpm show handler --json

This shows both built-in and third-party handlers installed in the current
environment.


Writing Custom Handlers
=======================

To create your own handler, see :doc:`extending_ivpm` for the full API
reference, including the ``PackageHandler`` base class, activation conditions,
thread safety, progress reporting, and entry-point registration.
