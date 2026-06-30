########
Patching
########

Overview
========

IVPM can apply one or more patch files to a dependency after it is fetched,
without forking the upstream repository. You declare the patches in
``ivpm.yaml`` next to the dependency; IVPM applies them deterministically and
records exactly what it did so that re-running ``ivpm update`` is idempotent.

.. note::

   Patching is currently supported for **git** dependencies (both cached and
   editable). Archive sources (``http``, ``gh-rls``, ``tgz``/``txz``/``zip``/
   ``jar``) declare patch capability but are **not yet wired** to the patch
   pipeline -- ``patches:`` on those sources is silently ignored for now. Apply
   patches only to ``git`` dependencies until archive support lands.

Declaring Patches
=================

Add a ``patches:`` list to a dependency. Each entry is either a **string** (a
path to a patch file) or a **mapping** with options:

.. code-block:: yaml

   package:
     name: consumer
     dep-sets:
       - name: default
         deps:
           - name: somelib
             src: git
             url: https://github.com/foo/somelib.git
             patches:
               - patches/fix.patch                 # string form (strip: 1)
               - file: patches/feature.patch        # mapping form
                 strip: 1
                 directory: src
                 tool: git

Patch-entry options (mapping form):

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Option
     - Meaning
   * - ``file`` (required)
     - Path to the patch file, resolved **relative to the directory of the
       declaring** ``ivpm.yaml`` (absolute paths are honored as-is). Keep patch
       files alongside the project that references them.
   * - ``strip``
     - The ``-p<N>`` leading-path strip level passed to the patch tool.
       Defaults to ``1``.
   * - ``directory``
     - Sub-directory of the package to apply the patch within. Defaults to the
       package root.
   * - ``tool``
     - Force the patch engine: ``git`` (``git apply``) or ``patch`` (GNU
       ``patch``). Omit to auto-detect (see below). ``tool`` does **not** affect
       the resulting tree, so it is deliberately excluded from the patch-set
       identity.

A patch file that does not exist (at parse time) or an unknown option is a hard,
located error. Declaring ``patches:`` on a source type that cannot be patched
(``dir``, ``file``, ...) is also an error.

How Patches Are Applied
=======================

- Patches are applied in **declaration order**.
- With no explicit ``tool``, IVPM auto-detects: it tries ``git apply -p<strip>``
  first, then falls back to GNU ``patch -p<strip>``. The first engine that
  applies cleanly wins.
- Application is **fail-closed**: the first patch that does not apply cleanly
  aborts the update with an error (a partially patched tree is never published).
- A path-traversal guard rejects any patch whose targets would escape the
  package directory before the patch tool runs.

Idempotency and Change Detection
================================

When IVPM patches a tree it writes a manifest, ``.ivpm/patch-manifest.json``,
recording the base version, every applied patch (name, source, MD5, ``strip``,
``directory``), and a **patch-set id**.

The patch-set id is the SHA-256 of a canonical description of the patch set --
one entry per patch, in order, carrying each patch file's MD5 plus its ``strip``
and ``directory``. Consequences:

- Editing a patch file's contents, adding/removing a patch, or **reordering**
  patches changes the id.
- Renaming a patch file or changing its ``tool`` does **not** change the id (the
  resulting tree is identical).

On the next ``ivpm update``, IVPM compares the declared patch set against the
manifest: a match is a no-op; a change triggers a re-establish (for a clean
tree) or an error (for a tree with other local modifications -- see
:ref:`patching-drift`).

Caching Patched Dependencies
============================

For a cached dependency (``cache: true``), patching is **base-first**:

- The pristine **base** is always cached as its own entry, keyed by its base
  version (the resolved commit for git).
- Each patched **variant** is cached as a full copy of the base with the patch
  set applied, keyed by ``<base_version>+patch.<id>`` (the first 16 hex
  characters of the patch-set id). The variant is symlinked read-only into
  ``packages/`` exactly like any other cached package.
- A dependency with an **empty** patch set resolves to the base version
  byte-for-byte, so merely *being patchable* never fragments the cache or
  invalidates existing pristine entries.

Worked example -- two consumers, same base:

- Project A pins ``somelib`` at commit ``abc123`` with patch set *P*.
- Project B pins the same commit with a **different** patch set *Q*.

The cache ends up with one base entry (``abc123``) and two variant entries
(``abc123+patch.<idP>`` and ``abc123+patch.<idQ>``). The base is fetched once
and shared; each distinct patch set materializes one variant, reused by every
consumer that requests the same ``(base, patch set)`` pair.

Editable Patched Checkouts
==========================

For an editable git dependency (``cache: false`` or ``cache`` omitted), IVPM
applies the patches **in place** in ``packages/<pkg>``: it retains the pristine
base (so it can roll back), applies the patch set, and writes the manifest.

Re-running ``ivpm update`` is idempotent:

- **Same declared patch set, clean tree** -- no-op.
- **Changed declared patch set, clean tree** -- IVPM rolls the tree back to the
  pristine base and re-applies the new set.
- **Same declared patch set, you have local edits** -- IVPM leaves your
  in-progress work alone (it never reverts a working tree you are editing).

The safety invariant: IVPM mutates the working tree only when the *sole*
deviation from "base + recorded patches" is its own patch application. Any other
drift makes it stop rather than risk discarding your work.

.. _patching-drift:

The Drift Error
---------------

If the declared patch set changes **and** the working tree has local
modifications beyond the patches IVPM recorded, an unattended ``ivpm update``
cannot safely re-establish the tree without discarding those changes, so it
stops with an error instead:

.. code-block:: text

   package somelib has local modifications beyond its applied patches; refusing
   to re-establish (this would discard your changes). Your tree is left
   untouched.
     - to discard the changes and re-establish: remove 'packages/somelib', then
       re-run 'ivpm update'

The tree is never modified in this case. To re-establish cleanly, remove the
package directory and re-run ``ivpm update``; IVPM will re-fetch the base and
apply the current declared patch set. (This is the intended escape hatch under
the current CLI; a destructive ``ivpm update --reset`` convenience may be added
later.)

Limitations
===========

- Patching applies to **git** dependencies only today; archive sources are not
  yet wired (see the note at the top).
- A patched git dependency is resolved through the patch pipeline and does not
  consult a :doc:`deps-source <deps_source>`.
