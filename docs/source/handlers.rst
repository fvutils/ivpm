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
    files, etc.  Root callbacks run sequentially, in an order computed from
    each handler's named **phase** and its relative ``run_after`` /
    ``run_before`` constraints (see `Handler Ordering`_).

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
    3. Process -------- root handlers run in resolved phase order
        |                  ENVIRONMENT: direnv, modules (built-in)
        |                  INSTALL:     node, python (built-in)
        |                  INTEGRATE:   agents, dv-flow, fusesoc (built-in)
        |                  (third-party handlers slot into these phases)
        |
        v
    4. Lock file ------ handlers contribute entries, lock file written

Phases are **barriers**: all handlers in one phase finish before any handler
in the next begins (see `Handler Ordering`_).

The ``PackageHandlerList`` dispatcher manages this flow: it forwards each
fetched package to every handler's leaf callback (filtered by ``leaf_when``
conditions), accumulates the full package list, then calls each handler's
root callback (filtered by ``root_when`` conditions) in the resolved order.


Built-in Handlers
=================

IVPM ships seven built-in handlers.  They resolve to the order
direnv → modules → node → python → agents → dv-flow → fusesoc: the
``ENVIRONMENT`` phase (direnv, modules) configures the environment, the
``INSTALL`` phase (node, python) installs managed packages, and the
``INTEGRATE`` phase (agents, dv-flow, fusesoc) generates tool-integration
artifacts.  Because phases are barriers, the Python venv is ready before the
agents handler queries ``agent.skills`` entry-points.
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
- ``--py-skip-install`` -- skip Python package installation entirely
- ``--py-force-install`` -- force re-installation of all Python packages
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
(npm / pnpm / yarn) to install all detected packages.  Source packages with a
``package.json`` are emitted as relative ``file:`` dependencies, which npm
installs as symlinks -- an editable install whose executables land in
``node_modules/.bin`` with everything else.

**Leaf phase**

Runs for every package.  Detection rules:

- ``src: npm`` -- always a Node.js package (installed via the package manager)
- ``src: package.json`` -- import deps from an existing ``package.json`` file
- Has ``package.json`` in its path -- auto-detected as a Node.js source package
- Explicit ``type: node`` in ``ivpm.yaml`` -- detected as a linkable source package

**Root phase**

Runs when at least one Node.js package was detected *or* when the project has
``with.node`` configuration.  Steps:

1. Synthesise ``packages/node/package.json`` from all collected npm packages,
   plus a relative ``file:`` entry for each source package with ``link: true``
2. Compare SHA-256 hash with stored value -- skip install if unchanged and
   ``node_modules/`` exists
3. Run ``npm install --prefix packages/node`` (or pnpm/yarn equivalent)
4. Symlink ``<project_root>/node_modules`` at the managed ``node_modules``,
   unless ``link-root: false`` or the mode is ``TOOLCHAIN``
5. Write ``packages/node/export.envrc`` (a direnv snippet, all platforms)
6. Patch sentinel section in ``packages/packages.envrc``
7. Write ``packages/node/.nvmrc`` if ``version:`` is set

**Destroy phase**

Removes ``packages/node`` and the root ``node_modules`` symlink -- the latter
only when it is a symlink resolving into the managed tree, never a real
directory.

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
          link-root: true   # Symlink <project>/node_modules (default: true)

Per-package options via the ``type:`` field:

.. code-block:: yaml

    deps:
      # Link a TypeScript library into the node environment
      - name: my-ts-lib
        url: https://github.com/org/my-ts-lib.git
        type: { node: { dev: false, link: true } }

**Output:** ``packages/node/`` -- the project-local Node.js environment -- plus
a ``node_modules`` symlink at the project root.

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

Runs when at least one package with an envrc file was found, or when the
root project declares ``env:`` directives.  Steps:

1. Build a dependency map among envrc-providing packages
2. Topologically sort them (dependencies before dependents)
3. Write ``packages/packages.envrc``:

   a. ``export IVPM_PACKAGES`` and ``export IVPM_PROJECT``
   b. one ``source_env`` line per package, in dependency order
   c. the root project's ``env:`` directives last (so the project's own
      declarations take precedence over package-provided envrc)

**Configuration:** None.  No ``with:`` parameters, no CLI options.

**Output:** ``packages/packages.envrc``

**Usage:** Add the following to your project-level ``.envrc``:

.. code-block:: bash

    source_env packages/packages.envrc


.. _handler-agents:

Agents Handler (``agents``)
----------------------------

Creates symlinks (or copies) to per-package skill files and Agent Plugins for
AI coding agents.

.. seealso::

   :doc:`agent_plugins` covers Agent Plugins support in depth: discovery,
   validation, the per-tool projection strategy, and MCP configuration.  This
   section documents the handler itself and the loose-skills mechanism.

**Purpose**

Packages can provide skill files (``SKILL.md``) that describe capabilities or
instructions for AI agents.  The agents handler discovers these skill files and
creates organized symlinks in ``.agents/skills/`` and, by default, the
tool-specific ``.claude/skills/`` and ``.cursor/skills/`` directories for use by
AI tools.  The tool-specific mirrors are *opt-out* — see **Configuration** below.

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

Runs for every non-PyPI package.

`Agent Plugins <https://agent-plugins.org/specification>`_ are looked for first:
a ``plugin.json`` at the package root or under ``plugins/*/``, or the paths named
by ``with.agents.plugins`` or an ``agents: {plugins: [...]}`` dep entry.  A
plugin names its own skills, so those directories are not also discovered as
loose skills.  Python packages may register plugins through the
``agent.plugins`` entry-point group.  See :doc:`agent_plugins`.

Remaining skill files are then discovered using one of four methods
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

4. **Python ``agent.skills`` entry-points** (Python packages only)

   Python packages installed into the project's managed virtual environment may
   register skills via the ``agent.skills`` `entry-point group`_.  The Python
   handler queries this group after installing all packages, and the agents
   handler processes the results.

   Each entry-point must be a callable that returns either a single path
   (``str``) or a list of paths to **directories containing** ``SKILL.md``:

   .. code-block:: toml

       # pyproject.toml of the skill-providing Python package
       [project.entry-points."agent.skills"]
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

1. Create the ``.agents/skills/`` directory, and ``.agents/plugins/`` when any
   Agent Plugin was found
2. Create the ``.claude/skills/`` and ``.cursor/skills/`` directories by
   default; skip either one when ``claude: false`` / ``cursor: false`` is set.
   An explicit ``false`` always wins, even if the corresponding directory
   already exists.
3. Process skills gathered from dependencies (mechanisms 1–3 above) and from
   ``agent.skills`` Python entry-points (mechanism 4)
4. For each skill, create a relative symlink (or copy as fallback) with a
   human-readable name derived from its source directory
5. Dependency skills are named as ``<package>-<dir>`` (or just ``<package>`` for a
   package-root ``SKILL.md``); conflicting names expand to include parent
   directories, such as ``<package>-<parent>-<dir>``
6. Root-project skills are named as ``<dir>``; conflicting names expand to include
   parent directories, such as ``<parent>-<dir>``
7. Link each Agent Plugin whole into ``.agents/plugins/<name>``, named from its
   manifest.  Tools with a plugin mechanism of their own receive the plugin
   itself instead of its individual skills -- Claude Code gets
   ``.claude/skills/<name>/`` with a generated ``.claude-plugin/plugin.json``.
   Tools without one receive each skill as ``<plugin>-<skill>``
8. Remove stale entries from previous runs

**Configuration (``ivpm.yaml``)**

Project-level settings under ``package.with.agents``.  Mirroring into the
tool-specific directories is *opt-out* — both ``.claude/skills/`` and
``.cursor/skills/`` are populated by default.  Set a key to ``false`` to skip
that tool:

.. code-block:: yaml

    package:
      name: my-project
      with:
        agents:
          claude: true          # default -- set false to skip .claude/skills/
          cursor: true          # default -- set false to skip .cursor/skills/
          plugins:              # Agent Plugin manifests (default: auto-probe)
            - plugins/**/plugin.json
          expand_skills: true   # default -- also link each plugin skill
          plugin_install: true  # default -- install plugins natively where supported
          mcp: false            # default -- do not wire up plugin MCP servers

.. note::

   **Behavior change.**  Earlier releases treated ``.claude/skills/`` as
   *opt-in* (created only when ``claude: true`` was set, or when ``.claude/``
   already existed).  It is now created by default.  Projects that relied on
   ``.claude/`` *not* being created must set ``claude: false`` explicitly.

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
- **Tool-specific directories**: ``.claude/skills/`` and ``.cursor/skills/`` are
  populated by default (opt-out).  Set ``claude: false`` / ``cursor: false``
  under ``package.with.agents`` to skip a given tool; an explicit ``false``
  always wins, even when the directory already exists.
- **Stale cleanup**: Removes entries from previous runs before writing new ones.
  This includes entries in a tool directory that was populated by an earlier run
  but is now disabled.

**Output:**

- ``.agents/skills/<package>`` — symlink(s) to skill directories
- ``.agents/plugins/<name>`` — symlink(s) to Agent Plugin roots
- ``.claude/skills/<package>`` — same as ``.agents/skills/``, unless ``claude: false``
- ``.claude/skills/<plugin>/`` — an installed Agent Plugin, with a generated
  ``.claude-plugin/plugin.json`` (and ``.mcp.json`` when ``mcp: true``)
- ``.cursor/skills/<package>`` — same as ``.agents/skills/``, unless ``cursor: false``
- ``.agents/data/<plugin>/`` — persistent plugin data; never removed by
  stale-entry cleanup

No per-tool *plugin* directories are created: no client reads a project-local
plugin-root directory.


.. _handler-modules:

Modules Handler (``modules``)
------------------------------

Generates ``module load`` statements for `Environment Modules
<https://modules.readthedocs.io/>`_ integration.

**Purpose**

Packages can declare an Environment Module dependency via the ``module``
source type, or carry the ``module`` content type in ``ivpm.yaml``.  The
modules handler collects these declarations, generates
``packages/modules.envrc`` with ``module load`` statements, and patches
``packages/packages.envrc`` to source it.

**Leaf phase**

Inspects every package.  Packages carrying ``ModuleTypeData`` with
``load: true`` are recorded.

**Root phase**

Writes ``packages/modules.envrc`` with one ``module load <spec>`` line per
discovered module.  The spec is a logical specifier (``gcc/15.2.0``) for a
dependency declared with ``module:``, and the resolved **absolute
modulefile path** for one declared with ``modulefile:``.  Patches
``packages/packages.envrc`` with a sentinel-wrapped ``source_env`` line.
Cleans up stale entries when no modules remain.

**Configuration (``ivpm.yaml``)**

.. code-block:: yaml

    deps:
      # logical specifier, resolved via the modules system
      - name: gcc-toolchain
        src: module
        module: "gcc/15.2.0"

      # a modulefile on disk (requires Modules 4.x or Lmod to load)
      - name: mytool
        src: module
        modulefile: etc/modulefiles/mytool/1.0

      # resolve the root but emit no 'module load' line
      - name: quiet-tool
        src: module
        module: "quiet/1.0"
        type: { module: { load: false } }

**Output:** ``packages/modules.envrc``

See :doc:`environment_modules` for the full source-type reference,
root-directory selection, and troubleshooting.


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
     - ENVIRONMENT
     - Environment file aggregation
     - ``.envrc`` / ``export.envrc``
     - Writes combined envrc
     - ``packages/packages.envrc``
   * - ``modules``
     - ENVIRONMENT
     - Environment Modules integration
     - Packages with ``ModuleTypeData``
     - Writes ``module load`` statements
     - ``packages/modules.envrc``
   * - ``node``
     - INSTALL
     - Node.js environment and package install
     - ``package.json`` / ``src: npm``
     - Synthesises ``package.json``, runs npm/pnpm/yarn, links source packages
     - ``packages/node/``
   * - ``python``
     - INSTALL
     - Python venv and package install
     - ``setup.py`` / ``pyproject.toml`` / ``src: pypi``
     - Creates venv, installs packages; queries ``agent.skills`` entry-points
     - ``packages/python/``
   * - ``agents``
     - INTEGRATE
     - Skill and Agent Plugin discovery and linking
     - ``plugin.json`` at root or under ``plugins/``; ``SKILL.md`` at root, under ``skills/``, declared paths, or ``agent.skills`` / ``agent.plugins`` entry-points
     - Creates links to skills and plugins
     - ``.agents/skills/``, ``.agents/plugins/``, ``.claude/skills/``, ``.cursor/skills/``
   * - ``dv-flow``
     - INTEGRATE
     - DV-Flow package-map generation
     - Packages with a root ``flow.yaml``
     - Writes ``dv-flow-package-map.yaml``
     - ``packages/dv-flow-package-map.yaml``
   * - ``fusesoc``
     - INTEGRATE
     - FuseSoC core library mapping
     - ``.core`` files (CAPI-2) or ``with.fusesoc.cores``
     - Writes ``fusesoc-cores.envrc``, ``fusesoc-cores.txt``; optionally updates ``fusesoc.conf``
     - ``packages/fusesoc-cores.*``


Toolchain Mode
==============

When a :doc:`shared tool directory <tool_directories>` is built with ``ivpm
install``, the deps-dir **is** the root: there is no project directory above
it. A handler that would normally write a project-scoped artifact has nowhere
correct to put it, so each handler declares whether it can run in this mode:

.. code-block:: python

    from ivpm.handlers.package_handler import PackageHandler, ToolchainSupport

    class MyHandler(PackageHandler):
        toolchain_support = ToolchainSupport.UNSUPPORTED

``SUPPORTED`` (the default)
  The handler either writes nothing outside the deps-dir, or branches
  internally on ``update_info.project_root_or_none()``.

``UNSUPPORTED``
  The handler's root phase is skipped in toolchain mode. The skip is always
  announced -- a tool tree missing a handler's output with no explanation is a
  support ticket waiting to happen:

  .. code-block:: text

      note: Skipping the 'agents' handler: its output is project-scoped and
      this is a tool directory (no project root)

``ivpm show handler <name>`` reports the declaration.

Behavior of the built-in handlers:

.. list-table::
   :header-rows: 1
   :widths: 15 20 65

   * - Handler
     - Toolchain mode
     - Behavior
   * - ``direnv``
     - supported
     - Writes ``packages.envrc`` as usual, but omits the ``IVPM_PROJECT``
       export -- there is no project root to point it at. ``IVPM_PACKAGES``
       is unaffected: it is the outdir.
   * - ``fusesoc``
     - supported
     - Still writes ``fusesoc-cores.envrc`` and ``fusesoc-cores.txt`` into the
       deps-dir. Skips the project's own ``.core`` contribution and the
       ``update-conf`` write of ``fusesoc.conf``, both of which need a project.
   * - ``agents``
     - **unsupported**
     - Every artifact it produces (``.agents/``, ``.claude/``, ``.cursor/``) is
       project-scoped, so the whole root phase is skipped.
   * - others
     - supported
     - Output lands in the deps-dir, which is the outdir.

See :doc:`extending_ivpm` for writing a handler that works in both modes.

Handler Ordering
================

Root callbacks run in an order computed from each handler's **phase** and its
relative **constraints**.  Built-in and third-party handlers are ordered
together by the same rules.

Every handler belongs to one of five ordered, barrier-separated phases:

.. code-block:: text

    PREPARE  ->  ENVIRONMENT  ->  INSTALL  ->  INTEGRATE  ->  FINALIZE

Because phases are **barriers**, all handlers in one phase finish before any
handler in the next begins -- so an ``INTEGRATE`` handler can rely on the
managed Python venv (built in ``INSTALL``) already existing.

Within or across phases, a handler can declare relative ordering with
``run_after`` / ``run_before``, each naming another handler or a phase
(``"phase:install"``).  For example, the ``modules`` handler declares
``run_after = ["direnv"]`` because it patches the ``packages.envrc`` file that
``direnv`` writes.  A constraint naming an absent handler is ignored with a
warning; a constraint that forms a cycle aborts the run with a clear error.

Handlers in the same phase with no constraint between them run in a
deterministic, reproducible order (by name).

Inspect the resolved order with:

.. code-block:: bash

    $ ivpm show handler --order

See :doc:`extending_ivpm` for how to set ``phase``, ``run_after``, and
``run_before`` on a custom handler.


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


.. _handler-content-attribution:

Attributing Content-Install Failures
====================================

A handler that hands inputs to an external installer is expected to record
where each input came from, so that a failure can be traced back to the
dependency that caused it and to the ``ivpm.yaml`` line that imported it.

Recording provenance
--------------------

Record each emitted input **at the point it is emitted**, where the
``Package`` is still in hand.  Reconstructing the mapping afterwards means
guessing:

.. code-block:: python

    from ivpm.content_attrib import OriginMap

    self._origins = OriginMap()
    ...
    self._origins.record(line, pkg, group, language="mylang", dist=dist_name)

* ``group`` identifies the batch -- typically the input file's path.  Isolation
  and reporting both work per group.
* ``dist`` is the name the *installer* will use, which is often not the IVPM
  package name.  Supplying it lets an installer that names a failure be
  matched to the package that caused it.
* ``language`` makes ``record`` read the enrollment reason off the package, so
  the reason travels with the input automatically.

Recording enrollment
--------------------

Whenever a handler decides a package carries its content, record *why*:

.. code-block:: python

    from ivpm.content_attrib import (
        ENROLLED_EXPLICIT, ENROLLED_PROBE, ENROLLED_PROVIDES,
        ENROLLED_SRC_TYPE, set_enrollment,
    )

    set_enrollment(pkg, "mylang", ENROLLED_PROBE, evidence="mylang.toml")

The reason determines how loudly a problem with that package is reported: a
package the user explicitly declared gets a fatal error, while one the handler
merely guessed at gets a note and is quietly left alone.  ``evidence`` is what
made the decision, and turns an unfalsifiable claim into one the user can
check.

Gating auto-detection
---------------------

Use ``probe_allowed(pkg, language)`` rather than testing ``pkg.pkg_type``.
``pkg_type`` is a single slot shared by every handler, so gating on it makes
one language's declaration silently switch off another's detection:

.. code-block:: python

    from ivpm.content_attrib import probe_allowed

    if probe_allowed(pkg, "mylang") and pkg.path:
        ...

Reporting a failure
-------------------

.. code-block:: python

    from ivpm.content_attrib import (
        isolate, isolation_identified_by, isolation_note,
        report_content_failure,
    )

    contributors = self._origins.by_group(group)
    iso = isolate(contributors, retry_one)
    report_content_failure(
        "mylang", iso.culprits or contributors,
        update_info.all_pkgs_by_key,
        group=group,
        installer="%s (exit %d)" % (" ".join(cmd), result.returncode),
        output=format_output_tail(result.lines),
        identified_by=isolation_identified_by(iso),
        note_text=isolation_note(iso))

``isolate`` takes a ``retry_one(origin) -> bool`` callback that installs one
input on its own.  It asks only *which* input fails, never *why*, which is what
makes it work for failure modes nobody anticipated.

.. warning::

   Parsing an installer's output to identify the culprit is acceptable **only**
   as a fast path.  It stops working the moment the installer rewords a
   message, and it says nothing at all about failures that name no package.
   Every handler must have a route to an answer that does not depend on it.

Writing Custom Handlers
=======================

To create your own handler, see :doc:`extending_ivpm` for the full API
reference, including the ``PackageHandler`` base class, activation conditions,
thread safety, progress reporting, and entry-point registration.
