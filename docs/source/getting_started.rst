#########################
Getting Started with IVPM
#########################

Installing IVPM
===============

IVPM must be installed before it can be used to work with a project. Typically,
the easiest approach is to install IVPM as a user-installed package:

.. code-block:: bash

    $ python3 -m pip install --user ivpm

Once this is done, you can invoke IVPM either via the entry-point script (ivpm)
or as a Python module:

.. code-block:: bash

    $ ivpm --help
    $ python3 -m ivpm --help

Quick Start: Clone an Existing Project
=======================================

The fastest way to start with IVPM is to clone an existing IVPM-enabled project:

.. code-block:: bash

    $ ivpm clone https://github.com/org/project.git
    $ cd project

This single command:

1. Clones the Git repository
2. Reads the project's ``ivpm.yaml``
3. Fetches all dependencies
4. Creates a Python virtual environment
5. Installs Python packages

After cloning, you're ready to work:

.. code-block:: bash

    $ ivpm activate -c "python --version"
    $ ivpm activate -c "pytest"

Clone Command Options
---------------------

.. code-block:: bash

    $ ivpm clone [options] <src-url-or-path> [workspace_dir]

**Common options:**

``-a, --anonymous``
    Clone anonymously over HTTPS instead of using SSH. By default,
    HTTPS URLs are converted to SSH form (``git@host:path``).

``-b, --branch <name>``
    Checkout the specified branch. If ``origin/<name>`` exists, it
    will be checked out tracking the remote; otherwise, a new local branch is created.

``-d, --dep-set <name>``
    Specify the dependency set for ``ivpm update`` (e.g., ``default-dev``).

``--py-uv`` or ``--py-pip``
    Choose whether ``ivpm update`` should use "uv" or "pip"
    to manage the project-local Python virtual environment.

**Examples:**

.. code-block:: bash

    # Clone with default workspace directory name
    $ ivpm clone https://github.com/fvutils/ivpm
    
    # Clone into specific directory with new branch
    $ ivpm clone https://github.com/org/project my-workspace -b feature/new
    
    # Clone anonymously and select dependency set
    $ ivpm clone -a https://github.com/org/project -d default-dev
    
    # Clone and use uv for Python package management
    $ ivpm clone https://github.com/org/project --py-uv

Creating a New IVPM Project
============================

To create a new IVPM-enabled project from scratch:

Step 1: Initialize the Project
-------------------------------

.. code-block:: bash

    $ mkdir my-project
    $ cd my-project
    $ ivpm init my-project -v 0.1.0

This creates a basic ``ivpm.yaml`` file:

.. code-block:: yaml

    package:
      name: my-project
      version: "0.1.0"

Step 2: Add Dependency Sets
----------------------------

Edit ``ivpm.yaml`` to add your dependencies:

.. code-block:: yaml

    package:
      name: my-project
      version: "0.1.0"
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            # Runtime dependencies
            - name: requests
              src: pypi
        
        - name: default-dev
          deps:
            # Runtime dependencies
            - name: requests
              src: pypi
            # Development dependencies
            - name: pytest
              src: pypi

Step 3: Run Initial Update
---------------------------

Fetch dependencies and create the Python virtual environment:

.. code-block:: bash

    $ ivpm update

This creates:

.. code-block:: text

    my-project/
    ├── ivpm.yaml
    └── packages/
        └── python/           # Virtual environment
            ├── bin/
            ├── lib/
            └── ...

Step 4: Create Your Project Structure
--------------------------------------

.. code-block:: bash

    $ mkdir -p src/my_project test
    $ touch src/my_project/__init__.py
    $ touch test/test_basic.py

Now you have a complete project structure:

.. code-block:: text

    my-project/
    ├── ivpm.yaml
    ├── packages/
    │   └── python/
    ├── src/
    │   └── my_project/
    │       └── __init__.py
    └── test/
        └── test_basic.py

Working with Dependencies
=========================

Adding Dependencies
-------------------

To add a new dependency, edit ``ivpm.yaml``:

**Add a PyPI package:**

.. code-block:: yaml

    deps:
      - name: numpy
        src: pypi
        version: ">=1.20"

**Add a Git repository:**

.. code-block:: yaml

    deps:
      - name: my-library
        url: https://github.com/org/my-library.git
        branch: main

**Add a local development package:**

.. code-block:: yaml

    deps:
      - name: co-developed
        url: file:///home/user/projects/library
        src: dir

After editing, run:

.. code-block:: bash

    $ ivpm update

Updating Dependencies
---------------------

To fetch the latest changes from Git repositories:

.. code-block:: bash

    $ ivpm status    # Check status of all Git packages
    $ ivpm sync      # Update Git packages from upstream

To update Python packages:

.. code-block:: bash

    $ ivpm update --force-py-install

Using the Python Virtual Environment
=====================================

IVPM creates a project-local Python virtual environment in ``packages/python/``.

Activate for a Single Command
------------------------------

.. code-block:: bash

    $ ivpm activate -c "python script.py"
    $ ivpm activate -c "pytest"
    $ ivpm activate -c "python -m my_module"

Start an Interactive Shell
---------------------------

.. code-block:: bash

    $ ivpm activate
    (venv) $ python
    (venv) $ pytest
    (venv) $ exit

The shell prompt shows ``(venv)`` when the environment is active.

Practical Examples
==================

Example 1: Simple Python Project
---------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: data-processor
      version: "1.0.0"
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            - name: pandas
              src: pypi
            - name: numpy
              src: pypi
        
        - name: default-dev
          deps:
            - name: pandas
              src: pypi
            - name: numpy
              src: pypi
            - name: pytest
              src: pypi
            - name: black
              src: pypi

**Usage:**

.. code-block:: bash

    $ ivpm update -d default-dev
    $ ivpm activate -c "pytest"
    $ ivpm activate -c "black src/"

Example 2: Mixed Dependencies
------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: verification-env
      default-dep-set: default-dev
      
      dep-sets:
        - name: default-dev
          deps:
            # Python test framework
            - name: cocotb
              src: pypi
            
            # Co-developed Python library
            - name: bus-models
              url: https://github.com/org/bus-models.git
            
            # Verilog RTL (raw package)
            - name: uart-rtl
              url: https://github.com/org/uart.git
              type: raw
            
            # Test vectors
            - name: test-data
              url: https://cdn.example.com/vectors.tar.gz
              type: raw

**Usage:**

.. code-block:: bash

    $ ivpm update
    $ ivpm activate -c "make sim"

Example 3: Using Dependency Sets
---------------------------------

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: soc-design
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            - name: cpu-core
              url: https://github.com/org/cpu.git
              tag: v1.0
        
        - name: default-dev
          deps:
            - name: cpu-core
              url: https://github.com/org/cpu.git  # Development branch
            - name: test-framework
              url: https://github.com/org/test.git
        
        - name: fpga
          deps:
            - name: cpu-core
              url: https://github.com/org/cpu.git
              tag: v1.0
            - name: xilinx-ips
              url: https://github.com/org/xilinx.git

**Usage:**

.. code-block:: bash

    # Development work
    $ ivpm update -d default-dev
    
    # Release build
    $ ivpm update -d default
    
    # FPGA build
    $ ivpm update -d fpga

Common Workflows
================

Daily Development
-----------------

.. code-block:: bash

    # 1. Start working
    $ cd my-project
    $ ivpm activate
    
    # 2. Work on code
    (venv) $ python src/my_script.py
    (venv) $ pytest
    
    # 3. Check dependency status
    (venv) $ ivpm status
    
    # 4. Done for the day
    (venv) $ exit

Adding a New Dependency
-----------------------

.. code-block:: bash

    # 1. Edit ivpm.yaml to add dependency
    $ vim ivpm.yaml
    
    # 2. Update to fetch new dependency
    $ ivpm update
    
    # 3. Verify it's available
    $ ivpm activate -c "python -c 'import new_package'"

Syncing with Upstream
---------------------

.. code-block:: bash

    # 1. Check status of all Git dependencies
    $ ivpm status
    
    # 2. Update from upstream
    $ ivpm sync
    
    # 3. Re-install Python packages if needed
    $ ivpm update --force-py-install

Troubleshooting
===============

Update Fails with SSH Error
----------------------------

**Problem:** Cannot clone Git repositories with SSH

**Solution:** Use anonymous (HTTPS) cloning:

.. code-block:: bash

    $ ivpm update -a

Or set ``anonymous: true`` in ``ivpm.yaml``:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/lib.git
        anonymous: true

Python Package Not Found
-------------------------

**Problem:** Cannot import a package that should be installed

**Solution:** Verify the package is in the active dependency set:

.. code-block:: bash

    $ ivpm update -d default-dev  # Ensure correct dep-set
    $ ivpm activate -c "pip list"  # Check installed packages

Dependencies Not Loading
------------------------

**Problem:** Expected dependencies not appearing in ``packages/``

**Solution:** Check your dependency set:

.. code-block:: bash

    # See what's configured
    $ cat ivpm.yaml
    
    # Try explicit dependency set
    $ ivpm update -d default-dev

Slow Updates
------------

**Problem:** ``ivpm update`` is very slow

**Solutions:**

1. **Use caching** for Git dependencies:

   .. code-block:: yaml

       deps:
         - name: large-repo
           url: https://github.com/org/large.git
           cache: true

2. **Use shallow clones**:

   .. code-block:: yaml

       deps:
         - name: repo
           url: https://github.com/org/repo.git
           depth: 1

3. **Skip Python reinstall** if already done:

   .. code-block:: bash

       $ ivpm update --skip-py-install

Next Steps
==========

Now that you have the basics:

- Read :doc:`core_concepts` to understand IVPM's model
- Learn about :doc:`dependency_sets` for complex projects
- Explore :doc:`package_types` for all dependency options
- Set up :doc:`caching` for faster updates
- See :doc:`python_packages` for Python-specific features

