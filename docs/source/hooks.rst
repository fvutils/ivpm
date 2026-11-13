#############
Project Hooks
#############

A **hook** is code that IVPM runs at a fixed point in its own lifecycle,
declared in the project's ``ivpm.yaml``. The common use is a step that needs
the freshly built environment, for example generating the root project's
``.egg-info`` once its dependencies are installed.

.. code-block:: yaml

    package:
      name: my-project
      hooks:
        - name: egg-info
          event: post-update
          python: |
            from setuptools import setup
            setup(script_args=["egg_info", "-e", "src"])

Events
======

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Event
     - When it runs
   * - ``post-update``
     - After ``ivpm update`` has fetched every package and the handlers have
       finished (Python and Node installation, envrc generation, tool
       integration). ``ivpm clone`` runs ``update``, so it runs these too.
       ``ivpm sync`` does not.

Hook entries
============

``hooks:`` is a list. Each entry has these keys:

``event`` (required)
    The event that runs the hook, or a list of them.

``name`` (optional)
    A label, shown in progress output and in error messages. Use letters,
    digits, ``.``, ``_`` and ``-``. Names must be unique, including across
    files merged with ``include:``. A hook without a name is shown as
    ``post-update hook #1``, ``#2``, and so on, by its position among that
    event's hooks.

``python`` (required)
    Python source code. It is not a shell command.

An unknown key, an unknown event, or a missing ``python`` is reported when
``ivpm.yaml`` is read, with the file and line.

How a ``python`` hook runs
==========================

* **Interpreter.** The project venv's interpreter runs the code as
  ``packages/python/bin/python -c <source>``. No shell is involved, and the
  system Python is never used.
* **Working directory.** The project root.
* **Environment.** Your environment, with the venv active: ``VIRTUAL_ENV`` is
  set and the venv's ``bin`` comes first on ``PATH``, so a hook that starts
  ``python`` or ``pip`` gets the venv's. ``IVPM_PROJECT`` and
  ``IVPM_PACKAGES`` are set as in ``packages.envrc``.
* **Indentation.** Indentation shared by every line is removed before the
  code runs, so the source can be indented to fit the YAML around it. The
  indentation of lines relative to each other is kept.
* **Venv.** A project with a ``python`` hook always gets a venv, even if it
  has no Python dependencies. If Python installation is turned off
  (``with: python: venv: false``, ``--py-skip-install``) and there is no venv
  left from an earlier update, the update fails and says why. Hooks never
  fall back to the system Python.

The hook's imports must be installed in the venv. List them as dependencies:
IVPM does not guess what a hook needs.

Order and failure
=================

Hooks run one at a time, in the order they are written. With ``include:``,
the including file's hooks run first, then the included file's.

A hook that exits with a non-zero status, including from an uncaught
exception, fails the update. The rest of that event's hooks do not run. The
error names the hook and its location in ``ivpm.yaml``, and quotes the end of
its output.

A hook's output is printed as it runs, unless the full-screen progress
display is active. It is always captured, and ``ivpm --log-level DEBUG
update`` logs all of it.

To skip hooks for one run, use ``--no-hooks``:

.. code-block:: bash

    $ ivpm update --no-hooks
    $ ivpm clone <url> --no-hooks

Only the root project's hooks run
=================================

Hooks run only from the ``ivpm.yaml`` of the project you are updating. Hooks
in a dependency's ``ivpm.yaml`` are ignored, so fetching a dependency never
runs code its author chose. Composer applies the same rule to its scripts.

Hooks also do not run:

* for ``ivpm update --from <url>``, where the manifest is fetched and is not
  the project's own;
* for ``ivpm install``, which builds a tool directory with no project root.

Variables
=========

``${{name}}`` references in hook source are expanded like anywhere else in
``ivpm.yaml`` (see :doc:`variables`), and an undeclared name is an error. To
put a literal ``${{`` in Python code, write ``$${{``.

Recipe: ``.egg-info`` for the root project
==========================================

IVPM installs a project's dependencies into the venv, but not the project
itself. Tools that read package metadata, for example entry-point discovery
through ``importlib.metadata``, then cannot see it. A ``post-update`` hook can
generate the metadata in place:

.. code-block:: yaml

    package:
      name: my-project
      dep-sets:
        - name: default-dev
          deps:
            - name: setuptools
              src: pypi
      hooks:
        - name: egg-info
          event: post-update
          python: |
            from setuptools import setup
            setup(script_args=["egg_info", "-e", "src"])

``script_args`` replaces the command line that ``setup()`` would otherwise
read, so this is the same as running ``python setup.py egg_info -e src``.
``setup()`` reads ``pyproject.toml`` from the working directory, which for a
hook is the project root. ``-e src`` puts the ``.egg-info`` directory next to
the package sources in a ``src/`` layout.
