###########################
Python Package Management
###########################

Overview
========

IVPM provides first-class support for Python packages, managing both source 
packages (editable installs) and binary packages (from PyPI) within a 
project-local virtual environment.

Virtual Environment Creation
=============================

IVPM automatically creates a project-local Python virtual environment in 
``packages/python/`` when you run ``ivpm update``.

Automatic Creation
------------------

.. code-block:: bash

    $ ivpm update

Creates::

    packages/
    └── python/
        ├── bin/
        │   ├── python
        │   ├── pip
        │   └── activate
        ├── lib/
        │   └── python3.x/
        │       └── site-packages/
        └── ...

The virtual environment is created using Python's built-in ``venv`` module.
By default the environment is **isolated**: packages installed in the base
Python or system Python are **not** visible inside it.  This ensures
reproducible, self-contained workspaces.

To opt in to inheriting system site-packages (e.g. for hardware-specific
Python extensions that cannot be installed via pip), pass
``--py-system-site-packages`` to ``ivpm update`` or ``ivpm clone``:

.. code-block:: bash

    $ ivpm update --py-system-site-packages
    $ ivpm clone https://github.com/org/project --py-system-site-packages

Package Manager Selection
--------------------------

IVPM supports two Python package managers:

1. **uv** - Fast, modern Python package installer (recommended)
2. **pip** - Traditional Python package installer

**Auto-detection** (default):

IVPM checks if ``uv`` is available. If found, uses ``uv``; otherwise, uses ``pip``.

**Explicit selection:**

.. code-block:: bash

    # Use uv
    $ ivpm update --py-uv
    $ ivpm clone https://github.com/org/project --py-uv
    
    # Use pip
    $ ivpm update --py-pip
    $ ivpm clone https://github.com/org/project --py-pip

Managing Installation
---------------------

**Skip Python package installation:**

.. code-block:: bash

    $ ivpm update --skip-py-install

Use this when packages are already installed and you only want to fetch 
non-Python dependencies.

**Force reinstallation:**

.. code-block:: bash

    $ ivpm update --force-py-install

Use this to re-install all Python packages, useful after:

- Updating dependencies in ``ivpm.yaml``
- Corrupted virtual environment
- Switching between dev/release dependency sets

Editable vs Binary Packages
============================

IVPM installs Python packages in two modes:

Editable Packages
-----------------

**Source:** Git repositories, local directories

**Installation:** ``pip install -e`` (editable mode)

**Characteristics:**

- Source code directly accessible
- Changes to source immediately available
- Can modify and commit
- Full development setup

**Example:**

.. code-block:: yaml

    deps:
      - name: my-library
        url: https://github.com/org/my-library.git

**Result:**

.. code-block:: text

    packages/
    ├── my-library/          # Source code
    │   ├── setup.py
    │   ├── src/
    │   └── ...
    └── python/
        └── lib/python3.x/site-packages/
            └── my-library.egg-link  # Points to ../../../my-library

**Import behavior:**

.. code-block:: python

    import my_library  # Uses code from packages/my-library/

Binary Packages
---------------

**Source:** PyPI

**Installation:** ``pip install`` (normal mode)

**Characteristics:**

- Pre-built wheels or source distribution
- Installed into site-packages
- Cannot modify (read-only)
- Faster installation

**Example:**

.. code-block:: yaml

    deps:
      - name: requests
        src: pypi
        version: ">=2.28.0"

**Result:**

.. code-block:: text

    packages/
    └── python/
        └── lib/python3.x/site-packages/
            ├── requests/
            ├── requests-2.31.0.dist-info/
            └── ...

**Import behavior:**

.. code-block:: python

    import requests  # Uses installed package

PyPI Package Configuration
==========================

Basic PyPI Packages
-------------------

.. code-block:: yaml

    deps:
      # Latest version
      - name: requests
        src: pypi
      
      # Specific version
      - name: numpy
        src: pypi
        version: "==1.24.0"
      
      # Version range
      - name: pandas
        src: pypi
        version: ">=1.5.0,<2.0"
      
      # Minimum version
      - name: pytest
        src: pypi
        version: ">=7.0"

Version Specifiers
------------------

IVPM supports PEP 440 version specifiers:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Specifier
     - Meaning
   * - ``==1.2.3``
     - Exactly version 1.2.3
   * - ``>=1.2.3``
     - Version 1.2.3 or higher
   * - ``<2.0``
     - Any version below 2.0
   * - ``>=1.2,<2.0``
     - 1.2 or higher, but below 2.0
   * - ``~=1.2.3``
     - Compatible release (>=1.2.3, <1.3.0)
   * - ``*`` or unspecified
     - Latest version

Pre-release Packages
--------------------

To include pre-release versions (alpha, beta, rc):

.. code-block:: bash

    $ ivpm update --py-prerls-packages

Or in ``ivpm.yaml``, specify the pre-release version explicitly:

.. code-block:: yaml

    deps:
      - name: package
        src: pypi
        version: ">=1.0.0a1"  # Includes alpha releases

Source Packages
===============

Git-based Python Packages
--------------------------

Any Git repository with ``setup.py``, ``setup.cfg``, or ``pyproject.toml`` 
is automatically detected as a Python package.

**Example:**

.. code-block:: yaml

    deps:
      - name: my-package
        url: https://github.com/org/my-package.git
        branch: develop

**Auto-detection:** IVPM scans for Python build files after fetching.

**Manual specification:**

.. code-block:: yaml

    deps:
      - name: my-package
        url: https://github.com/org/my-package.git
        type: python  # Explicitly mark as Python

**Installation:** Installed as editable: ``pip install -e packages/my-package``

Local Development Packages
---------------------------

Use ``src: dir`` for co-developed packages:

.. code-block:: yaml

    deps:
      - name: co-dev-lib
        url: file:///home/user/projects/library
        src: dir
        link: true  # Symlink instead of copy

**Result:**

- ``packages/co-dev-lib`` → symlink to ``/home/user/projects/library``
- Installed as editable
- Changes in original location immediately available

Setup Dependencies
==================

Some packages must be installed before others. Use ``setup-deps`` to specify 
installation order:

.. code-block:: yaml

    package:
      name: my-project
      
      setup-deps:
        - wheel
        - setuptools
        - cython
      
      dep-sets:
        - name: default-dev
          deps:
            - name: wheel
              src: pypi
            - name: setuptools
              src: pypi
            - name: cython
              src: pypi
            - name: my-cython-package
              url: https://github.com/org/cython-pkg.git

**Behavior:**

1. Install ``wheel``, ``setuptools``, ``cython`` first (in order)
2. Then install all other packages (topologically sorted)

This ensures build dependencies are available before packages that need them.

Building Native Extensions
===========================

The ``build`` Command
---------------------

For Python packages with native extensions (C, C++, Cython):

.. code-block:: bash

    $ ivpm build

This runs the build process for all Python packages in ``packages/``.

**Options:**

.. code-block:: bash

    # Build with debug symbols
    $ ivpm build --debug
    
    # Build specific dependency set
    $ ivpm build -d default-dev

Debug Builds
------------

To build native extensions with debug symbols:

.. code-block:: bash

    $ ivpm build --debug

Or set environment variable:

.. code-block:: bash

    $ DEBUG=1 ivpm build

This passes ``-g`` flag to the C/C++ compiler and disables optimization.

Example: Building a Package with Native Extensions
---------------------------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    setup-deps:
      - cython
      - setuptools
    
    dep-sets:
      - name: default-dev
        deps:
          - name: cython
            src: pypi
          - name: setuptools
            src: pypi
          - name: my-fast-lib
            url: https://github.com/org/fast-lib.git

**Build process:**

.. code-block:: bash

    $ ivpm update    # Fetch sources, install packages
    $ ivpm build     # Build native extensions

Using the Virtual Environment
==============================

Activate Command
----------------

Run commands within the virtual environment:

.. code-block:: bash

    # One-off command
    $ ivpm activate -c "python script.py"
    $ ivpm activate -c "pytest"
    
    # Interactive shell
    $ ivpm activate
    (venv) $ python
    (venv) $ pip list
    (venv) $ exit

**Behind the scenes:** ``ivpm activate`` sources ``packages/python/bin/activate``

Direct Access
-------------

You can also use the virtual environment directly:

.. code-block:: bash

    # Use Python directly
    $ packages/python/bin/python script.py
    
    # Install additional packages
    $ packages/python/bin/pip install extra-package
    
    # Run installed scripts
    $ packages/python/bin/pytest

Environment Variables
---------------------

When activated, these variables are set:

- ``VIRTUAL_ENV`` → ``packages/python``
- ``PATH`` → ``packages/python/bin:$PATH``
- ``PYTHONPATH`` → (can be customized via ``env-sets``)

Complete Examples
=================

Example 1: Pure Python Project
-------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: data-analyzer
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            - name: pandas
              src: pypi
              version: ">=2.0"
            - name: numpy
              src: pypi
              version: ">=1.24"
        
        - name: default-dev
          deps:
            - name: pandas
              src: pypi
              version: ">=2.0"
            - name: numpy
              src: pypi
              version: ">=1.24"
            - name: pytest
              src: pypi
            - name: black
              src: pypi
            - name: mypy
              src: pypi

**Usage:**

.. code-block:: bash

    $ ivpm update
    $ ivpm activate -c "pytest"
    $ ivpm activate -c "black src/"
    $ ivpm activate -c "mypy src/"

Example 2: Mixed Source and Binary
-----------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: ml-pipeline
      default-dep-set: default-dev
      
      dep-sets:
        - name: default-dev
          deps:
            # Binary packages from PyPI
            - name: torch
              src: pypi
              version: ">=2.0"
            - name: transformers
              src: pypi
            
            # Source package (co-developed)
            - name: custom-models
              url: https://github.com/org/models.git
            
            # Local development
            - name: data-utils
              url: file:///home/user/projects/utils
              src: dir

**Usage:**

.. code-block:: bash

    $ ivpm update
    $ ivpm activate -c "python train.py"
    
    # Edit custom-models or data-utils, changes immediate
    $ ivpm activate -c "python train.py"  # Uses modified code

Example 3: Native Extensions with Build
----------------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: signal-processor
      
      setup-deps:
        - cython
        - numpy
      
      dep-sets:
        - name: default-dev
          deps:
            - name: cython
              src: pypi
            - name: numpy
              src: pypi
            - name: scipy
              src: pypi
            - name: fast-dsp
              url: https://github.com/org/fast-dsp.git

**fast-dsp has C extensions that need compilation**

**Usage:**

.. code-block:: bash

    $ ivpm update       # Fetch and install
    $ ivpm build        # Build native extensions
    $ ivpm activate -c "python process.py"
    
    # Debug build
    $ ivpm build --debug
    $ ivpm activate -c "gdb python"

Troubleshooting
===============

Package Not Importable
-----------------------

**Problem:** ``ModuleNotFoundError`` when importing

**Check:**

1. Is the package in the active dependency set?

   .. code-block:: bash

       $ cat ivpm.yaml  # Check dep-sets

2. Was ``ivpm update`` run?

   .. code-block:: bash

       $ ivpm update

3. Is the virtual environment activated?

   .. code-block:: bash

       $ ivpm activate -c "python -c 'import package'"

4. List installed packages:

   .. code-block:: bash

       $ ivpm activate -c "pip list"

Editable Install Not Working
-----------------------------

**Problem:** Changes to source package not reflected

**Check:**

1. Is it installed in editable mode?

   .. code-block:: bash

       $ ivpm activate -c "pip list"
       # Look for -e /path/to/package

2. Check the egg-link:

   .. code-block:: bash

       $ cat packages/python/lib/python3.*/site-packages/package.egg-link

**Solution:** Reinstall:

.. code-block:: bash

    $ ivpm update --force-py-install

Build Failures
--------------

**Problem:** Native extension build fails

**Common causes:**

1. Missing build dependencies (compiler, headers)
2. Missing setup-deps
3. Wrong order of installation

**Solutions:**

.. code-block:: bash

    # Install system dependencies (Ubuntu/Debian)
    $ sudo apt-get install build-essential python3-dev
    
    # Add setup-deps to ivpm.yaml
    $ vim ivpm.yaml  # Add setuptools, wheel, cython
    
    # Rebuild
    $ ivpm update --force-py-install
    $ ivpm build

Version Conflicts
-----------------

**Problem:** Package version conflict errors

**Check versions:**

.. code-block:: bash

    $ ivpm activate -c "pip list"

**Solution:** Adjust version specs in ``ivpm.yaml``:

.. code-block:: yaml

    deps:
      # Too restrictive
      - name: package-a
        src: pypi
        version: "==1.0.0"
      
      # Better
      - name: package-a
        src: pypi
        version: ">=1.0.0,<2.0"

Best Practices
==============

1. **Use version ranges** instead of exact versions for flexibility
2. **Specify setup-deps** for packages with build dependencies
3. **Use editable installs** for packages under active development
4. **Keep dev tools separate** in ``default-dev`` dependency set
5. **Pin versions** for reproducible builds (release deps)
6. **Use --force-py-install** after changing dependency sets
7. **Leverage uv** for faster package installation
8. **Test with both pip and uv** if distributing to diverse users

See Also
========

- :doc:`getting_started` - Basic Python package setup
- :doc:`dependency_sets` - Organizing Python and non-Python deps
- :doc:`package_types` - PyPI package configuration
