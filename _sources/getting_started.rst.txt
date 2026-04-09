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

``--py-system-site-packages``
    Opt in to inheriting system site-packages inside the virtual environment.
    By default the environment is **isolated**; use this flag only when you
    need access to system-installed packages (e.g. hardware-specific bindings).

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

**JSON Schema Support**

For IDE autocompletion and validation, add a ``$schema`` reference at the top of your ``ivpm.yaml``:

.. code-block:: yaml

    $schema: https://fvutils.github.io/ivpm/ivpm.schema.json

    package:
      name: my-project
      version: "0.1.0"

Step 2: Add Dependency Sets
----------------------------

Edit ``ivpm.yaml`` to add your dependencies:

.. code-block:: yaml

    $schema: https://fvutils.github.io/ivpm/ivpm.schema.json

    package:
      name: my-project
      version: "0.1.0"
      default-dep-set: default-dev

      dep-sets:
        - name: default
          deps:
            - name: requests
              src: pypi

        - name: default-dev
          uses: default
          deps:
            - name: pytest
              src: pypi

For details on dependency sets, see :doc:`dependency_sets`.

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

Step 4: Work with Your Project
-------------------------------

.. code-block:: bash

    # Run a command in the virtual environment
    $ ivpm activate -c "pytest"

    # Start an interactive shell
    $ ivpm activate
    (venv) $ python
    (venv) $ exit

Using the Python Virtual Environment
=====================================

IVPM creates a project-local Python virtual environment in ``packages/python/``.

**Run a single command:**

.. code-block:: bash

    $ ivpm activate -c "python script.py"
    $ ivpm activate -c "pytest"

**Start an interactive shell:**

.. code-block:: bash

    $ ivpm activate
    (venv) $ python
    (venv) $ pytest
    (venv) $ exit

For details on Python package management (editable installs, uv vs pip,
native extensions), see :doc:`python_packages`.

Next Steps
==========

Now that you have the basics:

- :doc:`core_concepts` -- Understand the update pipeline and mental model
- :doc:`handlers` -- How handlers process packages (Python, Direnv, Skills)
- :doc:`dependency_sets` -- Organize dependencies by profile
- :doc:`package_types` -- All dependency attributes and source types
- :doc:`workflows` -- Common development workflows
- :doc:`troubleshooting` -- Solutions to common problems
