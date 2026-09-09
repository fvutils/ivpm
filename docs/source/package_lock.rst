####################
Package Lock File
####################

Overview
========

The **package lock file** (``packages/package-lock.json``) records the
fully-resolved identity of every fetched package.  Unlike ``ivpm.yaml``,
which describes *what you want* (e.g. ``branch: main``), the lock file
records *what you actually got* (e.g. ``commit_resolved: a1b2c3d``).

The lock file is written automatically as a side-effect of ``ivpm update``
and ``ivpm sync``.  It is a **local artifact** — it lives inside the
``packages/`` directory alongside the fetched packages and is not committed
to version control by default.

When you need to reproduce an exact workspace — for archival, CI, or
debugging — copy the lock file to a stable location and pass it back to
``ivpm update --lock-file``.

Key Properties
==============

* **Complete transitive closure** — every package fetched, including
  transitive dependencies, is recorded.  There is no need to scan
  sub-package ``ivpm.yaml`` files during reproduction.
* **Source-type aware** — each entry records the fields relevant to its
  source type (git commit hash, GitHub release tag, HTTP ETag, pip version,
  etc.).
* **Platform-agnostic version** — for GitHub Releases (``gh-rls``), the
  resolved *version tag* (e.g. ``v2.3.1``) is stored.  The correct
  platform binary is resolved at fetch time, so the same lock file works
  across Linux, macOS, and Windows.
* **Reproducibility flag** — packages sourced from local paths (``dir``,
  ``file``) are still recorded but are marked ``"reproducible": false``
  because they cannot be restored on a different machine.
* **Python package versions** — the Python handler contributes the
  complete set of pip-installed package versions under the
  ``python_packages`` key, enabling full Python environment reproducibility.
  This works regardless of whether ``pip`` or ``uv`` was used to install.
* **Integrity checksum** — a SHA-256 checksum of the lock file body is
  embedded in the ``sha256`` field.  IVPM warns (but does not fail) if the
  checksum does not match, allowing you to detect accidental manual edits.

Lock File Format
================

.. code-block:: json

    {
      "ivpm_lock_version": 1,
      "generated": "2024-01-15T10:23:00+00:00",
      "sha256": "...",
      "packages": {
        "my_git_lib": {
          "src": "git",
          "url": "https://github.com/org/my_git_lib.git",
          "branch": "main",
          "tag": null,
          "commit_requested": null,
          "commit_resolved": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
          "cache": null,
          "resolved_by": "root",
          "dep_set": "default",
          "reproducible": true
        },
        "my_tool": {
          "src": "gh-rls",
          "url": "https://github.com/org/my_tool",
          "version_requested": "latest",
          "version_resolved": "v2.3.1",
          "cache": null,
          "resolved_by": "root",
          "dep_set": null,
          "reproducible": true
        },
        "an_archive": {
          "src": "http",
          "url": "https://example.com/archive.tar.gz",
          "etag": "abc123",
          "last_modified": "Wed, 15 Jan 2024 00:00:00 GMT",
          "cache": null,
          "resolved_by": "my_git_lib",
          "dep_set": null,
          "reproducible": true
        },
        "requests": {
          "src": "pypi",
          "version_requested": ">=2.0",
          "version_resolved": "2.31.0",
          "resolved_by": "root",
          "dep_set": null,
          "reproducible": true
        },
        "local_lib": {
          "src": "dir",
          "path": "../../shared/local_lib",
          "resolved_by": "root",
          "dep_set": null,
          "reproducible": false
        }
      },
      "python_packages": {
        "certifi": "2024.1.1",
        "charset-normalizer": "3.3.2",
        "idna": "3.6",
        "requests": "2.31.0",
        "urllib3": "2.1.0"
      }
    }

Fields common to all entries:

``src``
    Source type: ``git``, ``gh-rls``, ``http``, ``pypi``, ``dir``, ``file``, etc.

``resolved_by``
    The package that first introduced this dependency.  ``"root"`` means it
    came directly from the top-level ``ivpm.yaml``.  The *first* (highest-
    priority) specification wins; lower-level duplicates are ignored.

``dep_set``
    The dependency-set name used when loading sub-dependencies from this
    package.

``reproducible``
    ``true`` for packages that can be restored on any machine.
    ``false`` for ``dir`` and ``file`` packages (local paths).

``from_ivpm_source`` *(optional)*
    Present only on packages contributed by a ``src: ivpm.yaml`` dep-set
    factory.  Records the factory's ``"<url>#<dep-set>"`` so ``ivpm show deps``
    can explain where the dependency came from.  See :ref:`ivpm-yaml-factory`.

``resolved_on`` *(optional)*
    See below.

Platform-Specific Entries (``resolved_on``)
===========================================

For ``http``, ``tgz``, ``txz``, ``zip`` and ``jar`` entries the ``url`` *is*
the artifact -- unlike ``gh-rls``, which records the repository and the
release tag and re-runs asset selection on each machine.  So when such a URL
was built from a platform variable (see :doc:`variables`), the entry records
the platform that resolved it:

.. code-block:: json

    {
      "src": "url",
      "url": "https://.../linux/f04ea.../wasm-binaries.tar.xz",
      "etag": "\"-CNLglcuhzJYDEAE=\"",
      "resolved_on": "linux-x86_64"
    }

The value is ``"{os}-{arch}"`` -- the same string a manifest sees as
``${{ivpm_platform}}``, which is the platform the entry was resolved *for*.
With ``-D ivpm_os=macos`` on a Linux machine the entry is tagged
``macos-arm64``, not ``linux-x86_64``; tagging it with the resolving machine
would make it look native on the next bare run there and it would never be
re-resolved.

``resolved_on`` is written **only** when the entry actually used a platform
variable, so platform-independent packages and every lock file written before
this feature existed are unchanged, and an entry without it is honoured
exactly as before.

When it is present and does not match the current platform:

- On ``ivpm update``, the entry is treated as not matching, and the package
  is re-resolved from the manifest for this platform.  A mismatch is not an
  error -- re-resolution is the expected outcome.
- On ``ivpm update --lock`` (reproduction), the lock is the only source of
  packages, so there is nothing to re-resolve from and IVPM reports an error
  naming both platforms.  Regenerate the lock on this platform.

Without this, a lock committed from Linux would hand a macOS teammate a Linux
tarball with a perfectly valid ETag and no error anywhere.

Dep-Set Factory Sources (``ivpm_sources``)
==========================================

A ``src: ivpm.yaml`` dependency (a :ref:`dep-set factory <ivpm-yaml-factory>`)
contributes deps but has **no packages-dir representation**, so it is not listed
under ``packages``.  Instead, the lock file records it under a top-level
``ivpm_sources`` map keyed by the factory ``url``:

.. code-block:: json

    {
      "packages": {
        "pyyaml": {
          "src": "pypi",
          "version_resolved": "6.0.1",
          "resolved_by": "core-tools",
          "from_ivpm_source": "https://example.com/tools.yaml#core",
          "reproducible": true
        }
      },
      "ivpm_sources": {
        "https://example.com/tools.yaml": {
          "src": "ivpm.yaml",
          "dep_set": "core",
          "fingerprint": "sha256:1a2b3c…",
          "reproducible": true,
          "virtual": true
        }
      }
    }

Each contributed leaf (e.g. ``pyyaml`` above) appears in the normal ``packages``
map with a ``from_ivpm_source`` provenance field.  The ``fingerprint`` is the
factory's resolved etag / last-modified, or a content ``sha256`` — it lets a
re-resolve detect that the factory's dep-set membership changed upstream even
though each leaf re-pins independently.

External Manifest Source (``source_manifest``)
==============================================

When a workspace is created with ``ivpm update --from <path-or-url>`` (installing
from an external manifest rather than a local ``ivpm.yaml``), the external
manifest is **not** copied into the workspace.  Instead, the lock file records
where the workspace came from in a top-level ``source_manifest`` block:

.. code-block:: json

    {
      "ivpm_lock_version": 1,
      "source_manifest": {
        "from": "https://example.com/acme/ivpm.yaml",
        "dep_set": "gui-tools"
      },
      "packages": { }
    }

``from`` is the original ``--from`` argument (path or URL) and ``dep_set`` is the
resolved dependency set that was installed.  This makes the workspace
self-describing without a local manifest: tooling can locate the lock file by
knowing the deps directory and re-resolve against the recorded source.  It is
also what makes a bare ``ivpm update`` in such a workspace work -- the recorded
source (and the deps-dir it was installed into) is replayed, so ``--from`` need
not be repeated.  The field is absent for ordinary (local ``ivpm.yaml``)
workspaces.

Tool-Directory Install (``install_mode``, ``sources``)
=======================================================

``ivpm install`` builds a :doc:`shared tool directory <tool_directories>` from
**several** manifests, which ``source_manifest`` (a single source) cannot
express.  Those workspaces record a richer, ordered spec instead:

.. code-block:: json

    {
      "ivpm_lock_version": 1,
      "install_mode": "toolchain",
      "sources": [
        {"from": "https://edapack.github.io", "as": "edapack",
         "dep_sets": ["digital-sim", "digital-formal"], "definitions": {}},
        {"from": "https://mycorp.internal/tools", "as": "corp",
         "dep_sets": ["common"], "definitions": {}}
      ],
      "collision_resolutions": {"verilator": "corp"},
      "packages": { }
    }

``install_mode``
    ``"toolchain"`` -- the deps-dir is the root, with no project above it.

``sources``
    The ordered source list.  Order is significant: it determines ``PATH``
    precedence in the generated ``packages.envrc``, so a replay must preserve
    it.  Each entry carries the ``--from`` value, the resolved alias (``as``),
    the selected ``dep_sets``, and any per-source ``-D`` ``definitions``.

``collision_resolutions``
    Every ``--resolve <package>=<source>`` the user supplied, including any
    that did not fire on this run -- a resolution that is stale today may be
    needed again after an upstream bump, and dropping it would silently
    re-break the replay.

These are **additive** top-level keys, so ``ivpm_lock_version`` is unchanged; a
reader that does not know them is unaffected.  ``sources`` takes precedence
over ``source_manifest``, which remains the single-source form written by
``ivpm update --from``.

Root Project (``root``)
=======================

When a workspace is created by ``ivpm clone``, the lock file records which
**clone provider** produced the root in a top-level ``root`` block:

.. code-block:: json

    {
      "ivpm_lock_version": 1,
      "root": {
        "provider": "git",
        "src": "https://github.com/fvutils/ivpm",
        "resolved_revision": "138f994..."
      },
      "packages": { }
    }

* ``provider`` — the registered name of the clone provider (e.g. ``git``).  This
  is the only field ``ivpm status`` needs to select the provider that describes
  the root project (see :doc:`clone_providers`).
* ``src`` — the original locator passed to ``ivpm clone``.  Optional; for
  display and diagnostics.
* ``resolved_revision`` — the concrete revision the provider reported.  Optional.
* ``config`` — optional forwarded configuration that makes a **bare** workspace
  (no root ``ivpm.yaml``) self-describing.  Present only when the provider
  returned a ``root_config`` on its ``CloneResult``:

  .. code-block:: json

      "root": {
        "provider": "myvcs",
        "src": "myvcs://my_app",
        "config": {
          "default_package": {
            "name": "my_app",
            "deps-dir": "import",
            "dep-sets": [ { "name": "default",
                            "deps": [ { "name": "my_lib",
                                        "url": "https://github.com/acme/my_lib.git",
                                        "branch": "main" } ] } ]
          },
          "handler_overlay": { "example-handler": { "items": ["a"] } }
        }
      }

  - ``default_package`` — a synthesized ``package:`` mapping used to drive
    ``ivpm update`` when the tree has no ``ivpm.yaml``.
  - ``handler_overlay`` — handler config merged underneath the workspace's own
    ``ivpm.yaml`` (the local manifest wins on conflict).

  On a later ``ivpm update`` with no ``ivpm.yaml``, IVPM reproduces the driving
  configuration from this block (see :ref:`bare-workspaces`).

The block is **additive** (no lock-version bump) and **absent** for workspaces
not created by ``ivpm clone`` — those rely on ``ivpm status`` probing the root.
It is written by ``ivpm clone`` and **preserved** across subsequent
``ivpm update`` re-writes of the lock.

Change Detection
================

When ``ivpm update`` runs and a lock file already exists, IVPM compares the
**user-specified** fields in ``ivpm.yaml`` against the corresponding lock
entry for each package:

* **git**: ``url``, ``branch``, ``tag``, ``commit``, ``cache``
* **gh-rls**: ``url``, ``version``
* **http**: ``url``
* **pypi**: ``version``

If the specs **match**, the package is considered up to date and no network
calls are made.  If the specs **differ** (e.g. you changed ``branch: main``
to ``branch: dev``), IVPM reports the differences but does **not** re-fetch
unless you also pass ``--refresh-all`` or ``--force``.

.. code-block:: bash

    # Reports differences, takes no action
    $ ivpm update

    # Re-fetches packages whose specs changed
    $ ivpm update --refresh-all

    # Re-fetches everything; suppresses safety errors
    $ ivpm update --force

Reproduction Mode
=================

Pass a lock file as input to ``ivpm update`` to reproduce an exact workspace:

.. code-block:: bash

    $ ivpm update --lock-file ./ivpm.lock

In this mode:

* ``ivpm.yaml`` is **not read** for packages — the lock file is the sole
  source of truth.
* Every package is fetched at its pinned resolved version:

  * **git** — shallow clone at ``commit_resolved``; falls back to full clone
    if the server does not support arbitrary commit fetch.
  * **gh-rls** — fetches the exact ``version_resolved`` tag; platform binary
    selection still occurs at fetch time.
  * **http** — fetches the same URL; warns if the ETag/Last-Modified header
    no longer matches.
  * **pypi** — installs ``version_resolved`` exactly.

* Sub-package ``ivpm.yaml`` files are **not** scanned; the lock file already
  encodes the complete transitive closure.
* The ``packages/`` destination is still determined by the active
  ``ivpm.yaml`` (or the CLI default), not the lock file.
* Cache interaction is unchanged: if the pinned version is already in the
  IVPM cache, it is reused.

.. note::

    The lock file does not encode absolute paths.  ``deps_dir`` can differ
    between the machine that generated the lock and the machine that
    consumes it.

CI/CD Reproducibility Pattern
==============================

The recommended workflow for CI reproducibility:

**On the developer workstation:**

.. code-block:: bash

    # 1. Fetch/update packages normally
    $ ivpm update

    # 2. Archive the lock file (packages/ may be .gitignore'd)
    $ cp packages/package-lock.json ./ivpm.lock

    # 3. Commit the lock file alongside ivpm.yaml
    $ git add ivpm.lock
    $ git commit -m "Update dependency lock file"

**In CI:**

.. code-block:: bash

    # Reproduce the exact workspace from the committed lock file
    $ ivpm update --lock-file ./ivpm.lock

**GitHub Actions example:**

.. code-block:: yaml

    - name: Install IVPM
      run: pip install ivpm

    - name: Reproduce workspace from lock
      run: ivpm update --lock-file ./ivpm.lock

    - name: Run tests
      run: direnv exec . pytest

Deciding Whether a Package Needs Loading
========================================

On every ``update``, IVPM decides *per package* whether to fetch it, reuse what
is already there, or re-examine it.  Two sources feed that decision, and the
order matters:

**Residency comes from the filesystem.**
    It is the only authority on whether content is present.  A package
    directory you deleted by hand is re-fetched no matter what the lock says.

**Identity comes from the lock file.**
    It records what IVPM believes is there.  It can only ever *add* information
    to a disk observation — it can never assert that something is present.

The resulting states:

.. list-table::
   :header-rows: 1
   :widths: 22 14 64

   * - State
     - Action
     - Meaning
   * - ``ABSENT``
     - fetch
     - Nothing at the path.
   * - ``PREPARED_EMPTY``
     - fetch
     - The directory exists but is empty — not a loaded package.  This is what
       a :ref:`package preparer <extending_ivpm:Contributing a Package Preparer>`
       leaves behind.
   * - ``PATCHED``
     - reconcile
     - A patch set is declared, or a patch manifest is on disk.  Evaluated
       before every residency test, so a changed patch set is always picked up.
   * - ``RESIDENT_LINK``
     - reuse
     - A symlink from the cache or a deps-source.
   * - ``RESIDENT_MATCHING``
     - reuse
     - Populated, and its spec matches the lock.
   * - ``RESIDENT_UNTRACKED``
     - reuse
     - Populated, with no lock entry — a manual checkout, or a workspace whose
       lock was deleted.  Never treated as licence to overwrite.
   * - ``RESIDENT_DRIFTED``
     - reuse
     - Populated, but its spec has changed since it was locked.  Reported, not
       re-fetched: re-fetching would discard whatever is in the tree.

Spec drift
----------

When a dependency's ``url``, ``branch``, ``commit``, ``version`` or patch set
changes in ``ivpm.yaml`` after it has been fetched, IVPM reports it after the
fetch phase and keeps the existing content:

.. code-block:: text

    note: The following packages have changed specs vs package-lock.json:
    note:   fast-dsp
    note: No packages re-fetched; their existing content was kept.

Because the check now happens as each package is decided rather than up front,
it covers **transitive dependencies and every nested scope** — not just the
dependencies listed in the root project's dep-set.

To pick up the new spec, remove the package directory and re-run ``ivpm
update``.  Inspect the tree first if you have local work in it.

Syncing and the Lock File
=========================

``ivpm sync`` pulls the latest upstream commits into all editable (writable)
git packages.  After sync completes, the lock file is automatically
regenerated to reflect the new ``HEAD`` commit of each updated package.

This means ``packages/package-lock.json`` always represents the **current
state** of your packages directory, whether packages were fetched by
``update`` or brought forward by ``sync``.

.. code-block:: bash

    $ ivpm sync
    # ... merges upstream changes into editable packages ...
    # Note: Updated package-lock.json after sync

Python Package Version Locking
===============================

After Python packages are installed into the IVPM-managed virtual
environment, the Python handler queries the venv for all installed package
versions using ``pip list``.  This works regardless of whether ``pip`` or
``uv`` was used to install packages — both write to the same venv
``site-packages`` directory.

The result is stored under the top-level ``python_packages`` key in the lock
file.  In reproduction mode (``--lock-file``), PyPI packages are pinned to
their ``version_resolved`` value, which corresponds to the version recorded
in ``python_packages``.

.. note::

    ``python_packages`` records *all* packages installed in the venv,
    including transitive pip dependencies that were not explicitly listed in
    ``ivpm.yaml``.

Format Versioning
=================

The ``ivpm_lock_version`` field guards against schema changes.  IVPM writes
version **2** and reads versions 1 and 2; anything else is rejected with a
clear error:

.. code-block:: text

    ValueError: package-lock.json version 99 is not supported (expected one
    of: 1, 2). Please regenerate the lock file with this version of ivpm.

Version 2 keys the ``packages`` map by **scope path** rather than by bare
package name, and records the package name in an explicit ``name`` field.  In
a flat workspace a scope path *is* the bare name, so a v2 lock for a flat
project is shape-identical to a v1 one:

.. code-block:: text

    "packages": {
      "libA":                { "name": "libA", ... },
      "toolB":               { "name": "toolB", "deps_mode": "nested",
                               "deps_dir": "packages", ... },
      "toolB/packages/libA": { "name": "libA", "scope": "toolB/packages", ... }
    }

The scope-path key is what lets one lock record two versions of one package.
Entries may also carry ``cycle_elided``, naming the enclosing scope that
already provides the package.  See :doc:`nested_deps`.

See Also
========

* :doc:`workflows` — Reproducible build and CI workflows
* :doc:`caching` — How IVPM caches packages
* :doc:`reference` — Full ``ivpm update`` / ``ivpm sync`` option reference
