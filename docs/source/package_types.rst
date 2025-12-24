#######################
Package Types & Sources
#######################

Understanding Package Attributes
=================================

Every IVPM package has two fundamental attributes:

1. **Source Type** (``src``): How to fetch the package
2. **Package Type** (``type``): What the package contains

These are independent - any source type can provide any package type.

Source Types
============

Source types determine how IVPM obtains a package.

Git (``git``)
-------------

Clone from a Git repository. Supports branches, tags, commits, and submodules.

**Basic usage:**

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        src: git  # Usually auto-detected

**Attributes:**

``branch``
    Checkout a specific branch (default: repository default branch)

``tag``
    Checkout a specific tag

``commit``
    Checkout a specific commit hash

``depth``
    Clone depth for shallow clones (e.g., ``depth: 1``)

``anonymous``
    Use HTTPS instead of SSH (default: false, converts to SSH)

``cache``
    Enable caching (see :doc:`caching`)

**Examples:**

.. code-block:: yaml

    # Specific branch
    - name: my-lib
      url: https://github.com/org/my-lib.git
      branch: develop
    
    # Specific tag (release)
    - name: my-lib
      url: https://github.com/org/my-lib.git
      tag: v1.2.3
    
    # Specific commit
    - name: my-lib
      url: https://github.com/org/my-lib.git
      commit: abc123def456
    
    # Shallow clone (faster, less history)
    - name: my-lib
      url: https://github.com/org/my-lib.git
      depth: 1
    
    # Anonymous clone (HTTPS, no SSH key needed)
    - name: my-lib
      url: https://github.com/org/my-lib.git
      anonymous: true
    
    # Cached (read-only, symlinked from cache)
    - name: my-lib
      url: https://github.com/org/my-lib.git
      branch: v1.0
      cache: true

**Submodules:** Automatically initialized if ``.gitmodules`` is present.

**URL conversion:** By default, HTTPS URLs are converted to SSH format 
(``git@github.com:org/repo.git``) unless ``anonymous: true`` is set.

PyPI (``pypi``)
---------------

Install from the Python Package Index using pip or uv.

**Basic usage:**

.. code-block:: yaml

    deps:
      - name: requests
        src: pypi
    
    # No URL needed for PyPI packages

**Attributes:**

``version``
    Version specification (e.g., ``>=1.0,<2.0``)

**Examples:**

.. code-block:: yaml

    # Latest version
    - name: requests
      src: pypi
    
    # Specific version
    - name: numpy
      src: pypi
      version: ">=1.20,<2.0"
    
    # Exact version
    - name: pytest
      src: pypi
      version: "==7.4.0"

**Install behavior:** Installed into ``packages/python/`` virtual environment.

HTTP/URL (``http``)
-------------------

Download an archive file via HTTP or HTTPS.

**Basic usage:**

.. code-block:: yaml

    deps:
      - name: my-package
        url: https://example.com/releases/pkg-1.0.tar.gz
        src: http  # Usually auto-detected

**Supported formats:**

- ``.tar.gz`` / ``.tgz``
- ``.tar.xz`` / ``.txz``
- ``.tar.bz2``
- ``.zip``
- ``.jar`` (not unpacked by default)

**Attributes:**

``unpack``
    Whether to unpack the archive (default: true, except for ``.jar``)

``cache``
    Enable caching (version detected from Last-Modified or ETag)

**Examples:**

.. code-block:: yaml

    # Download and unpack tarball
    - name: boost
      url: https://example.com/boost-1.82.0.tar.gz
    
    # Download JAR (not unpacked)
    - name: my-tool
      url: https://example.com/tool.jar
      unpack: false
    
    # Cached download
    - name: data-pack
      url: https://cdn.example.com/data.tar.gz
      cache: true

File (``file``)
---------------

Use a local file.

**Basic usage:**

.. code-block:: yaml

    deps:
      - name: local-archive
        url: file:///path/to/package.tar.gz
        src: file  # Usually auto-detected

**Examples:**

.. code-block:: yaml

    # Absolute path
    - name: my-package
      url: file:///home/user/packages/pkg.tar.gz
    
    # With environment variable
    - name: my-package
      url: file://${MY_PACKAGE_DIR}/pkg.tar.gz

Directory (``dir``)
-------------------

Use a local directory. Symlinked or copied into ``packages/``.

**Basic usage:**

.. code-block:: yaml

    deps:
      - name: local-dev
        url: file:///path/to/source
        src: dir

**Attributes:**

``link``
    Use symlink (true, default) or copy (false)

**Examples:**

.. code-block:: yaml

    # Symlink to local development directory
    - name: co-developed-lib
      url: file:///home/user/projects/lib
      src: dir
      link: true
    
    # Copy directory contents
    - name: template-files
      url: file:///opt/templates
      src: dir
      link: false

**Use case:** Develop multiple related projects simultaneously.

GitHub Releases (``gh-rls``)
-----------------------------

Download assets from GitHub Releases. Automatically selects platform-specific 
binaries or falls back to source.

**Basic usage:**

.. code-block:: yaml

    deps:
      - name: my-tool
        url: https://github.com/org/tool
        src: gh-rls

**Attributes:**

``version``
    Version selector (default: ``latest``)
    
    - ``latest`` - Most recent non-prerelease
    - ``1.2.3`` - Exact version (matches tag v1.2.3 or 1.2.3)
    - ``>=1.0`` - Minimum version
    - ``<2.0`` - Maximum version (exclusive)
    - ``<=1.5`` - Maximum version (inclusive)
    - ``>1.0`` - Greater than version

``file``
    Specific asset filename (not yet implemented)

``prerelease``
    Include pre-release versions (default: false)

``cache``
    Enable caching (version includes platform info)

**Platform selection:**

IVPM automatically selects the appropriate asset for your platform:

- **Linux**: manylinux wheels matching your glibc version and architecture
- **macOS**: Assets tagged with "macos", "darwin", or "osx"
- **Windows**: Assets tagged with "windows", "win64", or "win32"

If no platform-specific binary is found, falls back to source (tarball/zipball).

**Examples:**

.. code-block:: yaml

    # Latest release
    - name: uv
      url: https://github.com/astral-sh/uv
      src: gh-rls
    
    # Specific version
    - name: ruff
      url: https://github.com/astral-sh/ruff
      src: gh-rls
      version: "0.1.0"
    
    # Version range
    - name: tool
      url: https://github.com/org/tool
      src: gh-rls
      version: ">=1.0,<2.0"
    
    # Include prereleases
    - name: beta-tool
      url: https://github.com/org/tool
      src: gh-rls
      version: latest
      prerelease: true
    
    # Cached
    - name: large-binary
      url: https://github.com/org/binary
      src: gh-rls
      cache: true

Package Types
=============

Package types determine how IVPM processes a package after fetching.

Python (``python``)
-------------------

Python packages are installed into the project's virtual environment.

**Installation modes:**

1. **Editable mode**: Source packages with ``setup.py``, ``setup.cfg``, or 
   ``pyproject.toml`` are installed with ``pip install -e``
2. **Binary mode**: PyPI packages are installed normally

**Auto-detection:** A package is considered Python if:

- ``src: pypi`` is specified, OR
- Directory contains ``setup.py``, ``setup.cfg``, or ``pyproject.toml``

**Examples:**

.. code-block:: yaml

    # Source package (editable install)
    - name: my-python-lib
      url: https://github.com/org/lib.git
      type: python  # Auto-detected if setup.py exists
    
    # PyPI package
    - name: requests
      src: pypi  # Automatically type: python

Raw (``raw``)
-------------

Raw packages are placed in ``packages/<name>/`` but not processed further.

**Use cases:**

- Data files
- Configuration
- IP cores (Verilog, VHDL)
- Documentation
- Pre-built binaries

**Examples:**

.. code-block:: yaml

    # Verilog IP
    - name: uart-rtl
      url: https://github.com/org/uart.git
      type: raw
    
    # Test data
    - name: test-vectors
      url: https://example.com/vectors.tar.gz
      type: raw

Auto-Detection
==============

IVPM automatically detects both source and package types.

Source Type Auto-Detection
---------------------------

Based on the URL:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - URL Pattern
     - Detected Source Type
   * - Ends with ``.git``
     - ``git``
   * - Starts with ``http://`` or ``https://``
     - ``http``
   * - Starts with ``file://`` (is a file)
     - ``file``
   * - Starts with ``file://`` (is a directory)
     - ``dir``
   * - No URL specified
     - ``pypi``

Package Type Auto-Detection
----------------------------

Based on content and source:

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Condition
     - Detected Package Type
   * - ``src: pypi``
     - ``python``
   * - Contains ``setup.py``, ``setup.cfg``, or ``pyproject.toml``
     - ``python``
   * - Otherwise
     - ``raw``

When to Specify Explicitly
---------------------------

Specify source or package type explicitly when:

1. **Auto-detection is wrong**
   
   .. code-block:: yaml
   
       # URL doesn't end in .git but is a Git repo
       - name: repo
         url: https://git.example.com/repo
         src: git

2. **You want different behavior**
   
   .. code-block:: yaml
   
       # Has setup.py but want to treat as raw
       - name: data-package
         url: https://github.com/org/data.git
         type: raw

3. **Using gh-rls (always specify)**
   
   .. code-block:: yaml
   
       - name: tool
         url: https://github.com/org/tool
         src: gh-rls

Common Dependency Patterns
===========================

Pattern 1: Mix of Sources
--------------------------

.. code-block:: yaml

    deps:
      # Git source (editable Python package)
      - name: my-library
        url: https://github.com/org/library.git
      
      # PyPI binary
      - name: numpy
        src: pypi
        version: ">=1.20"
      
      # HTTP archive (raw data)
      - name: test-data
        url: https://cdn.example.com/data.tar.gz
        type: raw
      
      # Local development
      - name: co-dev-lib
        url: file:///home/user/projects/lib
        src: dir

Pattern 2: Versioned Dependencies
----------------------------------

.. code-block:: yaml

    deps:
      # Git tag
      - name: stable-lib
        url: https://github.com/org/lib.git
        tag: v2.0.0
      
      # Git commit (for reproducibility)
      - name: exact-version
        url: https://github.com/org/exact.git
        commit: abc123def456
      
      # PyPI version
      - name: requests
        src: pypi
        version: "==2.28.0"
      
      # GitHub Release version
      - name: tool
        url: https://github.com/org/tool
        src: gh-rls
        version: ">=1.0,<2.0"

Pattern 3: Cached Dependencies
-------------------------------

.. code-block:: yaml

    deps:
      # Cached Git (read-only, symlinked)
      - name: large-repo
        url: https://github.com/org/large.git
        branch: stable
        cache: true
      
      # Cached HTTP (read-only, symlinked)
      - name: big-archive
        url: https://cdn.example.com/big.tar.gz
        cache: true
      
      # Cached GitHub Release (platform-specific)
      - name: binary-tool
        url: https://github.com/org/tool
        src: gh-rls
        cache: true

Complete Reference
===================

All Package Attributes
----------------------

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Attribute
     - Type
     - Description
   * - ``name``
     - string
     - Package identifier (required)
   * - ``url``
     - string
     - Source URL (not needed for pypi)
   * - ``src``
     - string
     - Source type: git, pypi, http, file, dir, gh-rls
   * - ``type``
     - string
     - Package type: python, raw
   * - ``version``
     - string
     - Version spec (PyPI, gh-rls)
   * - ``branch``
     - string
     - Git branch
   * - ``tag``
     - string
     - Git tag
   * - ``commit``
     - string
     - Git commit hash
   * - ``depth``
     - integer
     - Git clone depth
   * - ``anonymous``
     - boolean
     - Use HTTPS for Git
   * - ``cache``
     - boolean
     - Enable caching
   * - ``deps``
     - string
     - "skip" to skip sub-dependencies
   * - ``dep-set``
     - string
     - Sub-package dependency set
   * - ``link``
     - boolean
     - Symlink (true) or copy (false) for dir
   * - ``unpack``
     - boolean
     - Unpack archives
   * - ``file``
     - string
     - GitHub Release asset filename
   * - ``prerelease``
     - boolean
     - Include GitHub prereleases

See Also
========

- :doc:`core_concepts` - Understanding the package model
- :doc:`dependency_sets` - Organizing dependencies
- :doc:`caching` - Caching strategies
- :doc:`python_packages` - Python-specific features
- :doc:`github_releases` - GitHub Releases details
