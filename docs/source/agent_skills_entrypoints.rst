########################################
Shipping Agent Skills in Python Packages
########################################

This page specifies how a Python distribution declares the `Agent Skills
<https://agentskills.io/specification>`_ it ships, so that any tool -- IVPM is
one -- can find them in an installed environment and hand them to an AI coding
agent.  It is written to stand on its own: nothing here depends on IVPM.

Two mechanisms are defined.  The ``agent.skills`` entry-point group is the
primary one; ``share/agent-skills/`` is a code-free secondary one that other
tools (pixi-skills, conda skill packages) already use.

.. contents::
   :local:
   :depth: 2


The ``agent.skills`` entry-point group
======================================

A distribution declares skills by registering one or more `entry points
<https://packaging.python.org/en/latest/specifications/entry-points/>`_ in the
``agent.skills`` group:

.. code-block:: toml

    [project.entry-points."agent.skills"]
    mypkg = "mypkg.skills:get_skill_dirs"

Rules for publishers
--------------------

1. The object an entry point names **should** be a callable taking no
   arguments.  A plain string or ``os.PathLike`` is accepted and treated as a
   single path.
2. The callable returns a path, or an iterable of paths.  Each path is an
   **absolute** directory on the real filesystem that contains a
   ``SKILL.md``.
3. ``SKILL.md`` begins with YAML frontmatter with a non-empty ``name`` and
   ``description``, and follows the Agent Skills specification: the ``name``
   is 1-64 characters of lowercase ``a-z``, ``0-9`` and single inner hyphens,
   and **matches the name of the skill's directory**.
4. The callable is cheap and side-effect free: no network, no writes, no
   output.  Consumers call it in a subprocess with a timeout.
5. The entry-point **name** identifies the provider -- usually the
   distribution or skill-set name.  It is *not* the skill name: that comes
   from the frontmatter.  **One entry point returning several skills is the
   intended shape.**
6. **Discovery imports your package.**  Loading ``mypkg.skills:get_skill_dirs``
   runs ``mypkg/__init__.py`` first.  Keep that import cheap and free of native
   code -- for example with a :pep:`562` module ``__getattr__`` that imports
   submodules on first use.  Otherwise discovery is slow, and when a native
   library fails to load the skill silently disappears, which is exactly when
   an agent needs it most.
7. **Return the same directory every route finds.**  In an editable install,
   return the directory in the source tree (``<repo>/skills/<name>``) that a
   tool scanning the repository would also find, so the two deduplicate by
   real path rather than appearing twice.
8. **Only register what you mean to ship.**  A skill for working *on* the
   package (contributor instructions, say) belongs in the repository, not in
   an entry point or the wheel.

Reference implementation
------------------------

Works for wheels and editable installs:

.. code-block:: python

    # mypkg/skills.py
    import os
    from typing import List

    def get_skill_dirs() -> List[str]:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(here, "share", "skills")
        return sorted(
            os.path.join(root, d) for d in os.listdir(root)
            if os.path.isfile(os.path.join(root, d, "SKILL.md")))

The ``SKILL.md`` trees must be package data:

.. code-block:: toml

    [tool.setuptools.package-data]
    mypkg = ["share/skills/**/*"]

``__file__`` is used rather than ``importlib.resources.files()`` because a
consumer needs a real directory to link to; zipped installs are not supported.

.. note::

   Tools that predate this rule name an entry-point skill after the entry
   point rather than its frontmatter (IVPM before 2.37 did).  A publisher that
   must support them registers **one entry point per skill, named after the
   skill**.  Such entry points and a single many-skill one deduplicate by real
   path, so both can be registered during a transition.

Rules for consumers
-------------------

- Do not import entry points in your own interpreter.  Query the target
  environment's interpreter in a subprocess, with a timeout, and capture its
  output: a package may print, need native libraries, or target a different
  Python version.
- Treat each entry point on its own.  A return value that is not a path or
  an iterable of paths, a path that is not a directory, or a missing or
  invalid ``SKILL.md`` is an error *for that entry point*: warn and skip it,
  never fail the whole run.  (The group name is generic enough that at least
  one unrelated package registers a class in it.)
- Name an installed skill directory after the skill's frontmatter ``name``.
- Also accept the deprecated ``ivpm.skill`` group; when a distribution
  registers the same name in both, ``agent.skills`` wins.
- Python 3.9's ``importlib.metadata.entry_points()`` takes no arguments and
  returns a group-to-list mapping; select the group by hand there.

IVPM's query script, ``ivpm/agent_skills/_env_query.py``, is a dependency-free
consumer implementation of all of the above that can be run with
``python -c``.


The ``agent.plugins`` entry-point group
=======================================

The same shape for `Agent Plugins <https://agent-plugins.org/specification>`_:
an entry point returns a plugin root directory, or the path of its
``plugin.json``.  See :doc:`agent_plugins`.


``share/agent-skills/``
=======================

A distribution may instead install each skill to
``<prefix>/share/agent-skills/<name>/SKILL.md``, where ``<prefix>`` is the
environment's ``sys.prefix``.  A wheel does this through its
``<dist>-<version>.data/data/share/agent-skills/<name>/`` directory; a conda
package installs there directly.

Consumers attribute such a directory to a distribution by searching installed
distributions' ``RECORD`` files for it.

.. warning::

   Editable installs do not install data files, so a package under
   development will not expose ``share/agent-skills/`` skills.  Prefer the
   entry-point mechanism while developing, or use both.


Consumers
=========

- IVPM's ``agents`` handler links every skill in a project's managed venv
  during ``ivpm update`` (see :ref:`handler-agents`).
- ``ivpm skills`` installs a chosen set into any directory, from any
  environment (see :doc:`agent_skills_command`).
