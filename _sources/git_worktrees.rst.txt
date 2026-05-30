#############
Git Worktrees
#############

Overview
========

A **git worktree** lets you check out more than one branch of the same
repository at once, each in its own directory, while sharing a single ``.git``
history. You create one with:

.. code-block:: bash

   git worktree add ../myproj-feature feature-branch

This is ideal for working on a feature branch without disturbing your main
checkout -- no stashing, no second clone, no duplicated git history.

IVPM makes worktrees even better: when you run ``ivpm update`` inside a linked
worktree, it automatically reuses the **already-materialized packages from your
main worktree** instead of re-cloning and re-downloading everything. Packages
that your branch hasn't changed resolve from local disk; only the packages your
branch actually bumped are fetched.

Why Worktrees Need a Hand
=========================

IVPM's deps-dir (``packages/`` by default) is almost always **git-ignored** --
it holds fetched dependencies, not source you commit. So a freshly created
worktree starts with *no* ``packages/`` directory at all, even though
``ivpm.yaml`` is present (it is tracked).

A naive ``ivpm update`` in the new worktree would therefore re-resolve every
dependency from scratch: cloning git repos, downloading archives, and rebuilding
the Python virtual environment -- often minutes of work -- even though a complete
``packages/`` for nearly the same dependency set already exists in the sibling
main worktree.

IVPM closes this gap automatically.

Quickstart
==========

.. code-block:: bash

   cd ~/proj/myws                 # main workspace, already `ivpm update`-d
   git worktree add ../myws-feature feature-branch
   cd ../myws-feature
   ivpm update

When ``ivpm update`` runs in the worktree, it prints:

.. code-block:: text

   git worktree detected: sourcing unchanged packages from main worktree (/home/you/proj/myws/packages)

Unchanged packages are linked from the main worktree's ``packages/`` instantly;
anything your branch changed is fetched normally. No flags required.

How It Works
============

Detection
---------

IVPM identifies a *linked* worktree using git's own plumbing: in a linked
worktree ``git rev-parse --git-dir`` differs from
``git rev-parse --git-common-dir`` (in the main worktree they are equal). The
main worktree's path is the first entry of ``git worktree list``. See
:doc:`git_integration` for more on IVPM's git handling.

Lock-Verified Reuse
-------------------

Reuse is **verified**, not blind. For each package, IVPM resolves the identity
your branch asks for (e.g. a git commit) and compares it against the main
worktree's ``package-lock.json``:

- **Match** -- the package is unchanged on your branch, so ``packages/<pkg>`` is
  linked to the main worktree's copy. No fetch.
- **No match** -- your branch pins a different version, so IVPM falls through to
  the shared cache / remote and fetches the correct version.

This is exactly the behavior you want from a worktree: reuse everything you
didn't touch, fetch what you did. See :doc:`deps_source` ("How Matching Works")
and :doc:`package_lock` for details.

.. note::

   Worktree auto-detection always uses lock-verified matching. It never enables
   ``--trust-deps-source`` (trust-by-name), because that could silently link the
   main worktree's version of a package your branch deliberately changed.

What Gets Shared
----------------

Lock-verified matching decides this per package, and it does the right thing for
the worktree use case:

- **Cacheable packages** (``cache: true``) are read-only mirrors -- in either
  worktree they point into the same shared :doc:`cache <caching>` anyway, so
  linking is free and safe.
- **Editable packages** (``cache: false`` or ``cache`` omitted) are shared
  *only when your branch resolves to the same version as the main worktree*. If
  your branch pins a different commit/tag, the version won't match and you get
  your own independent clone (see `Working on Diverging Dependencies`_ below).

.. warning::

   When an editable package *is* shared, ``packages/<pkg>`` in the worktree is a
   symlink to the **same on-disk clone** as the main worktree -- they share one
   working copy, one branch, one index. Edits and ``git`` operations on that
   package are visible in both worktrees. This is usually what you want for a
   dependency you are co-developing (a single source of truth). If you need an
   independent copy, use ``--deps-source-mode=copy``, or pin the package to a
   different version on your branch so it falls through to its own clone.

Working on Diverging Dependencies
==================================

The headline benefit: create a worktree to try a dependency bump, and IVPM
fetches *only* the changed package while reusing everything else.

.. code-block:: bash

   git worktree add ../myws-bump main
   cd ../myws-bump
   # edit ivpm.yaml: bump `mylib` to a newer commit/tag
   ivpm update
   #   mylib        -> fetched fresh (branch pins a new commit)
   #   everything else -> linked from the main worktree

Controlling the Behavior
========================

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Option
     - Effect
   * - *(default)*
     - Auto-detect the parent worktree and reuse via lock-verified linking.
   * - ``--no-worktree-deps-source``
     - Disable auto-detection entirely; resolve as if standalone.
   * - ``--deps-source PATH`` / ``IVPM_DEPS_SOURCE``
     - Take precedence over auto-detection (auto only fills the gap).
   * - ``--deps-source-mode=copy``
     - Copy instead of symlink, for full physical isolation from the main tree.

Inspecting Provenance
=====================

``ivpm status`` annotates packages that were reused from the main worktree with
``(auto: worktree)`` (versus ``(deps-source)`` for a user-supplied
``--deps-source``):

.. code-block:: text

   ✓  mylib                          (detached @ 9f3a1c2)  9f3a1c2  clean
   ~  somedata (auto: worktree)      (dir)

``ivpm sync`` treats worktree-sourced packages as read-only mirrors and reports
them ``SKIPPED`` -- their source of truth is the main worktree, not upstream.

Lifecycle and Cleanup
=====================

Remove a worktree with git as usual:

.. code-block:: bash

   git worktree remove ../myws-feature

The worktree's ``packages/`` entries are symlinks into the main tree (or
independent copies under ``copy`` mode), so removing the worktree just deletes
those links; the main worktree is untouched.

.. warning::

   If you delete or move the **main** worktree while a linked worktree still
   references it, that worktree's symlinks will dangle. Re-run ``ivpm update``
   in the affected worktree to rematerialize (it will fetch fresh, or pick up a
   new main worktree if one exists).

Non-Git and CI
==============

.. note::

   This feature is purely opportunistic. In a directory that is not a git
   repository, or on a system where git is not installed, detection is a silent
   no-op -- ``ivpm update`` behaves exactly as it always has, with no errors and
   no extra output. IVPM never requires git for its own operation; git is only
   needed to fetch ``git:`` packages.

Troubleshooting
===============

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Symptom
     - Likely cause / fix
   * - Worktree re-fetched everything
     - The main worktree was never updated, has no ``package-lock.json``, or your
       branch diverged on most packages. Run ``ivpm update`` in the main
       worktree first.
   * - Want to disable reuse for one run
     - Pass ``--no-worktree-deps-source``.
   * - Need an independent copy of a reused package
     - Use ``--deps-source-mode=copy``, or pin the package to a different
       version on your branch so it falls through to its own clone.
   * - Edits to a reused package appeared in the main tree
     - Expected: a package whose version matched the main worktree is shared by
       symlink (one working copy for both trees). Use ``copy`` mode for
       isolation, or pin a different version on your branch.

See Also
========

- :doc:`deps_source` -- the underlying mechanism worktree detection builds on.
- :doc:`caching` -- the shared cache that backs cacheable packages.
- :doc:`git_integration` -- IVPM's broader git handling.
- :doc:`package_lock` -- how IVPM records and verifies resolved identity.
- :doc:`workflows` -- other day-to-day development workflows.
