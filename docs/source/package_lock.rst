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
      run: ivpm activate -c "pytest"

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

The ``ivpm_lock_version`` field guards against schema changes.  IVPM will
reject lock files with an unrecognised version number and emit a clear error:

.. code-block:: text

    ValueError: package-lock.json version 2 is not supported (expected 1).
    Please regenerate the lock file with this version of ivpm.

See Also
========

* :doc:`workflows` — Reproducible build and CI workflows
* :doc:`caching` — How IVPM caches packages
* :doc:`reference` — Full ``ivpm update`` / ``ivpm sync`` option reference
