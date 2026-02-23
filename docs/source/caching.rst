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
     - No
     - No
     - One-time use, temp deps
   * - (unspecified)
     - No
     - Yes
     - Yes
     - Development, co-development

**cache: true**
    - Package stored in ``$IVPM_CACHE``
    - Symlinked into ``packages/``
    - Read-only (cannot modify)
    - No Git history (shallow clone)
    - Shared across projects

**cache: false**
    - Not cached
    - Cloned directly to ``packages/``
    - Read-only (cannot modify)
    - No Git history (shallow clone)
    - Cannot be edited

**cache not specified**
    - Not cached
    - Cloned directly to ``packages/``
    - Full Git history
    - Can be modified and committed
    - Development-friendly

Cache Backends
==============

IVPM supports a pluggable cache backend system.  The backend controls
*where* package data is stored and retrieved.

Available Backends
------------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Backend
     - Description
   * - ``auto``
     - Auto-detect: use ``gha`` if running inside GitHub Actions, else
       ``filesystem`` if ``IVPM_CACHE`` is set, else no caching.
       **This is the default.**
   * - ``filesystem``
     - Local directory cache.  Requires ``IVPM_CACHE`` environment variable
       or ``local-dir`` in the ``cache:`` YAML block.
   * - ``gha``
     - GitHub Actions cache service.  Two-level cache: local filesystem (L1)
       plus the GHA REST API (L2).  Requires ``ACTIONS_CACHE_URL`` and
       ``ACTIONS_RUNTIME_TOKEN`` (set automatically by GHA runners).
   * - ``none``
     - Disable caching entirely, even if ``IVPM_CACHE`` is set.

Selecting a Backend
-------------------

There are three ways to select a backend, evaluated in priority order:

1. **CLI flag** (highest priority):

   .. code-block:: bash

       ivpm update --cache-backend gha
       ivpm update --cache-backend filesystem
       ivpm update --cache-backend none

2. **Environment variable:**

   .. code-block:: bash

       export IVPM_CACHE_BACKEND=gha   # or filesystem | none | auto

3. **``cache:`` block in ``ivpm.yaml``** (see :ref:`cache-yaml-block` below).

4. **Auto-detect** (lowest priority): GHA if inside GitHub Actions, otherwise
   filesystem if ``IVPM_CACHE`` is set, otherwise no caching.

GitHub Actions Backend
----------------------

When running inside a GitHub Actions workflow, IVPM can automatically exploit
the GHA cache service — no ``actions/cache`` step required.

**How it works:**

- Each package version is stored as an individual GHA cache entry, keyed by
  ``ivpm-pkg-{OS}-{name}-{version}``.
- On the first run, packages are fetched normally and uploaded to the GHA cache
  in the background (async).
- On subsequent runs (e.g. later workflow runs or parallel matrix jobs), IVPM
  restores packages from the GHA cache into a local L1 directory before linking.
- The Python virtual environment and pip wheel cache are also saved/restored.

**Auto-detection:** The GHA backend activates automatically when both
``ACTIONS_CACHE_URL`` and ``ACTIONS_RUNTIME_TOKEN`` are set.  These are
injected by GitHub Actions runners.  No workflow changes are needed.

**Manual activation** (e.g., in a self-hosted runner without auto env vars):

.. code-block:: bash

    ivpm update --cache-backend gha

.. _cache-yaml-block:

``cache:`` Block in ``ivpm.yaml``
---------------------------------

Fine-tune backend behaviour in ``ivpm.yaml`` under the top-level ``package:``
key:

.. code-block:: yaml

    package:
      name: my-project
      cache:
        backend: auto           # auto | filesystem | gha | none
        local-dir: ~/.cache/ivpm   # override IVPM_CACHE
        key-prefix: myproject   # prefix for GHA cache keys (default: ivpm)
        include-python-venv: true   # save/restore Python venv (default: true)
        include-pip-cache: true     # save/restore pip wheel cache (default: true)
        max-age-days: 30            # local cache eviction threshold (default: 30)

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Key
     - Default
     - Description
   * - ``backend``
     - ``auto``
     - Which backend to use (``auto`` / ``filesystem`` / ``gha`` / ``none``)
   * - ``local-dir``
     - ``$IVPM_CACHE``
     - Local directory for the filesystem or GHA L1 cache
   * - ``key-prefix``
     - ``ivpm``
     - Short string prepended to all GHA cache keys
   * - ``include-python-venv``
     - ``true``
     - Whether to save/restore the Python virtual environment
   * - ``include-pip-cache``
     - ``true``
     - Whether to save/restore the pip/uv wheel cache
   * - ``max-age-days``
     - ``30``
     - Remove local cache entries older than this many days on clean

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

If ``IVPM_CACHE`` is not set, IVPM will fall back to full clones (no caching) with 
a warning message.

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

This ensures new files inherit the group ownership of the cache directory.

Cache Organization
==================

The cache is organized by package name, with version-specific subdirectories:

- For Git packages, the version is the commit hash
- For HTTP packages, the version is derived from the Last-Modified header or ETag
- For GitHub Releases, the version includes the release tag and platform info

Example structure::

   $IVPM_CACHE/
   ├── gtest/
   │   ├── abc123def456.../           # Git commit hash
   │   └── 789xyz012abc.../           # Different commit
   ├── boost/
   │   ├── Thu_01-Jan-2024_120000/   # HTTP Last-Modified timestamp
   │   └── Fri_15-Mar-2024_093000/
   └── uv/
       ├── 0.1.0_linux_x86_64/       # GitHub Release with platform
       └── 0.1.1_darwin_arm64/

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

**Cache key:** Last-Modified header (converted to safe filename) or ETag

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

**Cache key:** ``<release-tag>_<platform>_<architecture>``

Examples:

- ``0.1.0_linux_x86_64``
- ``0.1.0_darwin_arm64``
- ``0.1.0_windows_x86_64``

This allows different platforms to cache different binaries for the same release.

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
   Total packages: 15
   Total versions: 47
   Total size: 2.3 GB
   
   Package: gtest
     Versions: 3
     Size: 45 MB
   
   Package: boost
     Versions: 2
     Size: 856 MB

If ``IVPM_CACHE`` is not set, specify the cache directory:

.. code-block:: bash

   ivpm cache info --cache-dir /path/to/cache

Cleaning the Cache
------------------

Remove cache entries older than a specified number of days:

.. code-block:: bash

   ivpm cache clean --days 7

This removes entries that haven't been accessed in 7 days (the default).

**To remove old entries:**

.. code-block:: bash

   # Remove entries older than 30 days
   ivpm cache clean --days 30
   
   # Use a specific cache directory
   ivpm cache clean --cache-dir /shared/cache --days 14

**What gets removed:**

- Version directories with access time (atime) older than specified days
- Empty package directories after version removal
- Symlinks in projects will become broken and need ``ivpm update`` to recreate

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

Troubleshooting
===============

Cache Not Working
-----------------

**Problem:** Packages not being cached

**Check:**

1. Is ``IVPM_CACHE`` set?

   .. code-block:: bash

       echo $IVPM_CACHE

2. Does the cache directory exist?

   .. code-block:: bash

       ls -la $IVPM_CACHE

3. Is ``cache: true`` in your ``ivpm.yaml``?

4. Check IVPM output for cache hit/miss messages

**Solution:** If not set, initialize:

.. code-block:: bash

   export IVPM_CACHE=~/.cache/ivpm
   ivpm cache init $IVPM_CACHE
   ivpm update  # Re-run to use cache

Broken Symlinks
---------------

**Problem:** Symlinks in ``packages/`` pointing to non-existent cache entries

**Cause:** Cache was cleaned or manually deleted

**Solution:**

.. code-block:: bash

   # Remove broken symlinks
   find packages/ -type l ! -exec test -e {} \; -delete
   
   # Re-run update to recreate
   ivpm update

Permission Denied (Shared Cache)
---------------------------------

**Problem:** Cannot write to shared cache

**Check permissions:**

.. code-block:: bash

   ls -la /shared/ivpm-cache

**Solution:**

.. code-block:: bash

   # Add yourself to the group
   sudo usermod -a -G devteam $USER
   
   # Re-login or newgrp
   newgrp devteam
   
   # Verify permissions
   ls -la /shared/ivpm-cache

Cache Growing Too Large
------------------------

**Problem:** Cache directory taking too much space

**Solutions:**

.. code-block:: bash

   # View cache size
   ivpm cache info
   
   # Clean old entries
   ivpm cache clean --days 30
   
   # Manual cleanup (advanced)
   du -sh $IVPM_CACHE/*/ | sort -h

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

   ivpm cache info [-c/--cache-dir <dir>] [-v/--verbose] [--backend {auto,filesystem,gha,none}]

Options:

- ``-c, --cache-dir``: Cache directory (default: ``$IVPM_CACHE``)
- ``-v, --verbose``: Show detailed version information
- ``--backend``: Which backend to query (default: ``auto``)

cache clean
-----------

.. code-block:: text

   ivpm cache clean [-c/--cache-dir <dir>] [-d/--days <n>] [--backend {auto,filesystem,gha,none}]

Options:

- ``-c, --cache-dir``: Cache directory (default: ``$IVPM_CACHE``)
- ``-d, --days``: Remove entries older than this many days (default: 7)
- ``--backend``: Which backend to clean (default: ``auto``)

See Also
========

- :doc:`package_types` - Understanding cache attribute on different package types
- :doc:`getting_started` - Basic cache setup

