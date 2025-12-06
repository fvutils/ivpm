#######
Caching
#######

IVPM supports caching of package data to reduce network traffic and disk space 
for shared dependencies that aren't being edited.

Configuration
=============

Caching is enabled by setting the ``IVPM_CACHE`` environment variable to point 
to the cache directory:

.. code-block:: bash

   export IVPM_CACHE=/path/to/cache

Cache Organization
==================

The cache is organized by package name, with version-specific subdirectories:

- For Git packages, the version is the commit hash
- For HTTP packages, the version is derived from the Last-Modified header or ETag

Example structure::

   $IVPM_CACHE/
   ├── gtest/
   │   ├── abc123def.../
   │   └── 789xyz012.../
   └── another-package/
       └── version-hash/

Package Caching
===============

To enable caching for a package, set the ``cache`` attribute in your ``ivpm.yaml``:

.. code-block:: yaml

   - name: default-dev
     deps:
     - name: gtest
       url: https://github.com/google/google-test.git
       cache: true

The ``cache`` attribute can be ``true``, ``false``, or unspecified (defaults to 
package type behavior).

Cached packages are always read-only and are symlinked into the dependencies 
directory.

Git Packages
------------

For GitHub URLs with caching enabled:

1. IVPM queries the API to get the commit hash of the target branch
2. If the commit exists in the cache, it symlinks to the dependencies directory
3. If not cached, clones without history, makes read-only, and symlinks

**Note:** Only GitHub repositories are currently cacheable for Git packages.

HTTP/URL Packages
-----------------

For cacheable HTTP URLs (e.g., ``.tar.gz`` files):

1. IVPM fetches the Last-Modified date or ETag via the URL
2. If a matching entry exists in the cache, it symlinks to the dependencies directory
3. If not cached, downloads, unpacks, makes read-only, and symlinks

Cache Management
================

IVPM provides commands to manage the cache.

Initializing a Cache
--------------------

Create a new cache directory:

.. code-block:: bash

   ivpm cache init /path/to/cache

For shared environments where multiple users access the cache, use the 
``--shared`` option to set group inheritance permissions (``chmod g+s``):

.. code-block:: bash

   ivpm cache init --shared /path/to/shared/cache

This ensures new files inherit the group ownership of the cache directory.

Viewing Cache Information
-------------------------

See packages, number of cached versions, and total size:

.. code-block:: bash

   ivpm cache info

Use ``--verbose`` for detailed version information:

.. code-block:: bash

   ivpm cache info --verbose

If ``IVPM_CACHE`` is not set, specify the cache directory:

.. code-block:: bash

   ivpm cache info --cache-dir /path/to/cache

Cleaning the Cache
------------------

Remove cache entries older than a specified number of days:

.. code-block:: bash

   ivpm cache clean --days 7

This removes entries that haven't been accessed in 7 days (the default).

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
- ``-v, --verbose``: Show detailed version information

cache clean
-----------

.. code-block:: text

   ivpm cache clean [-c/--cache-dir <dir>] [-d/--days <n>]

Options:

- ``-c, --cache-dir``: Cache directory (default: ``$IVPM_CACHE``)
- ``-d, --days``: Remove entries older than this many days (default: 7)
