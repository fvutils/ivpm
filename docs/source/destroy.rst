#####################
Destroying Workspaces
#####################

Overview
========

``ivpm destroy`` tears down what ``ivpm update`` (and ``ivpm clone``)
materialized: the imported dependencies under the deps directory, the virtual
environment, the lock/state files — and, optionally, the root project itself.

It is the inverse of ``clone``/``update``. Its value over a plain ``rm -rf`` is
threefold, and none of these is achievable with a recursive delete:

#. **A safety gate.** It refuses to delete imports that contain unrecoverable
   local work — uncommitted edits, untracked files, unpushed commits, stashes,
   a local-only branch, or a drifted patched tree — unless ``--force`` is given.
   When it refuses, it reports *exactly which packages block the operation and
   why*.
#. **Source-specific teardown.** Some sources hold state *outside* their package
   directory, or hold their content as **read-only**. A cache-backed dependency
   is a symlink into a shared, write-protected cache; a deps-source dependency is
   a symlink into another workspace's tree. ``destroy`` unlinks these rather than
   recursing into (and destroying) a tree other workspaces share.
#. **A choice of blast radius.** ``destroy`` can remove just the imports and
   leave the root project intact (the common "reset my deps" case), or remove the
   whole workspace including the root.

Two Modes
=========

.. list-table::
   :header-rows: 1
   :widths: 18 30 52

   * - Mode
     - Invocation
     - Removes
   * - **full** (default)
     - ``ivpm destroy <wsdir>``
     - imports, venv, lock/state, the deps directory, **and the root project
       tree**
   * - **deps-only**
     - ``ivpm destroy --deps-only``
     - imports, venv, ``package-lock.json``, ``ivpm.json`` — **keeps** the root
       project and ``ivpm.yaml``

**Full destroy is the default** and is the headline behavior — "remove a root
project and all its imports." Because it deletes the ground you may be standing
on, it requires an **explicit target directory** and refuses to run against the
current directory or an ancestor of it. The root tree is held to the **same
gate** as imports — a root with unpushed commits blocks a full destroy too.

**Deps-only** removes the imports but leaves the root project (its ``ivpm.yaml``,
source, and git history) intact — the inverse of ``update``. Because it preserves
the root, it is safe to run *in place* and so does not require an explicit target.

.. code-block:: bash

    # Reset just the dependencies of the current workspace
    $ ivpm destroy --deps-only

    # Remove an entire cloned workspace
    $ ivpm destroy ../scratch-workspace

The Safety Gate
===============

Before removing anything, ``destroy`` asks **each package's own source provider**
whether it holds work that would be lost. The gate is *delegated*: ``destroy``
contains no git-specific logic, so a third-party source (Subversion, Mercurial, a
corporate VCS) participates correctly with no change to ``destroy``.

For git packages, the following block removal:

* **modified** — tracked working-tree changes
* **untracked** — untracked files (files matched by ``.gitignore`` are *not*
  counted, so the venv, ``__pycache__``, and build output never trip the gate)
* **unpushed** — commits ahead of the upstream branch (these exist nowhere else)
* **local-only branch** — a branch with no upstream
* **stash** — entries in ``git stash``
* **patched-tree drift** — a patch-managed tree whose changes exceed the recorded
  patch result

A clean checkout — including a detached ``HEAD`` pinned to a pushed commit or tag
— is **safe** and removed without prompting. Non-VCS trees (an extracted archive)
cannot be *proven* clean; they are listed under a "could not verify" heading and
removed anyway (a future ``--paranoid`` flag will make them block).

.. note::

   **Extending the gate.** A source provider participates in ``destroy`` through
   two ``Package`` hooks: ``removal_safety(remove_info)`` returns a structured
   ``RemovalSafety`` verdict (a level plus ``SafetyReason`` *data* — never
   formatted text), and ``remove(remove_info)`` performs the teardown (unlinking
   symlinks, tearing down external state). A new version-control source overrides
   these the same way it already overrides ``status()``; the front-end maps each
   reason's ``kind`` to a label, so a new source self-gates and self-explains
   with no change to ``destroy`` or its report.

The Blocking Report
-------------------

When the gate refuses, ``destroy`` lists **every** blocking package in one pass,
removes nothing, and exits non-zero:

.. code-block:: text

    ivpm destroy: refusing to remove — 2 package(s) hold local work:

      mylib      unpushed: 3 commits ahead of origin/feature-x
      otherpkg   modified: 4 files; untracked: 2 files

    No files were removed. Re-run with --force to delete anyway, or push/commit the
    work above first. Use 'ivpm status' to inspect details.

Add ``-v``/``--verbose`` to expand each finding into the exact files and commit
subjects:

.. code-block:: text

      otherpkg   modified: 4 files
                   src/core.py
                   src/util.py
                   ...

To proceed despite the gate, either resolve the work (``git push`` /
``git commit``) or pass ``--force``.

Read-only and Cache-backed Dependencies
========================================

A dependency fetched with ``cache: true`` is a **symlink into the shared cache**,
whose files are write-protected. ``destroy`` unlinks the symlink — it never
recurses through it, which would corrupt a cache entry other workspaces share.
The same applies to deps-source symlinks (which point into another workspace's
tree).

Reclaiming the now-unreferenced cache entry remains the job of
:doc:`ivpm cache clean <caching>`; ``destroy`` does not delete cache content.

Options
=======

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Option
     - Effect
   * - ``--deps-only``
     - Remove only the imports/venv; keep the root project and ``ivpm.yaml``.
       Runs in place; no ``wsdir`` required.
   * - ``-p``, ``--project-dir``
     - Workspace root for ``--deps-only`` mode (default: current directory).
   * - ``-n``, ``--dry-run``
     - Report what would be removed and the gate verdict; change nothing.
   * - ``-f``, ``--force``
     - Delete even when packages hold local modifications or unpushed commits.
   * - ``-y``, ``--yes``
     - Skip the interactive confirmation prompt (required in non-interactive/CI
       contexts).
   * - ``-j``, ``--jobs``
     - Number of parallel gate/teardown operations (default: CPU count).
   * - ``--no-rich``
     - Plain-text output without the Rich live display.
   * - ``-v``, ``--verbose``
     - List the blocking files/commits in the report.

Both phases run in parallel: the safety gate checks every package concurrently
(each git package shells out several times, so this is the larger win), and the
teardown removes packages concurrently. A live display (a spinner table per
phase, or concise per-line output under ``--no-rich``) shows progress as work
completes. Use ``-j`` to cap concurrency.

Refusals
========

``destroy`` removes trees, so it guards against operating on the wrong one:

#. **Not an IVPM workspace** — the target must contain ``ivpm.yaml`` and/or
   ``package-lock.json``. A mistyped path cannot delete an arbitrary directory.
#. **Full destroy against the current directory or an ancestor** — you cannot
   cleanly delete the ground you are standing on. (``--deps-only`` is exempt, as
   it preserves the root.)
#. **Confirmation in CI** — in a non-interactive context the prompt cannot be
   answered, so ``--yes`` (or ``--force``) is required to proceed.

Relationship to Other Commands
==============================

``destroy`` is the inverse of ``clone`` (full) and ``update`` (deps-only). The
name ``remove``/``rm`` is reserved for a future single-package removal
(``ivpm remove <pkg>``), matching ``npm uninstall`` / ``cargo remove``;
whole-workspace teardown is named ``destroy``, matching ``vagrant destroy`` /
``terraform destroy``.
