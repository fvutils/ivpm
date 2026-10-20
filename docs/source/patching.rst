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

   Patching is supported for **git** dependencies and for **archive**
   dependencies fetched by URL (``.tar.gz``/``.tgz``, ``.tar.xz``/``.txz``,
   ``.zip``, ``.jar``), cached and editable alike; see `Archive Dependencies`_.
   ``gh-rls`` dependencies cannot be patched yet: ``patches:`` on one is a
   located error.

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

Archive Dependencies
====================

A dependency fetched from an archive URL takes ``patches:`` the same way a git
dependency does:

.. code-block:: yaml

   - name: slang
     url: https://github.com/MikePopoloski/slang/archive/refs/tags/v11.0.tar.gz
     type: raw
     patches:
       - patches/slang/0001-fix.patch

Patch paths are relative to the package directory as IVPM unpacks it. A tar
archive (``.tar.gz``, ``.tar.xz``, ``.tar.bz2``) loses its leading directory
when unpacked, so the default ``strip: 1`` fits a ``git diff`` or
``diff -u a/... b/...`` made from an unpacked copy. A ``.zip`` or ``.jar`` is
unpacked as is, keeping any top-level directory: include it in the patch paths,
or point ``directory:`` at it.

- **Identity.** The base version of a patched archive is the server's
  ``ETag`` (``W/`` and quotes removed), else its ``Last-Modified``. A patched
  archive with neither is still patched, in place, but never cached: the update
  notes ``Not caching patched <name>``, because a shared entry with no stable
  identity could never be invalidated.
- **Editable** (the default, no ``cache: true``). IVPM unpacks the archive into
  ``packages/<name>`` and keeps a copy of the downloaded archive at
  ``.ivpm/base<ext>``. That copy is what IVPM re-extracts, offline, when the
  declared patch set changes, and what it compares the tree with to detect your
  own edits (`Idempotency and Change Detection`_). An archive first fetched
  unpatched, and given ``patches:`` later, is downloaded again then to keep that
  copy. A patched tree with no copy (left by an older IVPM) gets one the next
  time its patch set changes: IVPM downloads the base, and re-establishes the
  tree only if the tree is that base plus the recorded patches. Otherwise it
  stops with the drift error and drops the download.
- **Cached** (``cache: true`` with ``IVPM_CACHE`` set). As for git: a base
  entry plus a ``<base>_patch_<id>`` variant, shared between projects.
- The lock file records the archive's ``etag``/``last_modified`` and the patch
  set, as for git.

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

For a cached dependency (``cache: true``), each patched **variant** is its own
cache entry:

- On a miss, the pristine source is fetched straight into staging on the cache
  filesystem, the patch set is applied there, and the result is published by
  rename. Nothing is copied: content goes from the network to the cache once.
- The variant is keyed by ``<base_version>_patch_<id>`` (the first 16 hex
  characters of the patch-set id) and is symlinked read-only into
  ``packages/`` exactly like any other cached package.
- No separate pristine base entry is kept. A dependency with an **empty**
  patch set resolves to the base version byte-for-byte, so it shares the
  ordinary unpatched entry.

Worked example -- two consumers, same base:

- Project A pins ``somelib`` at commit ``abc123`` with patch set *P*.
- Project B pins the same commit with a **different** patch set *Q*.

The cache ends up with two variant entries (``abc123_patch_<idP>`` and
``abc123_patch_<idQ>``). Each was fetched once, and each is reused by every
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

A variant, ``package somelib cannot be checked for local modifications``, means
IVPM had no base to compare the tree with and could not recover one, so it
cannot tell whether you edited it. It is handled the same way.

The tree is never modified in this case. To re-establish cleanly, remove the
package directory and re-run ``ivpm update``; IVPM will re-fetch the base and
apply the current declared patch set. (This is the intended escape hatch under
the current CLI; a destructive ``ivpm update --reset`` convenience may be added
later.)

Limitations
===========

- ``gh-rls`` dependencies cannot be patched yet: their pristine bytes come from
  a platform-specific release asset rather than the declared URL, so IVPM
  rejects ``patches:`` on them rather than fetch the wrong archive.
- An archive whose server sends neither ``ETag`` nor ``Last-Modified`` is
  patched in place and not cached (`Archive Dependencies`_).
- A patched git dependency is resolved through the patch pipeline and does not
  consult a :doc:`deps-source <deps_source>`.
