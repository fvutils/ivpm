################
Package Handlers
################

What Are Handlers?
==================

A **handler** is a Python class that observes packages as they are fetched by
``ivpm update`` (or ``ivpm clone``) and performs processing work.  Handlers are
how IVPM turns a collection of fetched files into a working development
environment -- each handler builds a unified *view* of one facet of the
project: a virtual environment, a Node.js environment, an environment-variable
file, an agent skills directory, a FuseSoC library map, or a set of loaded
modules.

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
        |                  phase 0: direnv (built-in)
        |                  phase 5: python (built-in)
        |                  phase 6: agents (built-in)
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

IVPM ships six built-in handlers.  They run in phase order (direnv → modules
→ python → node/agents → fusesoc) so that the environment is configured
before Python packages are installed, and the Python venv is ready before the
agents handler queries ``ivpm.skill`` entry-points.
They are registered via entry points in IVPM's own ``pyproject.toml`` and run on every
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


.. _handler-node:

Node Handler (``node``)
------------------------

Manages the project-local Node.js environment at ``packages/node/``.

**Purpose**

Detects Node.js packages across the dependency tree, synthesises a
``packages/node/package.json``, and runs the configured package manager
(npm / pnpm / yarn) to install all detected packages.  Source packages with
a ``package.json`` are linked via ``npm link`` so they can be
``require()``-d directly.

**Leaf phase**

Runs for every package.  Detection rules:

- ``src: npm`` -- always a Node.js package (installed via the package manager)
- ``src: package.json`` -- import deps from an existing ``package.json`` file
- Has ``package.json`` in its path -- auto-detected as a Node.js source package
- Explicit ``type: node`` in ``ivpm.yaml`` -- detected as a linkable source package

**Root phase**

Runs when at least one Node.js package was detected *or* when the project has
``with.node`` configuration.  Steps:

1. Synthesise ``packages/node/package.json`` from all collected npm packages
2. Compare SHA-256 hash with stored value -- skip install if unchanged and
   ``node_modules/`` exists
3. Run ``npm install --prefix packages/node`` (or pnpm/yarn equivalent)
4. Run ``npm link <path>`` for each source package with ``link: true``
5. Write ``packages/node/export.envrc`` (and Windows ``.bat``/``.ps1`` helpers)
6. Patch sentinel section in ``packages/packages.envrc``
7. Write ``packages/node/.nvmrc`` if ``version:`` is set

**Configuration (``ivpm.yaml``)**

Project-level settings under ``package.with.node``:

.. code-block:: yaml

    package:
      name: my-project
      with:
        node:
          manager: npm      # npm (default) | pnpm | yarn
          version: "20"     # Node version → writes .nvmrc
          env: true         # Patch packages.envrc (default: true)

Per-package options via the ``type:`` field:

.. code-block:: yaml

    deps:
      # Link a TypeScript library into the node environment
      - name: my-ts-lib
        url: https://github.com/org/my-ts-lib.git
        type: { node: { dev: false, link: true } }

**Output:** ``packages/node/`` -- the project-local Node.js environment.

See :doc:`node_packages` for full Node.js workflow details.


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

Runs for every non-PyPI package.  Discovers skill files using one of four methods
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
   
   The package itself specifies skill paths in its ``ivpm.yaml`` under
   ``package.with.agents``:
   
   .. code-block:: yaml
   
       package:
         name: my-package
         with:
           agents:
             skills:
               - skills/**/SKILL.md
               - docs/SKILL.md

3. **Auto-probe** (lowest priority, fallback)
   
   No explicit paths declared — automatically checks two locations:

   a. ``SKILL.md`` in the package root — used if present and valid.
   b. ``skills/`` subdirectory — all ``SKILL.md`` files found recursively
      under ``<package-root>/skills/`` are included.

   Both locations are checked; a package may contribute multiple skills this way.

4. **Python ``ivpm.skill`` entry-points** (Python packages only)

   Python packages installed into the project's managed virtual environment may
   register skills via the ``ivpm.skill`` `entry-point group`_.  The Python
   handler queries this group after installing all packages, and the agents
   handler processes the results.

   Each entry-point must be a callable that returns either a single path
   (``str``) or a list of paths to **directories containing** ``SKILL.md``:

   .. code-block:: toml

       # pyproject.toml of the skill-providing Python package
       [project.entry-points."ivpm.skill"]
       my-skill = "mypkg.skills:get_skill_dir"

   .. code-block:: python

       # mypkg/skills.py
       import importlib.resources

       def get_skill_dir() -> str:
           """Return the path to the directory containing SKILL.md."""
           return str(importlib.resources.files("mypkg") / "skill_data")

   The returned directory must contain a ``SKILL.md`` with valid frontmatter.
   Entry-points that raise exceptions or return invalid paths emit a warning
   and are skipped.  This mechanism is independent of the package having an
   ``ivpm.yaml`` — it works for any Python package installed into the venv.

.. _entry-point group: https://packaging.python.org/en/latest/specifications/entry-points/

Skill paths (for mechanisms 1–3) support glob patterns (e.g.,
``skills/**/SKILL.md``) and are evaluated relative to the package directory.

**Root phase**

Runs when at least one valid skill file was found.  Steps:

1. Create ``.agents/skills/`` directory
2. Create ``.claude/skills/`` directory if ``claude: true`` OR if ``.claude/``
   already exists
3. Process skills gathered from dependencies (mechanisms 1–3 above) and from
   ``ivpm.skill`` Python entry-points (mechanism 4)
4. For each skill, create a relative symlink (or copy as fallback) with a
   human-readable name derived from its source directory
5. Dependency skills are named as ``<package>-<dir>`` (or just ``<package>`` for a
   package-root ``SKILL.md``); conflicting names expand to include parent
   directories, such as ``<package>-<parent>-<dir>``
6. Root-project skills are named as ``<dir>``; conflicting names expand to include
   parent directories, such as ``<parent>-<dir>``
7. Remove stale entries from previous runs

**Configuration (``ivpm.yaml``)**

Project-level settings under ``package.with.agents``:

.. code-block:: yaml

    package:
      name: my-project
      with:
        agents:
          claude: true          # Create .claude/skills/ in addition to .agents/skills/

Package-declared skill paths under ``package.with.agents``:

.. code-block:: yaml

    package:
      name: my-lib
      with:
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


.. _handler-modules:

Modules Handler (``modules``)
------------------------------

Generates ``module load`` statements for `Environment Modules
<https://modules.readthedocs.io/>`_ integration.

**Purpose**

Packages can declare an Environment Module dependency via the ``module``
content type in ``ivpm.yaml``.  The modules handler collects these
declarations, generates ``packages/modules.envrc`` with ``module load``
statements, and patches ``packages/packages.envrc`` to source it.

**Leaf phase**

Inspects every package.  Packages carrying ``ModuleTypeData`` with
``load: true`` are recorded.

**Root phase**

Writes ``packages/modules.envrc`` with one ``module load <spec>`` line per
discovered module.  Patches ``packages/packages.envrc`` with a
sentinel-wrapped ``source_env`` line.  Cleans up stale entries when no
modules remain.

**Configuration (``ivpm.yaml``)**

.. code-block:: yaml

    deps:
      - name: gcc-toolchain
        type: { module: { load: true, module: "gcc/15.2.0" } }

**Output:** ``packages/modules.envrc``


.. _handler-fusesoc:

FuseSoC Handler (``fusesoc``)
------------------------------

Discovers `FuseSoC <https://fusesoc.readthedocs.io/>`_ ``.core`` files
from dependencies and generates library metadata.

**Purpose**

When dependencies contain CAPI-2 ``.core`` files (or declare
``with.fusesoc.cores`` paths), the FuseSoC handler collects those
directories and writes ``packages/fusesoc-cores.envrc`` (setting
``FUSESOC_CORES``) and ``packages/fusesoc-cores.txt``.  Optionally, when
``update-conf: true`` is set in ``with.fusesoc``, it also updates
``fusesoc.conf`` with ``[library.ivpm.*]`` sections.

**Leaf phase**

Inspects every non-PyPI package.  Recursively searches for valid ``.core``
files and records directories that contain them.

**Root phase**

Always runs (cleans stale entries even when no cores remain).  Writes
output files and patches ``packages/packages.envrc``.

**Configuration (``ivpm.yaml``)**

.. code-block:: yaml

    package:
      name: soc-project
      with:
        fusesoc:
          update-conf: true
          cores:
            - rtl/cores

**Output:** ``packages/fusesoc-cores.envrc``, ``packages/fusesoc-cores.txt``,
optionally ``fusesoc.conf``

See :doc:`integrations` for FuseSoC integration patterns with CMake and
other build systems.


Handler Summary
===============

.. list-table::
   :header-rows: 1
   :widths: 12 8 30 25 25 15

   * - Handler
     - Phase
     - Purpose
     - Leaf Detection
     - Root Action
     - Output
   * - ``direnv``
     - 0
     - Environment file aggregation
     - ``.envrc`` / ``export.envrc``
     - Writes combined envrc
     - ``packages/packages.envrc``
   * - ``modules``
     - 1
     - Environment Modules integration
     - Packages with ``ModuleTypeData``
     - Writes ``module load`` statements
     - ``packages/modules.envrc``
   * - ``python``
     - 5
     - Python venv and package install
     - ``setup.py`` / ``pyproject.toml`` / ``src: pypi``
     - Creates venv, installs packages; queries ``ivpm.skill`` entry-points
     - ``packages/python/``
   * - ``node``
     - 6
     - Node.js environment and package install
     - ``package.json`` / ``src: npm``
     - Synthesises ``package.json``, runs npm/pnpm/yarn, links source packages
     - ``packages/node/``
   * - ``agents``
     - 6
     - Skill file discovery and symlinking
     - ``SKILL.md`` at root, under ``skills/``, declared paths, or ``ivpm.skill`` entry-points
     - Creates symlinks to skills
     - ``.agents/skills/``, ``.claude/skills/``
   * - ``fusesoc``
     - 10
     - FuseSoC core library mapping
     - ``.core`` files (CAPI-2) or ``with.fusesoc.cores``
     - Writes ``fusesoc-cores.envrc``, ``fusesoc-cores.txt``; optionally updates ``fusesoc.conf``
     - ``packages/fusesoc-cores.*``


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
