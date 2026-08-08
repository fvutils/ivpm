#############
Agent Plugins
#############

IVPM consumes `Agent Plugins <https://agent-plugins.org/specification>`_, the
vendor-neutral standard for packaging Agent Skills and MCP server
configuration into a portable unit.  A plugin travelling as an IVPM dependency
is discovered, validated, and projected into your workspace in whatever form
each AI coding tool can actually consume.

Agent Plugins 1.0 is governed independently, with maintainers from Amazon,
Cursor, Microsoft, OpenAI, and Vercel.

.. contents::
   :local:
   :depth: 2


What a plugin is
================

A plugin is a directory:

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Path
     - Required
     - Contents
   * - ``plugin.json``
     - yes
     - the manifest
   * - ``skills/``
     - no
     - one subdirectory per skill, each with ``SKILL.md``
   * - ``mcp.json``
     - no
     - MCP server configuration
   * - ``<reverse.domain>/``
     - no
     - client-namespaced extension directories

A minimal manifest:

.. code-block:: json

    {
      "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
      "name": "my-plugin",
      "version": "1.0.0",
      "description": "What this plugin does."
    }

``$schema`` and ``name`` are the only required fields.  ``name`` is 1--64
characters of lowercase letters, digits, ``-`` and ``.``; it must start and end
with a letter or digit and may not contain ``--`` or ``..``.

The manifest is *declarative only*.  It cannot relocate components and cannot
declare them inline: ``skills/`` means ``skills/``.


Consuming plugins
=================

Discovery order
---------------

Plugins are looked for before loose skills, because a plugin is a more
informative answer: it names its own components rather than leaving IVPM to
infer them from the directory layout.

1. **Consumer dep-entry override** — the importing project names the manifests:

   .. code-block:: yaml

       deps:
         - name: my-package
           url: https://github.com/org/my-package.git
           agents:
             plugins:
               - plugins/**/plugin.json

2. **Package-declared paths** — the package names them itself:

   .. code-block:: yaml

       package:
         name: my-package
         with:
           agents:
             plugins:
               - plugins/**/plugin.json

3. **Auto-probe** — nothing configured, so two conventional locations are
   checked: ``plugin.json`` at the package root, then
   ``plugins/*/plugin.json``.

4. **Python entry-points** — a package installed into the managed virtual
   environment may register plugins under the ``agent.plugins`` group.  See
   :ref:`agent-plugins-authoring` below.

Patterns name manifests, not directories
----------------------------------------

A ``plugins:`` pattern matches ``plugin.json`` **files**, exactly as a
``skills:`` pattern matches ``SKILL.md`` files.  The directory containing a
matched manifest is the plugin root.  This is the canonical spelling, and it is
what makes ``plugins/**/plugin.json`` work without special cases.

A pattern that matches a directory containing ``plugin.json`` is also accepted,
so an entry-point returning a package-data directory needs no change.

A configured path must resolve *inside* the package it describes.  A dep entry
pointing at ``../../../elsewhere/plugin.json`` is rejected with a warning.


What gets created
=================

IVPM creates one plugin-root directory of its own, and otherwise projects
plugins into each tool in the form that tool understands.

.. list-table::
   :header-rows: 1
   :widths: 26 18 56

   * - Location
     - Strategy
     - Contents
   * - ``.agents/plugins/<name>``
     - —
     - link to each plugin, whole and unmodified
   * - ``.agents/skills/``
     - unbundle
     - ``<plugin>-<skill>`` per skill
   * - ``.claude/skills/<name>/``
     - install
     - the plugin, with a ``.claude-plugin/plugin.json`` manifest
   * - ``.cursor/skills/``
     - unbundle
     - ``<plugin>-<skill>`` per skill

Link names for plugins come from the manifest ``name`` field, which the
specification constrains to a filesystem-safe charset.  Two plugins claiming
the same name are disambiguated by the IVPM package that supplied them
(``<package>-<name>``).

Unbundle
--------

Most tools understand individual skills but have no notion of a plugin, so
each of a plugin's skills is linked separately as ``<plugin>-<skill>``.

**Codex needs no mirror of its own.**  It scans ``.agents/skills`` in every
directory from the working directory up to the repository root, which is
exactly what IVPM writes — so the neutral directory *is* the Codex
integration.

Install
-------

Claude Code has a real plugin mechanism, so it receives the plugin whole.  Any
directory under a skills directory containing ``.claude-plugin/plugin.json``
loads as ``<name>@skills-dir``, and Claude Code namespaces the plugin's skills
itself as ``/<plugin>:<skill>``.

IVPM materializes ``.claude/skills/<name>/`` as a thin shell: every top-level
entry of the real plugin is linked through, and only the manifest is
generated.  The generated manifest is a **verbatim copy** of the Agent Plugins
``plugin.json`` — the field names coincide, and Claude Code documents that it
ignores unrecognized top-level fields, so no rewriting is needed.

Because the plugin's components are linked rather than copied, edits to a
dependency's skills are picked up without re-running ``ivpm update``.

.. note::

   Project-scope plugins load only after you accept the workspace trust dialog,
   and only from the ``.claude/skills/`` of the directory where Claude Code
   starts — they do not walk up to the repository root the way plain skills do.
   Launch from the repository root, or run ``/reload-plugins`` after changing
   directories.

The two strategies are mutually exclusive per tool.  A plugin installed whole
into ``.claude/skills/`` does **not** also have its skills unbundled there;
doing both would surface every skill twice, once namespaced and once bare.

.. note::

   No per-tool *plugin* directories are created.  No client reads a
   project-local plugin-root directory: Claude Code uses ``--plugin-dir``,
   marketplaces, or the skills-directory mechanism above; VS Code and Copilot
   use a settings key rather than a directory; Cursor has no such convention.


Validation
==========

``plugin.json`` is a heavily overloaded filename, so IVPM decides in layers:

1. the file is named ``plugin.json``, parses as JSON, and is an object;
2. its ``$schema`` is exactly
   ``https://agent-plugins.org/schemas/<version>/plugin.schema.json`` — this is
   the type tag;
3. it satisfies the schema for that version.

The distinction between layers 2 and 3 is the one you will notice:

* An **unrelated** ``plugin.json`` — a Grafana datasource manifest, a Backstage
  descriptor — is silently ignored.  Auto-probe encounters these routinely and
  saying so would be noise.
* A **malformed Agent Plugins** manifest warns and is skipped.  You asked for a
  plugin and did not get one.

A manifest naming a *pattern you configured* always warns when it is not a
plugin: you meant it, so silence would be unhelpful.

Failure is isolated to the smallest affected unit, as the specification
requires: unknown top-level fields are reported and ignored, a malformed
optional field is dropped while its siblings survive, a skill directory
without ``SKILL.md`` is skipped while its siblings load, and only ``$schema``
or ``name`` problems reject the whole plugin.

Diagnostic codes
----------------

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Code
     - Meaning
   * - ``manifest.not-a-plugin``
     - no recognized ``$schema``; not an Agent Plugins manifest
   * - ``manifest.unsupported-version``
     - an Agent Plugins manifest for a spec version this build cannot read
   * - ``manifest.unknown-key``
     - unknown top-level field, reported and ignored
   * - ``name.charset`` / ``name.length`` / ``name.type``
     - ``name`` violates the specification's rules
   * - ``field.invalid``
     - a malformed optional field was dropped
   * - ``skills.not-a-directory``
     - ``skills`` exists but is not a directory
   * - ``skills.no-skill-md``
     - a ``skills/`` subdirectory has no ``SKILL.md``
   * - ``skills.escapes-root``
     - a skill resolves outside the plugin root
   * - ``plugins.no-match``
     - a configured pattern matched nothing
   * - ``plugins.escapes-package``
     - a configured path resolves outside the package
   * - ``mcp.version-skew``
     - ``mcp.json`` and ``plugin.json`` target different spec versions
   * - ``server.*``
     - a single MCP server was rejected; see :ref:`agent-plugins-mcp`

Inspecting and checking
-----------------------

.. code-block:: bash

    ivpm show plugins                 # what this project has
    ivpm show plugins my-plugin       # detail for one
    ivpm show plugins --check ./path  # validate a plugin or manifest
    ivpm show plugins --mcp           # what MCP servers would be wired up

``--check`` exits non-zero when anything needs fixing, warnings included, so it
works as a conformance gate in CI.

``ivpm show plugins --check`` and ``claude plugin validate`` cover different
ground: the former validates components (skills, MCP servers) and the latter
validates the Claude Code manifest.  Running both catches more than either.


.. _agent-plugins-mcp:

MCP servers
===========

A plugin may declare MCP servers in ``mcp.json``.  **IVPM does not wire these
up unless you ask it to.**

A dependency's MCP configuration names an executable that a tool will later
launch, which is a materially larger blast radius than linking a Markdown
file, and it arrives through the transitive dependency graph.  So:

.. code-block:: yaml

    package:
      name: my-project
      with:
        agents:
          mcp: true        # default false

Review before enabling:

.. code-block:: bash

    ivpm show plugins --mcp

That command prints each server's transport, command, arguments, and the
**names** of its environment variables.  Values are never printed — they
routinely carry tokens, and this output should be safe to paste into an issue.

With ``mcp: true``, an installed plugin gets a translated ``.mcp.json``.  Only
the spelling differs between the two formats:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Agent Plugins
     - Claude Code
   * - ``mcp.json``
     - ``.mcp.json``
   * - ``${PLUGIN_ROOT}``, ``./``-relative paths
     - ``${CLAUDE_PLUGIN_ROOT}``
   * - ``${PLUGIN_DATA}``
     - resolved to ``.agents/data/<plugin>/``

``.agents/data/<plugin>/`` is the plugin's persistent data directory.  The
specification requires it to survive updates, so IVPM never removes it during
stale-entry cleanup.

Claude Code applies its own per-server approval to a project-scope plugin's
MCP servers, the same gate a project ``.mcp.json`` goes through.  Enabling
``mcp: true`` in IVPM makes the servers available to be approved; it does not
approve them.


.. _agent-plugins-authoring:

Authoring a plugin
==================

Layout
------

.. code-block:: text

    my-plugin/
    ├── plugin.json
    ├── mcp.json            (optional)
    └── skills/
        ├── code-review/
        │   └── SKILL.md
        └── refactor/
            └── SKILL.md

Only *immediate* subdirectories of ``skills/`` that contain a regular
``SKILL.md`` are skills.  ``skills/outer/inner/SKILL.md`` is not one.

Check it before publishing:

.. code-block:: bash

    ivpm show plugins --check ./my-plugin

Publishing from a Python package
--------------------------------

Register the plugin root under the ``agent.plugins`` entry-point group:

.. code-block:: toml

    [project.entry-points."agent.plugins"]
    my-plugin = "mypkg.plugins:get_plugin_dir"

.. code-block:: python

    import importlib.resources

    def get_plugin_dir() -> str:
        """Return the plugin root, or the path to its plugin.json."""
        return str(importlib.resources.files("mypkg") / "plugin")

The callable may return a single path or a list, and each may be a plugin root
**or** a path to its ``plugin.json``.

IVPM extension namespace
------------------------

IVPM-specific manifest data belongs under the ``io.fvutils.ivpm`` key of
``extensions``, never as an unknown top-level field:

.. code-block:: json

    {
      "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
      "name": "my-plugin",
      "extensions": {
        "io.fvutils.ivpm": { "package": "my-ivpm-package" }
      }
    }

Clients ignore namespaces they do not implement, and never inspect their
contents.


Configuration reference
=======================

All keys live under ``package.with.agents``.

.. list-table::
   :header-rows: 1
   :widths: 22 14 64

   * - Key
     - Default
     - Meaning
   * - ``plugins``
     - *(auto-probe)*
     - glob patterns matching ``plugin.json`` files
   * - ``expand_skills``
     - ``true``
     - also link each plugin skill individually
   * - ``plugin_install``
     - ``true``
     - install plugins natively where the tool supports it; ``false`` unbundles everywhere
   * - ``mcp``
     - ``false``
     - aggregate plugin MCP configuration into the workspace
   * - ``claude``
     - ``true``
     - populate ``.claude/skills/``
   * - ``cursor``
     - ``true``
     - populate ``.cursor/skills/``

.. code-block:: yaml

    package:
      name: my-project
      with:
        agents:
          plugins:
            - plugins/**/plugin.json
          expand_skills: true
          plugin_install: true
          mcp: false


Relationship to the skills mechanism
====================================

The existing skills mechanism (:ref:`handler-agents`) is **not deprecated**.
A package that ships loose ``SKILL.md`` files continues to work exactly as
before, and the ``agent.skills`` entry-point group is still supported.

Use a plugin when you want a versioned, named unit — one that carries MCP
servers, keeps its skills namespaced in tools that support it, and is portable
to clients that never heard of IVPM.  Use loose skills when you have a skill
or two and no packaging concerns.

A skill directory inside a discovered plugin is never *also* linked as a loose
skill: the plugin owns it, names it, and decides how it is projected.
