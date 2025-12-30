################
GitHub Releases
################

Overview
========

IVPM can automatically download and install packages from GitHub Releases, 
including platform-specific binaries. This is ideal for:

- Pre-built tools and utilities
- Platform-specific executables
- Large binary distributions
- Versioned releases without full Git history

IVPM automatically selects the appropriate binary for your platform (Linux, 
macOS, Windows) or falls back to source archives.

Basic Usage
===========

Simple Example
--------------

.. code-block:: yaml

    deps:
      - name: uv
        url: https://github.com/astral-sh/uv
        src: gh-rls

This downloads the latest release of ``uv`` for your platform.

With Version
------------

.. code-block:: yaml

    deps:
      - name: ruff
        url: https://github.com/astral-sh/ruff
        src: gh-rls
        version: "0.1.0"

Cached
------

.. code-block:: yaml

    deps:
      - name: tool
        url: https://github.com/org/tool
        src: gh-rls
        cache: true

Cached packages are stored by release tag and platform: ``<tag>_<platform>_<arch>``.

Version Selection
=================

IVPM supports multiple version selection strategies:

Latest Release
--------------

.. code-block:: yaml

    # Latest stable release (default)
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: latest

Excludes pre-releases by default. To include pre-releases:

.. code-block:: yaml

    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: latest
      prerelease: true

Exact Version
-------------

Match an exact release tag:

.. code-block:: yaml

    # Matches tag 'v1.2.3' or '1.2.3'
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: "1.2.3"
    
    # Also works with 'v' prefix
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: "v1.2.3"

Version Range
-------------

Use comparison operators:

.. code-block:: yaml

    # Minimum version
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: ">=1.0.0"
    
    # Maximum version (exclusive)
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: "<2.0"
    
    # Maximum version (inclusive)
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: "<=1.5.0"
    
    # Greater than
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: ">1.0"

**Behavior:**

- ``>=`` and ``>`` select the **earliest** matching version
- ``<=`` and ``<`` select the **latest** matching version

**Examples:**

Given releases: ``v1.0.0``, ``v1.1.0``, ``v1.2.0``, ``v2.0.0``

- ``>=1.1.0`` → selects ``v1.1.0``
- ``<2.0`` → selects ``v1.2.0``
- ``>1.0.0`` → selects ``v1.1.0``
- ``<=1.2.0`` → selects ``v1.2.0``

Version Parsing
---------------

IVPM parses version tags flexibly:

- ``v1.2.3`` → ``(1, 2, 3)``
- ``1.2.3`` → ``(1, 2, 3)``
- ``v1.2`` → ``(1, 2)``
- ``1.2.3.4`` → ``(1, 2, 3, 4)``

Comparison is done numerically on each component.

Pre-release Handling
====================

By default, pre-releases are excluded:

.. code-block:: yaml

    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: latest  # Excludes alpha, beta, rc

To include pre-releases:

.. code-block:: yaml

    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: latest
      prerelease: true

This affects all version selectors:

.. code-block:: yaml

    # Include prereleases in version range
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: ">=1.0.0"
      prerelease: true

Platform Selection
==================

IVPM automatically detects your platform and selects the appropriate binary asset.

Supported Platforms
-------------------

**Linux:**

- Detects glibc version
- Matches manylinux wheels (``manylinux_2_17_x86_64``, etc.)
- Matches generic Linux binaries (``linux-x86_64``, ``linux-aarch64``, etc.)
- Supported architectures: x86_64, aarch64, armv7l

**macOS:**

- Detects architecture (x86_64, arm64)
- Matches assets with "macos", "darwin", or "osx"
- Universal binaries supported

**Windows:**

- Matches assets with "windows", "win64", "win32"
- Supported architectures: x86_64, x86

Platform Detection Examples
---------------------------

**Linux x86_64 with glibc 2.31:**

IVPM looks for:

1. ``manylinux_2_31_x86_64`` (exact match)
2. ``manylinux_2_28_x86_64`` (older compatible)
3. ``manylinux_2_17_x86_64`` (manylinux2014)
4. Generic ``linux-x86_64`` or ``linux_x86_64`` binaries
5. Falls back to source

**macOS arm64 (M1/M2):**

IVPM looks for:

1. Assets with "arm64" or "aarch64" and "macos"/"darwin"/"osx"
2. Falls back to any macOS asset
3. Falls back to source

**Windows x86_64:**

IVPM looks for:

1. Assets with "x86_64" or "amd64" and "windows"/"win64"/"win"
2. Falls back to any Windows asset
3. Falls back to source

Linux Binary Naming Schemes
---------------------------

IVPM supports two Linux binary naming schemes:

**1. manylinux Tags (Preferred for Python wheels and glibc-aware packages):**

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Tag
     - glibc Version
     - Compatible Systems
   * - manylinux1
     - 2.5
     - Very old systems
   * - manylinux2010
     - 2.12
     - CentOS 6+
   * - manylinux2014
     - 2.17
     - CentOS 7+
   * - manylinux_2_28
     - 2.28
     - Ubuntu 22.04+
   * - manylinux_2_31
     - 2.31
     - Recent systems

IVPM selects the newest compatible manylinux version for your system.

**2. Generic Linux Naming (Common for C/C++ tools like protobuf):**

IVPM also recognizes generic Linux binary naming patterns:

- ``linux-x86_64`` or ``linux_x86_64``
- ``linux-aarch64`` or ``linux_aarch64`` or ``linux-arm64``
- ``linux-aarch_64`` (protobuf style)

Examples:

- ``protoc-33.2-linux-x86_64.zip``
- ``tool-v1.0-linux-aarch64.tar.gz``
- ``binary-linux_x86_64.tar.xz``

**Selection Priority:**

1. IVPM first looks for manylinux-tagged assets (glibc-aware)
2. If none found, falls back to generic Linux naming patterns
3. If neither found, falls back to source archives

Source Fallback
===============

If no platform-specific binary is found, IVPM falls back to source archives:

1. Try ``tarball_url`` (typically ``.tar.gz``)
2. Try ``zipball_url`` (typically ``.zip``)
3. Try any single generic asset
4. Fail with helpful error message

**Example:**

If a release has:

- Source: ``source.tar.gz``
- Windows: ``tool-windows.zip``
- Linux: ``tool-linux.tar.gz``
- macOS: ``tool-macos.tar.gz``

On FreeBSD (unsupported), IVPM will download ``source.tar.gz``.

Forcing Source Download
-----------------------

You can force IVPM to download the source archive instead of platform-specific binaries 
using the ``source`` option:

.. code-block:: yaml

    deps:
      - name: tool
        url: https://github.com/org/tool
        src: gh-rls
        version: latest
        source: true

This is useful when:

- You want to build from source for optimization or customization
- Binary releases don't work on your platform
- You need to audit or modify the source code
- You're distributing to users who will build themselves

When ``source: true`` is set:

1. IVPM skips all platform binary detection
2. Downloads ``tarball_url`` (GitHub's source tarball, typically ``.tar.gz``)
3. Falls back to ``zipball_url`` if tarball not available
4. Extracts to your packages directory

Complete Examples
=================

Example 1: Installing uv
-------------------------

.. code-block:: yaml

    deps:
      - name: uv
        url: https://github.com/astral-sh/uv
        src: gh-rls
        version: latest

**What happens:**

1. Queries GitHub API for latest release
2. Detects your platform (e.g., Linux x86_64 glibc 2.31)
3. Selects ``uv-x86_64-unknown-linux-gnu.tar.gz``
4. Downloads and extracts to ``packages/uv/``

**Usage:**

.. code-block:: bash

    $ ivpm update
    $ packages/uv/uv --version

Example 2: Specific Version with Cache
---------------------------------------

.. code-block:: yaml

    deps:
      - name: ruff
        url: https://github.com/astral-sh/ruff
        src: gh-rls
        version: "0.1.6"
        cache: true

**What happens:**

1. Finds release with tag ``v0.1.6`` or ``0.1.6``
2. Selects platform-specific binary
3. Caches with key ``0.1.6_linux_x86_64`` (example)
4. Symlinks from cache to ``packages/ruff/``

**Benefits:**

- Shared across projects
- Instant for subsequent projects
- Different platforms cache separately

Example 3: Version Range
-------------------------

.. code-block:: yaml

    deps:
      - name: tool
        url: https://github.com/org/tool
        src: gh-rls
        version: ">=1.2.0,<2.0"

**Interpretation:** Not directly supported as a single string.

**Workaround:** Use simple comparator:

.. code-block:: yaml

    # Get earliest >= 1.2.0
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: ">=1.2.0"

Example 4: Including Prereleases
---------------------------------

.. code-block:: yaml

    deps:
      - name: beta-tool
        url: https://github.com/org/tool
        src: gh-rls
        version: latest
        prerelease: true

**What happens:**

1. Queries all releases including pre-releases
2. Selects most recent (could be beta, rc, etc.)
3. Downloads platform-specific asset

Example 5: Protocol Buffers
---------------------------

.. code-block:: yaml

    deps:
      - name: protobuf
        url: https://github.com/protocolbuffers/protobuf
        src: gh-rls
        version: latest

**What happens:**

1. Queries GitHub API for latest release
2. Detects your platform (e.g., Linux x86_64)
3. Selects ``protoc-33.2-linux-x86_64.zip`` (generic Linux naming)
4. Downloads and extracts to ``packages/protobuf/``

**Usage:**

.. code-block:: bash

    $ ivpm update
    $ packages/protobuf/bin/protoc --version
    libprotoc 33.2

This demonstrates IVPM's support for generic Linux binary naming (not manylinux).

Example 6: Forcing Source Download
-----------------------------------

.. code-block:: yaml

    deps:
      - name: verilator
        url: https://github.com/verilator/verilator
        src: gh-rls
        version: latest
        source: true

**What happens:**

1. Queries GitHub API for latest release
2. Skips all binary detection (even if binaries are available)
3. Downloads source tarball (``tarball_url``)
4. Extracts to ``packages/verilator/``

**Use case:**

Building Verilator from source for custom optimization flags or when 
pre-built binaries aren't compatible with your system.

Example 7: Mixed Binaries and Source
-------------------------------------

.. code-block:: yaml

    package:
      name: verification-suite
      dep-sets:
        - name: default-dev
          deps:
            # Binary tool
            - name: verilator-bin
              url: https://github.com/verilator/verilator
              src: gh-rls
              version: latest
              cache: true
            
            # Source package
            - name: cocotb
              src: pypi
            
            # Git source
            - name: uvm-python
              url: https://github.com/pyuvm/pyuvm.git

Troubleshooting
===============

No Suitable Binary Found
-------------------------

**Error:** ``No suitable <platform> binary found``

**Causes:**

1. Release doesn't have a binary for your platform
2. Asset naming doesn't match IVPM's detection
3. Your glibc is too old (Linux)

**Solutions:**

1. **Check available assets:**

   Visit the GitHub release page and see what's available.

2. **Use source fallback:**

   If IVPM can't find a binary, it should fall back to source. If this fails, 
   check if ``tarball_url`` or ``zipball_url`` exists.

3. **Build from source:**

   .. code-block:: yaml

       # Switch to Git source
       - name: tool
         url: https://github.com/org/tool.git
         branch: v1.0.0

glibc Version Too New
---------------------

**Error:** Binary requires newer glibc

**Check your glibc:**

.. code-block:: bash

    $ ldd --version
    ldd (GNU libc) 2.31

**Solution:**

1. Upgrade your system
2. Use older release
3. Build from source

No Release Found
----------------

**Error:** ``Failed to find latest release`` or ``No release matches version spec``

**Causes:**

1. Repository has no releases
2. All releases are pre-releases (and ``prerelease: false``)
3. Version spec doesn't match any release

**Solutions:**

1. **Check releases exist:**

   Visit ``https://github.com/org/repo/releases``

2. **Enable prereleases:**

   .. code-block:: yaml

       - name: tool
         url: https://github.com/org/tool
         src: gh-rls
         prerelease: true

3. **Adjust version spec:**

   .. code-block:: yaml

       # Too specific
       version: "1.0.0"
       
       # More flexible
       version: ">=1.0"

Rate Limiting (GitHub API)
---------------------------

**Error:** ``HTTP 403`` or rate limit message

**Cause:** GitHub API rate limits (60 requests/hour for unauthenticated)

**Solution:**

Set ``GITHUB_TOKEN`` environment variable:

.. code-block:: bash

    export GITHUB_TOKEN=ghp_your_token_here
    ivpm update

Get a token from: https://github.com/settings/tokens

Asset Naming Issues
-------------------

**Problem:** IVPM doesn't detect your platform's binary

**Cause:** Asset naming doesn't match IVPM's patterns

**Example release assets:**

- ``tool-1.0-linux64.tar.gz`` ← Not detected (needs more specific pattern)
- ``tool-1.0-linux-x86_64.tar.gz`` ← Detected (generic Linux)
- ``tool-1.0-manylinux_2_17_x86_64.tar.gz`` ← Detected (manylinux)
- ``tool-1.0-macosx.tar.gz`` ← Detected (has "mac")
- ``tool-1.0.tar.gz`` ← Used as fallback source

**Supported naming patterns:**

- **Linux:** ``manylinux_X_Y_arch``, ``manylinux2014_arch``, ``linux-arch``, ``linux_arch``
- **macOS:** ``macos``, ``darwin``, ``osx`` with optional ``x86_64``, ``arm64``, ``aarch64``
- **Windows:** ``windows``, ``win64``, ``win32``, ``win`` with optional ``x86_64``, ``amd64``

**Workaround:**

Contact package maintainer to use standard naming patterns listed above.

Best Practices
==============

1. **Cache binary releases** - They're large and don't change

2. **Pin versions in production** - Use exact versions for reproducibility

3. **Use version ranges in development** - Stay updated with ``>=1.0``

4. **Check release assets** - Verify binaries exist for your platform before configuring

5. **Set GITHUB_TOKEN** - Avoid rate limiting in CI/CD

6. **Test on all platforms** - Ensure assets exist for Linux, macOS, Windows if deploying cross-platform

7. **Document platform requirements** - Let users know if only certain platforms are supported

8. **Provide source fallback** - Ensure releases include source archives

Advanced Configuration
======================

Forcing Source Archives
-----------------------

Use ``source: true`` to skip platform binary selection and download source:

.. code-block:: yaml

    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: "1.0.0"
      source: true

This downloads the GitHub source tarball (``.tar.gz``) or zipball (``.zip``) 
instead of platform-specific binaries.

**Benefits:**

- Full source code access for building with custom flags
- Avoid binary compatibility issues
- Works on any platform (not just those with prebuilt binaries)

**Tradeoffs:**

- Requires build tools and dependencies to be installed
- Slower initial setup compared to downloading prebuilt binaries
- May need manual build steps after download

Specific Asset File (Not Yet Implemented)
------------------------------------------

Future support for selecting specific asset:

.. code-block:: yaml

    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: "1.0.0"
      file: "tool-custom-x86_64.tar.gz"

Currently, IVPM auto-selects based on platform.

Custom URL Patterns
-------------------

For non-standard release URLs, use ``http`` source type:

.. code-block:: yaml

    - name: tool
      url: https://github.com/org/tool/releases/download/v1.0/tool.tar.gz
      src: http

See Also
========

- :doc:`package_types` - All source type options
- :doc:`caching` - Caching GitHub Release binaries
- :doc:`getting_started` - Basic dependency setup
