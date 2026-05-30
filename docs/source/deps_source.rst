###########
Deps-Source
###########

Overview
========

A **deps-source** is a sibling workspace's ``packages/`` directory (whatever
the project calls its packages dir -- ``packages/`` by default in IVPM).
When configured, IVPM consults that directory *before* the shared cache and
before any remote fetch. If a satisfying entry is found, the current
workspace's package becomes a symlink (or copy) of the parent's, and no
clone or download happens.

A deps-source is a third package source, in addition to remote fetch and
the shared :doc:`cache <caching>`:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Source
     - Description
   * - Remote fetch
     - Clone or download from ``url:`` (git, http, pypi, gh-rls, ...).
   * - Shared cache
     - ``$IVPM_CACHE`` (default ``~/.cache/ivpm/``), populated lazily on
       first remote fetch, then symlinked into ``packages/``.
   * - Deps-source
     - A parent ``packages/`` directory that acts as an even-more-local
       cache, consulted before the shared cache.

When to Use It
==============

The motivating use case is **LLM benchmarking** and other
derivative-workspace workflows: you maintain one large "golden" workspace
whose ``packages/`` has every package already materialized -- possibly
patched or pinned in ways that aren't reproducible from ``url:`` alone --
and want short-lived child workspaces to pull only the subset of packages
they actually need from it.

If you just want network avoidance across multiple workspaces, the shared
``IVPM_CACHE`` is usually a better fit. Deps-source is for the case where
the *source of truth* for a package is "whatever is in that other
``packages/`` over there."

.. note::

   If you work with **git worktrees**, IVPM configures a deps-source
   automatically: a linked worktree reuses the main worktree's ``packages/``
   with no flags required. See :doc:`git_worktrees`.

Quickstart
==========

.. code-block:: bash

   ivpm update --deps-source /path/to/golden/packages

Repeatable for multiple parents (searched in order, first-match-wins):

.. code-block:: bash

   ivpm update --deps-source /shared/golden/packages \
               --deps-source /shared/baseline/packages

Environment-variable form (colon-separated like ``PATH``):

.. code-block:: bash

   export IVPM_DEPS_SOURCE=/shared/golden/packages:/shared/baseline/packages
   ivpm update

How Matching Works
==================

By default, IVPM verifies a candidate by reading the parent's
``package-lock.json`` and comparing the *resolved identity* of the package
the current project would fetch against the lock entry's identity field:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - ``src_type``
     - identity field in parent lock
   * - ``git``, ``gh-rls``
     - ``commit_resolved`` / ``version_resolved``
   * - ``http``, ``tgz``, ``txz``, ``zip``, ``jar``
     - ``etag`` (then ``last_modified``)
   * - ``pypi``
     - ``version_resolved``
   * - ``dir``, ``file``
     - never matched (no caching identity)

A name collision across different ``src_type`` s (the parent has ``foo`` as
``pypi``, you want ``foo`` as ``git``) is a deliberate miss.

If the parent has no ``package-lock.json``, all matches via this strategy
fail silently -- IVPM falls through to the cache / remote fetch path as if
no deps-source were configured.

``--trust-deps-source``
-----------------------

Skip lock-file verification and trust that any same-named directory in the
parent satisfies the request:

.. code-block:: bash

   ivpm update --deps-source /shared/golden/packages --trust-deps-source

Use this when you know the parent is the right snapshot (typical for
benchmarking) and don't want to depend on having a current lock file.

Materialization Mode
====================

``--deps-source-mode={link,copy}``

- ``link`` (default): ``packages/<pkg>`` is a symlink into the parent.
  Fast and free, but edits leak through to the parent.
- ``copy``: ``packages/<pkg>`` is a real copy. Slower and disk-heavy, but
  the child workspace can mutate the package without affecting the parent.

Interaction with ``ivpm sync``
==============================

``ivpm sync`` is a no-op for packages materialized from a deps-source --
they are read-only mirrors of someone else's tree. The sync result reports
``SKIPPED`` with an actionable next step ("re-run ``ivpm update`` without
``--deps-source``, or point at a refreshed parent").

What Does *Not* Go Through Deps-Source
======================================

- ``dir:`` and ``file:`` packages -- already user-supplied paths with no
  caching identity.

Git packages *are* eligible regardless of their ``cache`` setting, including
editable checkouts (``cache: false`` or ``cache`` omitted): if a parent entry
matches the requested identity, the package is linked (or copied) from the
parent. With lock-verified matching this only happens when the resolved commit
matches, so a divergent version still falls through to a fresh clone. Use
``--deps-source-mode=copy`` when you need the local checkout to be independent
of the parent's.

Lock-File Representation
========================

When a package comes from a deps-source, the local workspace's
``package-lock.json`` entry records the provenance:

.. code-block:: json

   {
     "name": "fusesoc",
     "src": "git",
     "commit_resolved": "abc123...",
     "from_deps_source": "/shared/golden/packages/fusesoc"
   }

``ivpm status`` surfaces this provenance so it's clear which packages are
mirrors of a foreign tree.

See Also
========

- :doc:`caching` -- the more general shared-cache mechanism.
- :doc:`package_lock` -- how IVPM records resolved package identity.
- :doc:`package_types` -- per-type identity fields used for matching.
