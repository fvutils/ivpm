.. _plugin-marketplaces:

###################
Plugin Marketplaces
###################

A *plugin marketplace* is a catalog, ``marketplace.json``, that lists plugins
and says where each one's source is.  Claude Code defined the format, and
GitHub Copilot, VS Code and Codex read it too.  IVPM can install plugins from a
marketplace **by name**, as ordinary dependencies: each plugin is fetched
through IVPM's cache, pinned in ``package-lock.json``, and projected into every
agent's directories like any other plugin (see :doc:`agent_plugins`).

IVPM does not call ``claude plugin install`` or any other agent's installer.
Those install into a per-user cache under your home directory, cannot be
pinned, and serve one agent each.

.. contents::
   :local:
   :depth: 2


Installing plugins from a marketplace
=====================================

.. code-block:: yaml

    package:
      name: my-project
      dep-sets:
        - name: default-dev
          deps:
            - name: claude-official
              src: marketplace
              url: anthropics/claude-plugins-official
              plugins:
                - code-review
                - "lsp-*"

``plugins`` is required and lists plugin names or glob patterns.  A name that
matches nothing is an error, with the closest match suggested.  Installing a
whole marketplace takes an explicit ``plugins: ["*"]``.

After ``ivpm update``:

.. code-block:: text

    .agents/plugins/code-review         the plugin, whole
    .agents/skills/code-review-<skill>  each skill, for every agent
    .claude/skills/code-review/         the plugin, installed for Claude Code

The ``mcp`` and ``executables`` settings apply to marketplace plugins exactly
as to any other dependency plugin: MCP servers, hooks and ``bin/`` stay out
unless you enable them.


Where the marketplace comes from
================================

``url`` accepts:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - ``url``
     - The marketplace is
   * - ``owner/repo``
     - a GitHub repository
   * - a git URL (``https://…``, ``git@…``, ``….git``)
     - a git repository, fetched and locked like a ``src: git`` dependency
   * - a local directory, or a path to a ``marketplace.json``
     - read in place, like a ``src: dir`` dependency
   * - ``https://…/marketplace.json``
     - a catalog downloaded on its own

A git marketplace also accepts ``branch``, ``tag`` and ``commit``.

Inside a repository or directory the catalog is looked for at
``.claude-plugin/marketplace.json``, then ``.agents/plugins/marketplace.json``,
then ``marketplace.json``.  ``marketplace-file: path/to/catalog.json`` names it
explicitly.

A catalog downloaded by URL has no repository around it, so it can only
supply plugins with remote sources (see below).  If a selected plugin lives
inside the marketplace, point ``url`` at the repository instead.


How plugin sources are fetched
==============================

Each marketplace entry says where its plugin comes from.  IVPM maps each kind
of source to a dependency source it already has:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Entry source
     - Fetched as
   * - relative path (``"./plugins/x"``)
     - part of the marketplace itself; nothing extra is fetched
   * - ``github``
     - a git dependency on ``https://github.com/<repo>.git``
   * - ``url``
     - a git dependency on that URL
   * - ``git-subdir``
     - a git dependency, of which only the named subdirectory is used
   * - ``archive``
     - an ``http`` dependency, checked against the entry's ``sha256``
   * - ``npm``
     - not supported yet; selecting one is an error
   * - ``command``
     - never supported: it runs a program to produce the plugin

An entry's ``sha`` pins the commit.  A ``ref`` becomes ``tag:`` or
``branch:`` depending on which the remote has.

Each plugin fetched separately is named after its marketplace entry.  If your
project declares a dependency with the same name, yours wins -- which is how
you pin or patch one plugin from a marketplace.

``metadata.pluginRoot`` in the catalog prefixes bare relative names, as in
Claude Code.


Manifests supplied by the marketplace
-------------------------------------

An entry may carry any manifest field.  With ``strict: true`` (the default)
the plugin's own manifest wins, and the entry only fills in a missing
``description`` or ``version``.  With ``strict: false`` the entry *is* the
manifest, and the plugin needs none of its own; IVPM writes the manifest into
the plugin it installs for Claude Code.


Browsing a marketplace
======================

.. code-block:: bash

    ivpm show plugins --from anthropics/claude-plugins-official
    ivpm show plugins --from ./my-marketplace --json

This fetches only the catalog -- a shallow clone, or one download -- and
lists every entry with its source and whether IVPM can install it.  Nothing is
written to the workspace.


Lock file and reproduction
==========================

* A git marketplace is locked by commit, like any git dependency.  A catalog
  downloaded by URL is recorded under ``ivpm_sources`` with its fingerprint.
* Each plugin fetched separately gets its own entry, with its commit (or
  checksum) and ``from_marketplace`` naming the marketplace it came from.
* Every entry records the ``agents`` configuration it was resolved with, so
  ``ivpm update --lock-file`` reproduces exactly the selected plugins
  without re-reading the catalog.

``ivpm update`` against an existing workspace uses the locked marketplace
commit, and so the catalog as it was when locked.  Refreshing the marketplace
refreshes the selection.


Trust
=====

A marketplace decides where plugin code comes from.  Selecting a plugin by
name trusts the marketplace's answer for that name, today and whenever you
refresh.  The lock pins the answer between refreshes, and
``ivpm show plugins --from`` shows every entry's source before anything is
fetched.  This is the same trust you place in a git dependency whose own
``ivpm.yaml`` names further dependencies.
