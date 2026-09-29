##################################
Installing Skills: ``ivpm skills``
##################################

``ivpm skills`` installs `Agent Skills <https://agentskills.io/specification>`_
shipped by Python packages into a directory, for the AI coding agents you use.
It needs no ``ivpm.yaml``: any directory and any Python environment will do.

It finds the skills a package declares through the ``agent.skills`` entry-point
group or ``share/agent-skills/`` (specified in :doc:`agent_skills_entrypoints`),
and links or copies them into:

- ``.agents/skills/`` -- always; read by Codex and other tools
- ``.claude/skills/`` -- Claude Code
- ``.cursor/skills/`` -- Cursor

.. contents::
   :local:
   :depth: 2


Quick start
===========

Nothing installed, nothing configured:

.. code-block:: bash

    $ uvx ivpm skills install --with pssparser --all
    Python: /home/u/.cache/uv/builds-v0/.tmpX/bin/python (--with)
    Copying rather than linking: the environment was built by uv (--with).
      installed ivpm  (ep:ivpm, ivpm 2.37.0)
      installed pssparser  (ep:pssparser, pssparser 3.1.7)
      installed pssparser-api  (ep:pssparser-api, pssparser 3.1.7)
      installed pssparser-checkers  (ep:pssparser-checkers, pssparser 3.1.7)
    4 skill(s) in .agents/skills, .claude/skills, .cursor/skills (copies)

``--with`` has uv build a throwaway environment containing ``pssparser``; the
skills are copied out of it.  The choice is recorded in
``.agents/ivpm-skills.json``.  Commit that file, and a teammate re-creates the
same skills with:

.. code-block:: bash

    $ uvx ivpm skills sync

IVPM's own skill is always offered, so ``--all`` includes it.

With packages already installed in a virtual environment:

.. code-block:: bash

    $ source .venv/bin/activate
    $ pip install pssparser
    $ ivpm skills list
    $ ivpm skills install pssparser-api --agent claude


Commands
========

.. code-block:: text

    ivpm skills list      [SELECTOR...]
    ivpm skills install   SELECTOR... | --all  [--agent LIST] [--copy | --link] [--as NAME]
    ivpm skills uninstall SELECTOR... | --all
    ivpm skills sync      [--upgrade]
    ivpm skills status

    Common options:
      -d, --dir DIR     Target directory (default: current directory)
      --python PATH     Interpreter whose environment is queried
      --with SPEC       Have uv build the environment from this package spec
                        (repeatable; implies copying)
      --offline         With --with: do not access the network
      --json            JSON output
      --no-rich         Plain-text output

``add`` and ``remove`` are accepted as aliases of ``install`` and ``uninstall``.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Command
     - Does
   * - ``list``
     - Shows the skills the environment offers: name, provider (``ep:<entry
       point>`` or ``share``), distribution and version, and where each is
       installed in the directory and by whom -- ``ivpm skills``,
       ``ivpm update``, or unmanaged (made by hand).
   * - ``install``
     - Records the selected skills in the state file and installs them.
       ``--agent`` takes a comma list of ``agents``, ``claude``, ``cursor`` or
       ``all`` (the default); ``.agents/skills/`` is always written.  The
       choice is remembered.  ``--as NAME`` installs a single skill under
       another name, to resolve a clash.
   * - ``uninstall``
     - Removes selections and exactly the entries installed for them.  Needs
       no environment.
   * - ``sync``
     - Re-resolves every recorded selection against the current environment
       and re-installs it.  Run it after ``pip install -U``, after cloning a
       repository that has the state file, or after switching environments.
       Reports version changes.  A selection the environment no longer
       provides is reported and left as it was.
   * - ``status``
     - Shows the selections, what is installed for which agent, and problems:
       a selection no longer provided, a dangling link, an entry that has gone
       missing, a stale copy (its source changed since it was copied), or a
       changed distribution version.


.. _skills-selectors:

Selectors
=========

A selector picks skills by any of (shell-style globs allowed everywhere):

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Selector
     - Matches
   * - ``pssparser-api``
     - the skill with that name (or the installed name, for ``uninstall``)
   * - ``pssparser``
     - also every skill the entry point named ``pssparser`` provides
   * - ``pssparser/pssparser-api``
     - fully qualified: one skill of one provider (``share/<name>`` for
       ``share/agent-skills/``)
   * - ``dist:pssparser``
     - every skill from that distribution
   * - ``'pssparser-*'``
     - a glob over names

A selector that matches nothing is an error.  So is a plain name that two
providers both ship a skill under: use the qualified form, and ``--as`` to
install the second one under another name.


Choosing the environment
========================

The first rule that applies wins; ``list`` and ``status`` print which one.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Rule
     - Environment
   * - ``--with SPEC``
     - built by ``uv run --no-project --isolated --with SPEC ...``; your
       ``PYTHONPATH`` and ``VIRTUAL_ENV`` are not passed to it
   * - ``--python PATH``
     - that interpreter's
   * - running under ``uvx --with PKG``
     - IVPM's own, when it has skills from packages other than IVPM -- even
       if the shell has another venv activated
   * - ``DIR`` is an IVPM project
     - its managed venv (``packages/python``)
   * - ``$VIRTUAL_ENV``
     - the activated venv
   * - ``DIR/.venv``
     - that venv
   * - otherwise
     - the interpreter running IVPM

When the state file records ``--with`` specs, ``install``, ``sync``,
``status`` and ``list`` rebuild that environment unless ``--python`` is given.
After ``uvx --with pkg ivpm skills install``, the distributions providing the
chosen skills are recorded as the ``--with`` specs, so ``sync`` can rebuild it
too.

uv is found through ``$UV`` (which uv sets for the processes it runs), then
``PATH``, then ``python -m uv``.


Link or copy
============

**Link** (the default) makes absolute symlinks into the environment's
``site-packages``.  They are cheap and always current after
``pip install -U``, but break if the environment moves and mean nothing on
another machine.  Recommended with ``.agents/skills/``, ``.claude/skills/`` and
``.cursor/skills/`` in ``.gitignore`` and ``.agents/ivpm-skills.json``
committed; teammates run ``ivpm skills sync``.

**Copy** (``--copy``) is self-contained, can be committed, and works where
symlinks don't.  It goes stale on upgrade; ``status`` detects that from a
content hash recorded at install time.  The whole skill directory is copied
except ``__pycache__/``, ``*.pyc`` and ``.git``.

IVPM copies even when link is asked for, and says why, when:

- the environment was built by uv (``--with``), or the skills live in uv's
  cache -- a link there would dangle after ``uv cache prune``;
- the filesystem does not support symlinks.

The mode is a property of the whole state file: ``install --copy`` switches
every recorded skill to copies.


The state file
==============

``.agents/ivpm-skills.json``:

.. code-block:: json

    {
      "version": 1,
      "agents": ["agents", "claude", "cursor"],
      "mode": "link",
      "with": ["pssparser"],
      "selections": [
        {"provider": "ep:pssparser-api", "skill": "pssparser-api", "dist": "pssparser"},
        {"provider": "share", "skill": "polars", "dist": "agent-skill-polars"}
      ],
      "installed": {
        "agents_skills": [{"name": "pssparser-api", "mode": "copy", "hash": "..."}],
        "claude_skills": [{"name": "pssparser-api", "mode": "copy", "hash": "..."}]
      },
      "resolved": {
        "python": "/home/u/proj/.venv/bin/python",
        "dists": {"pssparser": "3.1.7"}
      }
    }

- ``selections`` and ``with`` hold no paths: they are what a teammate needs.
- ``installed`` is what ``ivpm skills`` wrote and may remove.
- ``resolved`` is informational and machine-specific (``status`` and ``sync``
  use it to report version changes).


.. _skills-coexistence:

In an IVPM project
==================

``ivpm update`` already links every skill in a project's managed venv (see
:ref:`handler-agents`), so both can write into the same directories.  Each
removes only what its own state says it wrote, and neither replaces an entry
the other owns:

- ``ivpm update`` treats names in ``.agents/ivpm-skills.json`` as reserved.
  It does not link a skill ``ivpm skills`` already installed, and a different
  skill that wants a reserved name gets its next candidate name.
- ``ivpm skills install`` refuses a name ``ivpm update`` owns, and points at
  ``with.agents.entrypoints``.
- Entries neither made are never touched; ``install`` refuses to overwrite
  one and suggests ``--as``.

To leave a project's venv skills entirely to ``ivpm skills``, set:

.. code-block:: yaml

    package:
      with:
        agents:
          entrypoints: false        # or a list of selectors to keep some

and the two never overlap.
