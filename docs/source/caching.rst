#######
Caching
#######

Overview
========

IVPM supports caching of package data to:

- Reduce network traffic when fetching version-controlled files
- Reduce disk space for shared dependencies that aren't being edited
- Speed up project initialization across multiple workspaces

Cached packages are always **read-only** and **symlinked** into the 
``packages/`` directory, allowing multiple projects to share the same cached 
package data.

.. note::

   One exception: a package that acts as a :doc:`nested scope boundary
   <nested_deps>` must own its directory so it can hold a deps-dir, so it is
   materialized as a *writable copy* of the cache entry rather than a symlink.
   The cache entry itself is unaffected and still shared.  Cache identity is a
   function of source and version only -- never of where a package is placed --
   so nesting never creates a second entry for the same package version.

Cache Modes
===========

IVPM supports three caching modes per package, controlled by the ``cache`` attribute:

.. list-table::
   :header-rows: 1
   :widths: 20 15 15 15 35

   * - cache value
     - Cached?
     - History?
     - Editable?
     - Use Case
   * - ``true``
     - Yes
     - No
     - No
     - Production deps, stable releases
   * - ``false``
     - No
     - Yes\*
     - Yes
     - Editable clone, never cached
   * - (unspecified)
     - No
     - Yes\*
     - Yes
     - Development, co-development (default)

\* The *History?* and *Editable?* columns describe **git** packages, where
``depth:`` controls history (full unless set). For **archive** packages (``http``,
``gh-rls``, ``tgz``/``txz``/``zip``/``jar``) there is no editable working copy:
``cache: false`` downloads and unpacks the archive **read-only**, while omitting
``cache`` unpacks it **writable**.

**cache: true**
    - Stored in ``$IVPM_CACHE`` and symlinked into ``packages/``
    - Read-only (cannot modify), shared across projects
    - git: shallow checkout (depth 1) at the resolved commit

**cache: false**
    - Not cached -- never consults or writes the shared cache
    - git: an **editable** clone written to ``packages/`` (full history unless
      ``depth:`` is set) -- the same working copy as omitting ``cache``, just
      guaranteed never to use the cache
    - archive sources (``http``, ``gh-rls``, ...): downloaded, unpacked, and made
      **read-only**

**cache not specified** (default)
    - Not cached
    - git: full **editable** clone -- the common case for co-developed deps
    - archive sources: downloaded and unpacked **writable**

Configuration
=============

Enabling the Cache
------------------

Caching is enabled by setting the ``IVPM_CACHE`` environment variable to point 
to the cache directory:

.. code-block:: bash

    export IVPM_CACHE=/path/to/cache

Add this to your shell rc file (``.bashrc``, ``.zshrc``, etc.) to make it permanent.

**Recommended cache locations:**

- Personal cache: ``~/.cache/ivpm`` or ``~/ivpm-cache``
- Shared cache: ``/shared/ivpm-cache`` or ``/opt/ivpm-cache``

If ``IVPM_CACHE`` is not set, IVPM falls back to full (uncached) clones. A
dependency that explicitly requests ``cache: true`` while no cache is configured
is reported in the update summary; otherwise the fallback is silent.

Disabling the Cache for One Run
-------------------------------

Pass ``--no-cache`` to ``ivpm update`` to disable caching for a single run,
regardless of ``IVPM_CACHE`` or any ``cache: true`` flags:

.. code-block:: bash

    ivpm update --no-cache

This forces the *null cache provider* for that invocation, so every dependency is
fetched fresh as if no cache were configured. It does not modify or remove any
existing cache entries.

Initializing a Cache Directory
-------------------------------

Create a new cache directory:

.. code-block:: bash

    ivpm cache init /path/to/cache

For **shared environments** where multiple users access the cache, use the 
``--shared`` option to set group inheritance permissions (``chmod g+s``):

.. code-block:: bash

    sudo ivpm cache init --shared /shared/ivpm-cache
    sudo chown :developers /shared/ivpm-cache
    export IVPM_CACHE=/shared/ivpm-cache

This sets the setgid bit and group ownership on the cache *root* so new files
inherit the cache's group. (IVPM also applies the setgid bit to every individual
cache entry it creates, regardless of ``--shared``; ``--shared`` is about the
root's group ownership and inheritance.)

Cleaning up an entry does **not** depend on being able to write inside it.
Eviction is a rename of the entry out of the way, followed by deletion of the
renamed copy, and a rename needs write permission on the enclosing
``<cache>/<package>/`` directory only. Two consequences worth knowing:

* A group member can evict an entry created by another user even when they
  cannot modify the files inside it.
* A reader never observes an entry being taken apart. Because the rename
  removes the entry from view before any byte is deleted, a concurrent
  ``ivpm update`` either resolves the complete entry or sees a clean miss and
  re-fetches — it cannot resolve a half-deleted one.

A ``.gc.<uuid>`` directory in a package's cache directory is an entry that has
been evicted but whose bytes could not be deleted (usually cross-user
permissions). It is inert — no lookup can see it — and IVPM retries the
deletion on later runs.

How Caching Is Resolved
=======================

Internally, IVPM does not ask *"where is the cache directory?"* — it asks the
site configuration for a **cache provider** for the current session. This keeps
all caching intelligence in one place instead of scattered through each package
type.

- **The configuration always returns a provider** — never a bare path and never
  ``None``. When caching is disabled it returns a *null provider* whose every
  lookup reports the dependency **uncacheable**, so there is no
  "is the cache configured?" special-casing in package code.
- **One provider per ``ivpm update`` run.** A single provider is created once,
  is aware of the root project, and serves every dependency. For each
  dependency it answers two questions: *is this dependency cacheable?* (caching
  enabled **and** the dependency's ``cache: true`` flag set) and *is this
  version a hit, a miss, or uncacheable?*

The user-visible resolution order is unchanged:

1. The ``IVPM_CACHE`` environment variable (an empty value disables caching).
2. Otherwise, the site default (``get_default_cache_dir()`` — see below).
3. Otherwise, caching is disabled (the null provider).

A dependency with ``cache: true`` but no resolved cache directory falls back to
a full editable clone and is reported in the update summary, exactly as before.

Entry Layout
============

Everything IVPM writes under a package's cache directory falls into one of four
categories. Knowing which is which is what lets an administrator look at a
shared cache and tell content from residue::

    <cache>/<package>/
        <version>/                        the entry itself (read-only)
            .ivpm-cache-entry.json        the entry's seal — see below
            ...package content...
        <version>.meta.json               mutable sidecar: stored / last_linked
        <version>.staging.<uuid>/         a publish in flight, or crash residue
        build.staging.<uuid>/             a fetch in flight, or crash residue
        .gc.<uuid>/                       evicted, awaiting deletion

Only ``<version>/`` directories are entries. The rest is transient and is
skipped by every cache listing; stale staging directories are reclaimed after
24 hours and tombstones after one hour.

Both ``<package>`` and ``<version>`` are single path components, and IVPM
enforces that. A package name must match ``[A-Za-z0-9._+-]+`` or the manifest
is rejected at parse time. A version key is machine-made — an ETag, a commit
hash, a release tag — so rather than refusing to cache the package, any
character outside that set is percent-escaped: an ``ETag`` of ``W/"a/b"``
becomes ``W%2F%22a%2Fb%22``. Keys that are already safe are stored unchanged,
so no existing entry moves.

The seal (``.ivpm-cache-entry.json``)
-------------------------------------

Every entry IVPM publishes contains a small read-only JSON file recording what
the entry is:

.. code-block:: json

    {"schema": 1,
     "package": "libX",
     "version": "a1b2c3d4",
     "created": 1757600000.0,
     "creator": {"ivpm": "1.2.3", "host": "build07"},
     "source": {"src_type": "git", "url": "https://github.com/o/r.git"},
     "content": {"files": 8412, "dirs": 903, "bytes": 91240113, "merkle": null}}

It is written into the entry *before* the entry becomes visible, so it does two
things that nothing else can:

* **It marks the entry as complete.** A directory being non-empty does not
  distinguish a published entry from a half-finished copy; the presence of the
  seal does. An entry without one is treated as unpublished.
* **It records what the entry claims to be.** On a later cache hit, IVPM
  compares the seal against the package asking for it. An entry whose seal
  names a different package or version has been moved or hand-edited, and is
  treated as a miss rather than served.

The ``source`` block is compared too, but only for entries whose version key is
*not* derived from the content. A git entry is keyed on its commit hash, so two
different URLs resolving to it are mirrors of one repository and the entry is
served normally. Elsewhere a differing source means two packages have collided
on one key, and IVPM re-fetches instead of serving the wrong bytes. (For HTTP
and GitHub Release entries the URL is folded into the key itself, so that
collision cannot arise in the first place; the check remains as a backstop for
entries written by an older IVPM.)

.. note::

   Because ``deps/<package>`` is a symlink to the entry, this file is visible
   in the root of every cached package. It is read-only and safe to ignore.

Entries created by earlier IVPM versions have no seal. They continue to be used
as-is for one release, so upgrading does not invalidate an existing cache.

Verifying Cache Entries
=======================

A cache HIT is a decision to hand a consumer bytes chosen by *key* alone. Before
that happens, IVPM checks the entry against its seal. There are three levels:

.. list-table::
   :header-rows: 1
   :widths: 12 30 58

   * - Level
     - Work
     - Catches
   * - ``off``
     - Read the seal only
     - An entry that says it is something else (a moved entry, a key collision)
   * - ``shape``
     - One metadata-only walk
     - Truncation, partial deletion, an interrupted copy — every corruption a
       crashed fetch or a half-finished cleanup can produce
   * - ``content``
     - ``shape`` plus a hash of every byte
     - Bit rot, tampering, wrong bytes under a right key

The default is ``shape``. It reads no file contents, so it costs milliseconds
against the multi-second re-fetch it protects, and it is exactly the level that
catches the failure modes a shared cache actually produces. ``off`` remains
available for a private cache on a slow filesystem; note that it still refuses
an entry whose seal contradicts the key it was found under, because that check
is one small file read and is the only defense against a key collision.

Set the level with (highest priority first):

.. code-block:: bash

   ivpm update --verify content       # this run only
   export IVPM_CACHE_VERIFY=content   # this shell

.. code-block:: yaml

   # ~/.config/ivpm/config.yaml or /etc/ivpm/config.yaml
   cache-verify: content

``content`` also changes what is *recorded*: a content hash is only written into
the seal when an entry is published under ``content``, because hashing every
byte at publish time is a real cost on every cache miss. Entries published
earlier have no hash, and verifying them at ``content`` falls back to ``shape``
rather than inventing a mismatch.

What happens when verification fails
------------------------------------

Nothing fatal. The entry is evicted, the failure is reported, and the caller
sees a miss — so the ordinary miss path rebuilds it. **An everyday
``ivpm update`` is therefore the primary repair mechanism**, and the run
summary says so::

   ⚠ 1 cache entry failed verification and was rebuilt.
     Run 'ivpm cache verify --repair' to check the rest of the cache.

The eviction matters: without it, the "entry already exists" fast path would
hand the bad entry straight back on the next lookup. Auto-repair is bounded to
**once per entry per run** — an entry that fails again after being rebuilt is
reported as an error and fetched without the cache, rather than becoming a
refetch loop against a filesystem that is misbehaving.

Failed verifications are counted separately from ordinary cache misses. Without
that distinction, a systematically corrupt shared cache looks exactly like a
cold one in every statistic IVPM reports.

Verifying and Repairing a Cache
===============================

``ivpm cache verify`` inspects a whole cache. It has two modes, and the
difference between them is a guarantee rather than a convenience:

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * -
     - **check** (default)
     - **repair**
   * - Invocation
     - ``ivpm cache verify``
     - ``ivpm cache verify --repair`` (alias: ``ivpm cache repair``)
   * - Mutates
     - **Never**
     - Yes: evict / reseal / remove / backfill
   * - Produces
     - A health report
     - A per-pass repair log, then the health report
   * - Safe on a cache you do not own
     - Yes — read-only
     - Only for entries you can write

.. code-block:: bash

   ivpm cache verify [-c CACHE_DIR] [-p PACKAGE] [--content] [--json] [-v]
                     [--repair [--max-passes N] [-n] [--upgrade]]

The health report
-----------------

.. code-block:: text

   Cache health: DEGRADED       /shared/ivpm-cache
     Inventory     812 entries across 96 packages, 41.2 GB
                   784 sealed (manifest present) · 28 legacy (no manifest)
     Verified      812 entries at level 'shape' (0.0 B hashed) in 3.1s

     Problems      14 total · 11 auto-repairable · 3 need manual action
                     6  shape-mismatch       evict      (2.1 GB)
                     3  entry-writable       reseal
                     2  staging-residue      remove     (840.0 MB)
                     3  entry-perms          manual

     Worst         libX         4 problem(s)

     Reclaimable   2.9 GB from residue and failed entries

     Action        11 problem(s) repairable: ivpm cache verify --repair
                   3 problem(s) need manual action (owned by uid 1042)

The status is a three-valued rollup, defined so it can gate CI:

``HEALTHY``
   Nothing wrong, or only pre-manifest entries during the migration window.

``DEGRADED``
   Problems exist and IVPM can fix all of them, and **no entry is serving wrong
   content** — residue, permission drift.

``BROKEN``
   At least one entry can serve content that is not what its key promises, or a
   problem needs a human.

The distinction that matters: ``DEGRADED`` is untidy, ``BROKEN`` is *incorrect*.

Exit codes are derived from the final status, identically in both modes, so one
cron rule covers both:

.. list-table::
   :header-rows: 1
   :widths: 10 90

   * - Code
     - Meaning
   * - ``0``
     - ``HEALTHY``
   * - ``1``
     - ``DEGRADED`` — auto-repairable problems remain. *Run* ``--repair``.
   * - ``2``
     - Operational error (cache unreadable, bad arguments). Not a statement
       about cache health.
   * - ``3``
     - ``BROKEN`` — entries can serve wrong content, or a human is needed.
       *Wake somebody.*

``--json`` emits the same information under a frozen ``schema: 1`` structure,
with ``status`` always reflecting the **post-run** state so a monitor can alert
on that one field.

Repairing
---------

Repair iterates verify → repair → re-verify until the cache converges, then
reports one of three outcomes:

``CONVERGED``
   A verification pass found nothing.

``STALLED``
   Progress stopped with problems remaining — all manual, or all already
   attempted. The report names what is left and who has to act.

``EXHAUSTED``
   ``--max-passes`` was reached with problems outstanding. If this recurs across
   runs, something is damaging the cache faster than it is being repaired —
   which points at the filesystem or a broken writer, not at any one entry.

The repair for each problem is a property of the problem, not a choice made at
the command line:

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - Action
     - What it does
   * - ``evict``
     - Removes the entry whole. Its content is untrustworthy and rebuildable
       from source.
   * - ``reseal``
     - Fixes permissions. The content is fine; only the lock drifted, and
       evicting would discard good bytes over a permission bit.
   * - ``remove``
     - Deletes residue. Never touches an entry.
   * - ``backfill``
     - Writes a seal onto a pre-manifest entry (``--upgrade`` only). A
       baseline, not a certificate — it records what the entry holds now and
       claims nothing about its past.
   * - ``manual``
     - Reported, never attempted, with the owning uid.

Three guarantees worth stating outright:

- **Check mode never mutates.** It walks the filesystem itself and calls only
  pure readers — it never sweeps residue, never refreshes an entry's
  last-used timestamp, and never removes an empty directory it happens to
  find.
- **Repair never edits entry content in place.** Every action either removes
  an entry whole or changes permissions, so a repair can never *create* a
  corrupt entry.
- **Both modes are safe against a cache with active updates.** Check is
  read-only; every repair mutation is either an atomic rename or a ``chmod``.
  An update that is mid-lookup on an entry being evicted sees a clean miss and
  re-fetches — it never sees a tree being dismantled. A concurrent update that
  republishes an entry this run evicted is the system working, and the repair
  loop does not report it as a failure.

``--dry-run`` reports exactly what ``--repair`` would do, changing nothing. It
runs a single pass and says so, rather than implying a convergence it did not
test — later passes' findings depend on repairs that were not applied.

Customizing Caching (Site Config)
=================================

Sites can customize caching by shipping a :class:`~ivpm.site_config.SiteConfig`
subclass. The recommended way is an **extension** that declares an
``ivpm.site_config`` entry point (see :doc:`extending_ivpm`); the legacy
``ivpm_site_config`` module is also still honored. The simplest override sets the
default cache directory:

.. code-block:: python

   # src/acme_ivpm/site_config.py
   from ivpm.site_config import SiteConfig

   class MySiteConfig(SiteConfig):
       def get_default_cache_dir(self) -> str:
           return "/shared/ivpm-cache"   # return "" to disable by default

       def get_ivpm_install_args(self) -> list:
           return ["ivpm"]

.. code-block:: toml

   # pyproject.toml of the extension package
   [project.entry-points."ivpm.site_config"]
   acme = "acme_ivpm.site_config:MySiteConfig"

Run ``ivpm show site-config`` to confirm the config is registered and to see the
resolved cache directory it applies.

For full control — per-dependency routing, an alternate backend, or selectively
disabling caching for some packages — override ``get_cache_provider`` directly.
It receives a :class:`~ivpm.cache_provider.CacheContext` (root project name,
version, directory, and ``deps_dir``) and must return a
:class:`~ivpm.cache_provider.CacheProvider`:

.. code-block:: python

   from ivpm.site_config import SiteConfig
   from ivpm.cache_provider import NullCacheProvider

   class MySiteConfig(SiteConfig):
       def get_cache_provider(self, context):
           provider = super().get_cache_provider(context)
           # Example: never cache an internal, fast-moving package
           class _Routed(type(provider)):
               def is_cacheable(self_, pkg):
                   if getattr(pkg, "name", None) == "internal-wip":
                       return False
                   return super().is_cacheable(pkg)
           provider.__class__ = _Routed
           return provider

A site config that overrides only ``get_default_cache_dir()`` keeps working
unchanged — the default ``get_cache_provider()`` is built on top of it.

.. note::

   Patched dependencies (a future feature) layer on this same provider seam, so
   no configuration changes will be required to benefit from it.

Concurrency and Integrity
=========================

A shared cache normally has several writers: parallel worker threads inside one
``ivpm update``, separate ``ivpm update`` processes over one workspace, and —
on a shared ``IVPM_CACHE`` — separate users on separate machines. IVPM does not
lock. It relies instead on the cache being *immutable* and *version-addressed*:
two builders of the same key produce equivalent trees, so it does not matter
which one wins.

What that buys you, stated as guarantees:

- **A consumer never sees a partly-built entry.** An entry is built in a
  uniquely-named staging directory beside its destination and published with a
  single atomic rename. It becomes visible complete or not at all.
- **A consumer never sees an entry being taken apart.** Eviction renames the
  entry out of view first and deletes the bytes afterwards, so a reader either
  resolved the intact tree or sees a clean miss. A deletion that fails part-way
  leaves inert marked residue, not a truncated entry that reads as a hit
  forever.
- **Two concurrent fetches of the same package never collide.** Staging names
  carry a UUID, so no two builders — thread, process, host, or reused PID —
  are ever handed the same path.
- **A losing builder adopts the winner's entry** rather than failing or
  overwriting. Its own staging tree is discarded.
- **``ivpm cache clean`` and ``ivpm cache verify`` are safe to run while
  updates are in flight.** Neither destroys a build in progress, and a fresh
  staging directory is never mistaken for abandoned residue.
- **A published entry cannot be changed from the inside.** Directories within
  an entry are ``r-xr-sr-x`` (2555), not just its files. Clearing the files'
  write bits alone would protect nothing — unlink and rename are governed by
  the *parent* directory, so on a group-writable cache any member could delete
  or replace any file inside a "read-only" entry, and the next reader would be
  served the result as a hit. The package directory ``<cache>/<package>/``
  stays group-writable, because both publishing and eviction rename through it.

.. note::

   Entries published by an earlier IVPM have group-writable directories.
   ``ivpm cache verify`` reports this once as ``entry-perms`` and
   ``ivpm cache repair`` re-seals them in place — no re-fetch is involved.

The residual risk is not concurrency but *damage*: a filesystem that loses
bytes, a process killed at the wrong moment in an older IVPM, a hand-edited
entry. That is what the verification levels above are for.

Writing a Race-Safe Cache Provider
==================================

Several ``ivpm update`` runs may hit one shared cache at the same time — separate
projects sharing an ``IVPM_CACHE``, two CI jobs on one workspace, or parallel
worker threads within a single run. If you implement a **custom backend** (a
:class:`~ivpm.cache_provider.CacheProvider` subclass with its own storage rather
than the built-in directory store), it must stay correct under that concurrency.
The built-in :class:`~ivpm.cache.DirectoryCacheStore` is the reference
implementation of the rules below.

**The core principle: don't lock — publish atomically.** A cache entry is
*immutable* and *version-addressed*: its bytes are a pure function of its version
key (a commit hash, an ETag, a patched effective-version, …). Because any two
builders of the same key produce equivalent trees, it does not matter *which*
racing builder wins. That makes a lock unnecessary for correctness — and a lock
is actually worse, since it adds stale-lock recovery after a crash, unreliable
locking over NFS, cross-user ownership problems, and deadlock risk. Correctness
comes instead from an atomic *publish* plus *adopt-on-conflict*.

Concretely, a race-safe provider follows these rules:

* **Keep entries immutable and version-addressed.** Never edit a published entry
  in place; a changed input is a *new* version (a new key), stored alongside the
  old one. This is the invariant every other rule depends on.
* **Publish atomically.** Build into a private staging location, then make the
  finished entry appear in a single indivisible step — a same-filesystem
  ``rename`` for a directory store, or the backend's equivalent conditional
  put / compare-and-swap. A reader must never observe a half-built entry.
* **Adopt on conflict; losing the race is normal.** If another builder published
  the same version first, discard your own copy and return the winner's entry —
  it is already complete because the publish was atomic. Do **not** treat this as
  an error.
* **Separate "lost the race" from a genuine failure.** Only a publish collision
  leads to adopt. A real error (out of space, permission denied, network loss)
  must surface — never be swallowed and reported as a cache miss. The directory
  store raises :class:`~ivpm.cache.CacheStoreError` for this case.
* **Treat presence as "complete," not merely "exists."** A ``HIT`` must mean a
  fully-materialized entry. Guard against an empty or partial artifact left by an
  interrupted run (a completion marker, a non-empty check, or the backend's own
  atomic-visibility guarantee). Never report a partial entry as present.
* **Use collision-proof staging names.** Make each build's staging location
  unique (e.g. a ``uuid4`` suffix) so concurrent builders — threads that share a
  PID, separate processes, or a reused PID after a crash — never share a staging
  path or nest into one another.
* **Stage on the medium you publish to.** So the publish is a cheap atomic move,
  not an interruptible cross-device copy. Providers expose this via
  ``new_staging`` (below).
* **Never require a lock for correctness.** A lock is fine purely as an
  *optimization* — e.g. to avoid two runs fetching the same large artifact — but
  correctness must still hold when the lock is unavailable (NFS, cross-user, or
  stale after a crash). Any lock failure must fall back to the optimistic
  build-and-publish path.
* **Clean up crash leftovers conservatively.** Sweep orphaned staging only past a
  generous age, and key that age on a timestamp that reflects real creation
  (``ctime``), not one a copy can backdate (``mtime``), so a live build is never
  reaped.
* **Keep ``materialize`` last-writer-wins.** Placing the immutable entry into
  ``deps/`` (a symlink for the directory store) must tolerate a concurrent
  re-materialization of the same target.

The ``new_staging`` hook
------------------------

To make "stage on the medium you publish to" easy, a provider may implement:

.. code-block:: python

   def new_staging(self, pkg) -> Optional[str]:
       """A unique, not-yet-created build directory on the cache's own storage.
       Callers that must build a tree before store() (e.g. the patch resolver)
       build here so store() publishes by a same-filesystem move, not a copy."""

The base class returns ``None`` (callers then fall back to their own scratch
directory), so implementing it is **optional** — a provider that omits it stays
correct, just without the same-filesystem-publish optimization.

.. note::

   A custom provider must be a genuine
   :class:`~ivpm.cache_provider.CacheProvider` **subclass**, not a duck-typed
   look-alike. Subclassing guarantees you inherit safe defaults (such as
   ``new_staging`` and ``note_reference``) as the provider API grows, rather than
   breaking when a new hook is introduced.

Cache Organization
==================

The cache is organized by package name, with version-specific subdirectories:

- For Git packages, the version is the commit hash
- For HTTP packages, the version is derived from the Last-Modified header or
  ETag, plus a digest of the resolved URL
- For GitHub Releases, the version is the release tag plus a digest of the
  selected asset URL

The rule behind all three: **the cache version identifies the artifact that
was resolved, not the inputs that resolved it.** Enumerating inputs does not
scale -- a GitHub Release key would need OS, arch, glibc, distro and distro
version, and would need extending again the next time asset selection
consults something new. Identifying the output is stable under all of that.

Example structure::

   $IVPM_CACHE/
   ├── gtest/
   │   ├── abc123def456.../           # Git commit hash
   │   └── 789xyz012abc.../           # Different commit
   ├── boost/
   │   ├── Thu_01-Jan-2024_120000_1c9d4e77b0a2/   # HTTP: validator + URL digest
   │   └── Fri_15-Mar-2024_093000_1c9d4e77b0a2/
   └── uv/
       ├── 0.1.0_9f2c1ab34de0/       # GitHub Release: tag + asset digest
       └── 0.1.1_4b7e0c19aa52/

Each version directory contains the complete, read-only package content.

Package Caching
===============

Enabling Caching for Packages
------------------------------

To enable caching for a package, set the ``cache`` attribute in your ``ivpm.yaml``:

.. code-block:: yaml

   dep-sets:
     - name: default-dev
       deps:
         - name: gtest
           url: https://github.com/google/googletest.git
           branch: main
           cache: true

The ``cache`` attribute can be:

- ``true`` - Enable caching (read-only, symlinked from cache)
- ``false`` - No cache, read-only (clone without history, not cached)
- Unspecified - No cache, editable (full history, can be modified)

Cached packages are always read-only and are symlinked into the ``packages/`` 
directory.

Git Packages
------------

IVPM supports caching for **any Git repository**, not just GitHub.

**For GitHub URLs** (recommended for speed):

1. IVPM queries the GitHub API to get the commit hash of the target branch/tag
2. If the commit exists in the cache, it symlinks to ``packages/``
3. If not cached, clones without history, stores in cache, and symlinks

**For general Git URLs:**

1. IVPM uses ``git ls-remote`` to get the commit hash
2. If the commit exists in the cache, it symlinks to ``packages/``
3. If not cached, clones without history, stores in cache, and symlinks

**Examples:**

.. code-block:: yaml

   deps:
     # GitHub repo (uses API)
     - name: my-lib
       url: https://github.com/org/lib.git
       branch: v1.0
       cache: true
     
     # GitLab repo (uses git ls-remote)
     - name: other-lib
       url: https://gitlab.com/org/lib.git
       tag: release-1.0
       cache: true
     
     # Self-hosted Git (uses git ls-remote)
     - name: internal-lib
       url: https://git.company.com/team/lib.git
       branch: stable
       cache: true

**Cache key:** The full commit hash (40 characters)

**Benefits:**

- Multiple projects can share the same cached version
- Updates only download if the commit hash changes
- Significant time savings for large repositories

HTTP/URL Packages
-----------------

For cacheable HTTP URLs (e.g., ``.tar.gz`` files):

1. IVPM fetches the Last-Modified date or ETag via HTTP HEAD request
2. If a matching entry exists in the cache, it symlinks to ``packages/``
3. If not cached, downloads, unpacks, stores in cache, and symlinks

**Examples:**

.. code-block:: yaml

   deps:
     - name: boost
       url: https://boostorg.jfrog.io/artifactory/main/release/1.82.0/source/boost_1_82_0.tar.gz
       cache: true
     
     - name: test-data
       url: https://cdn.example.com/vectors-v2.tar.gz
       cache: true

**Cache key:** the Last-Modified header (converted to a safe filename) or ETag,
followed by a short digest of the resolved URL: ``<etag>_<url-digest>``.

The cache is keyed on package *name* plus version, and the validator alone does
not say which URL produced the bytes. Two projects that both call a dependency
``docs``, and both get a same-shaped ``Last-Modified`` from their respective
servers, would otherwise share one entry -- and with a shared cache (NFS, a CI
cache volume) the first writer's bytes would be served to the other. The same
applies to a single package whose ``url`` is built from a platform variable
(see :doc:`variables`), which resolves differently per platform. Folding the URL
in makes the key name the source as well as the version.

.. note::

   Earlier releases appended this digest only when the ``url`` used a platform
   variable. It is now unconditional, so HTTP entries written before that change
   are keyed differently and are fetched once more. The stale entries carry no
   references and age out with ``ivpm cache clean``.

**Benefits:**

- Avoid re-downloading large archives
- CDN files are often stable and benefit from caching

GitHub Releases
---------------

GitHub Release packages support platform-specific caching:

.. code-block:: yaml

   deps:
     - name: uv
       url: https://github.com/astral-sh/uv
       src: gh-rls
       version: latest
       cache: true

**Cache key:** ``<release-tag>_<digest-of-selected-asset-url>``

Examples:

- ``0.1.0_9f2c1ab34de0``
- ``0.1.0_4b7e0c19aa52``

This allows different platforms to cache different binaries for the same
release.

.. note::

   **Changed key.** This key was previously
   ``<release-tag>_<platform>_<architecture>``, which omitted the rest of what
   asset selection actually consults: ``manylinux`` assets are chosen using
   glibc, and distro-tagged assets using the distro and its version. Two Linux
   x86_64 machines -- one glibc 2.17, one glibc 2.34 -- therefore selected
   *different* assets and stored them under the identical key, and a shared
   cache silently handed the second machine a binary built against the wrong
   floor. ``ubuntu-22.04`` and ``ubuntu-24.04`` assets collided the same way.

   After upgrading, existing ``gh-rls`` cache entries miss once and are
   refetched. Some of those entries were the wrong bytes, which is the point.

**Benefits:**

- Cache platform-specific binaries separately
- Share cache across team members on the same platform
- Avoid re-downloading large binary releases

Cache Management
================

IVPM provides commands to manage the cache.

Viewing Cache Information
-------------------------

See packages, number of cached versions, and total size:

.. code-block:: bash

   ivpm cache info

Use ``--verbose`` for detailed version information:

.. code-block:: bash

   ivpm cache info --verbose

Example output::

   Cache directory: /home/user/.cache/ivpm
   Total size: 2.3 GB
   Packages: 15

     gtest:
       Versions: 3
       Size: 45 MB
     boost:
       Versions: 2
       Size: 856 MB

With ``--verbose``, each version is listed individually beneath its package
(``- <version>: <size>``) along with its ``stored`` and ``last linked``
timestamps.

If ``IVPM_CACHE`` is not set, specify the cache directory:

.. code-block:: bash

   ivpm cache info --cache-dir /path/to/cache

Cleaning the Cache
------------------

Remove cache entries that haven't been *used* in a given number of days:

.. code-block:: bash

   ivpm cache clean --days 7

**How an entry's age is measured.** "Used" means *last referenced into a
workspace*, not *first downloaded*. Each entry has a sidecar
(``<version>.meta.json``) recording two timestamps:

- ``stored`` — when the entry was first cached.
- ``last_linked`` — the most recent time ``ivpm update`` symlinked it into a
  ``packages/`` directory. This is refreshed on every cache hit **and** on a
  re-run that finds the dependency already linked, so a version shared by many
  live workspaces keeps being marked as used.

``clean`` prunes by ``max(stored, last_linked)`` age. An entry that was first
cached 90 days ago but linked into a project yesterday is **not** removed.
Entries created before this tracking existed (no sidecar) fall back to the
directory's modification time — exactly the previous behavior.

**Preview before deleting** with ``--dry-run``:

.. code-block:: bash

   ivpm cache clean --days 30 --dry-run     # list candidates, delete nothing
   ivpm cache clean --cache-dir /shared/cache --days 14

.. note::

   ``last_linked`` only advances when IVPM references an entry (a cache hit or
   a re-run of ``ivpm update`` that finds the dep already linked). It does not
   observe reads that bypass IVPM — e.g. a long-lived workspace that keeps
   building against a cached symlink without ever re-running ``ivpm update``.
   Re-running ``ivpm update`` periodically (as CI and normal workflows do)
   keeps in-use entries warm; otherwise raise ``--days`` to suit how often your
   workspaces refresh.

**What gets removed:**

- Version directories whose last-used age exceeds the threshold
- The entry's ``.meta.json`` sidecar (and any orphaned sidecars)
- Empty package directories after version removal
- Symlinks in projects will become broken and need ``ivpm update`` to recreate

.. note::

   :doc:`ivpm destroy <destroy>` only **unlinks** a workspace's cache-backed
   symlinks — it never deletes cache content, since other workspaces may share
   the same entry. Reclaiming an entry that is no longer referenced remains the
   job of ``ivpm cache clean``: once the last workspace referencing a version is
   destroyed, that version simply ages out and is pruned on the next ``clean``.

Practical Examples
==================

Example 1: Development with Caching
------------------------------------

**ivpm.yaml:**

.. code-block:: yaml

   package:
     name: my-project
     dep-sets:
       - name: default-dev
         deps:
           # Stable library - cache it
           - name: googletest
             url: https://github.com/google/googletest.git
             tag: v1.14.0
             cache: true
           
           # Co-developed library - don't cache
           - name: my-lib
             url: https://github.com/org/my-lib.git
             # No cache attribute - editable
           
           # Test data - cache it
           - name: test-vectors
             url: https://cdn.example.com/vectors.tar.gz
             cache: true

**Result:**

- ``googletest`` → Cached, read-only, symlinked
- ``my-lib`` → Not cached, full history, editable
- ``test-vectors`` → Cached, read-only, symlinked

Example 2: Shared Team Cache
-----------------------------

**Setup:**

.. code-block:: bash

   # Admin sets up shared cache
   sudo mkdir -p /shared/ivpm-cache
   sudo ivpm cache init --shared /shared/ivpm-cache
   sudo chown :devteam /shared/ivpm-cache
   
   # Team members add to their ~/.bashrc
   export IVPM_CACHE=/shared/ivpm-cache

**ivpm.yaml:**

.. code-block:: yaml

   deps:
     - name: large-dataset
       url: https://cdn.example.com/data-10GB.tar.gz
       cache: true
     
     - name: big-library
       url: https://github.com/org/big-lib.git
       branch: stable
       cache: true

**Benefits:**

- First team member downloads, all others get instant symlink
- Saves bandwidth and disk space across the team
- Managed with ``ivpm cache clean`` periodically

Example 3: Multi-Project Workflow
----------------------------------

**Scenario:** Working on three related projects

**Project A:**

.. code-block:: yaml

   deps:
     - name: common-lib
       url: https://github.com/org/common.git
       tag: v2.0
       cache: true

**Project B:**

.. code-block:: yaml

   deps:
     - name: common-lib
       url: https://github.com/org/common.git
       tag: v2.0
       cache: true

**Project C:**

.. code-block:: yaml

   deps:
     - name: common-lib
       url: https://github.com/org/common.git
       tag: v2.0
       cache: true

**Result:** All three projects share the same cached ``common-lib`` at commit 
corresponding to tag v2.0. Total disk usage: 1× instead of 3×.

When to Use Caching
===================

Use ``cache: true`` when:
--------------------------

✅ Stable, released versions (tags)
✅ Large repositories you don't modify
✅ Third-party dependencies
✅ Shared across multiple projects
✅ Team environments with shared cache
✅ CI/CD builds
✅ Binary releases from GitHub

Use ``cache: false`` when:
---------------------------

⚠️ You want read-only but not cached
⚠️ One-time use packages
⚠️ Testing package updates
⚠️ Temporary dependencies

Use no cache attribute when:
-----------------------------

✅ Actively developing/modifying
✅ Co-developed packages
✅ Need full Git history
✅ Making commits to the package
✅ Branching or rebasing

Performance Tips
================

1. **Enable caching for large dependencies** - Saves significant time

2. **Use shallow clones** when not caching - Combine ``depth: 1`` with ``cache: false``

3. **Shared cache for teams** - Set up once, benefits everyone

4. **Regular cleanup** - Schedule ``ivpm cache clean`` monthly

5. **Monitor cache size** - Use ``ivpm cache info`` periodically

6. **Cache stable versions** - Use tags or specific commits with ``cache: true``

7. **Don't cache development deps** - Leave packages you're actively modifying uncached

Command Reference
=================

cache init
----------

.. code-block:: text

   ivpm cache init [-s/--shared] [-f/--force] <cache_dir>

Options:

- ``-s, --shared``: Set group inheritance (``chmod g+s``) for shared cache usage
- ``-f, --force``: Force reinitialization of an existing directory

cache info
----------

.. code-block:: text

   ivpm cache info [-c/--cache-dir <dir>] [-v/--verbose]

Options:

- ``-c, --cache-dir``: Cache directory (default: ``$IVPM_CACHE``)
- ``-v, --verbose``: Show detailed version information (size, ``stored``,
  ``last linked``)

cache clean
-----------

.. code-block:: text

   ivpm cache clean [-c/--cache-dir <dir>] [-d/--days <n>] [-n/--dry-run]

Options:

- ``-c, --cache-dir``: Cache directory (default: ``$IVPM_CACHE``)
- ``-d, --days``: Remove entries unused (last-linked, see above) for more than
  this many days (default: 7)
- ``-n, --dry-run``: List entries that would be removed without deleting

See Also
========

- :doc:`deps_source` - A per-invocation "even-more-local cache" pointing at
  a sibling workspace's ``packages/`` directory, consulted *before* the
  shared cache when configured.
- :doc:`package_types` - Understanding cache attribute on different package types
- :doc:`getting_started` - Basic cache setup
- :doc:`troubleshooting` - Solutions to common problems
- :doc:`handlers` - How handlers process packages
