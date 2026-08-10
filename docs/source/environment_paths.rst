######################
Environment & Paths
######################

Overview
========

IVPM provides two mechanisms for managing project-specific configuration:

1. **Environment Variables** - Set, modify, and export shell variables
2. **Project Paths** - Organize and export file paths by category

Both features help tools discover project resources and configuration.

Environment Variables
=====================

.. note::

   IVPM delegates environment *application* to `direnv
   <https://direnv.net>`_.  It does not set variables in your shell itself;
   instead it emits the ``env:`` directives (together with each package's
   ``export.envrc``) into ``packages/packages.envrc``.  Running ``direnv
   allow`` once authorizes that file, after which the environment loads
   automatically whenever you are in the project directory.

Declaring Environment Variables
-------------------------------

Environment variables are declared with an ``env:`` clause inside a
``with:`` block -- the same place every other handler is configured.  Each
entry names a variable and exactly one action (see below):

.. code-block:: yaml

    package:
      name: my-project

      with:
        env:
          - name: MY_VAR
            value: "hello world"
          - name: PROJECT_ROOT
            value: "${IVPM_PROJECT}"

When ``env:`` directives (or packages that publish ``export.envrc``) are
present, ``ivpm update`` writes ``packages/packages.envrc``.

.. deprecated:: 2.22

   A top-level ``env:`` (directly under ``package:``, not under ``with:``)
   is the older spelling.  It still works -- it is folded into
   ``with.env`` -- but ``ivpm update`` warns, and it will be removed in a
   future release.  To migrate, indent the list under a ``with:`` block:

   .. code-block:: yaml

       # Before
       package:
         name: my-project
         env:
           - name: MY_VAR
             value: "hello"

   .. code-block:: yaml

       # After
       package:
         name: my-project
         with:
           env:
             - name: MY_VAR
               value: "hello"

.. _env-emission-order:

Emission Order
--------------

``packages.envrc`` is written in a fixed order, and because ``direnv``
evaluates it with bash, **later lines win** for ``value:`` and ``path:``:

1. Built-ins (``IVPM_PACKAGES``, ``IVPM_PROJECT``)
2. Each package's ``export.envrc``, in dependency order
3. The package-level ``with.env`` directives
4. The selected dep-set's ``with.env`` directives

So a project's own declarations take precedence over variables set by its
packages, and a dep-set's take precedence over the package-level ones.
``path-prepend`` and ``path-append`` accumulate at every level rather than
overriding.

.. _env-dep-set-scoped:

Dep-Set-Scoped Variables
------------------------

``with:`` is valid on an individual dep-set as well as on the package, so a
dep-set can contribute its own environment.  The directives of whichever
dep-set is installed are appended to the package-level ones:

.. code-block:: yaml

    package:
      name: my-project

      with:
        env:
          - name: MYPROJ_HOME
            value: "${IVPM_PROJECT}"
          - name: PATH
            path-prepend: "${IVPM_PROJECT}/bin"

      dep-sets:
        - name: default
          deps: []

        - name: ci
          uses: default
          with:
            env:
              - name: MYPROJ_MODE
                value: "ci"
              - name: PATH
                path-prepend: "${IVPM_PROJECT}/ci/bin"
          deps: []

Installing ``ci`` yields ``MYPROJ_HOME`` (from the package level),
``MYPROJ_MODE`` (from the dep-set), and *both* ``PATH`` entries.  Had the
dep-set re-declared ``MYPROJ_HOME`` with a ``value:``, its declaration would
win -- it is emitted later (see :ref:`env-emission-order`).

.. note::

   **Dep-set environments are additive.**  A dep-set can add variables and
   override individual ones, but it cannot remove or replace what the
   package level declares -- there is no "reset" form, and an empty
   ``env: []`` clears nothing.  When two dep-sets genuinely need different
   environments, express that in the dep-set structure: put the shared
   directives in a base dep-set and have each variant ``uses:`` it, rather
   than trying to subtract at the leaf.

.. caution::

   ``with.env`` (a list of variable directives) is unrelated to
   ``with.node.env`` (a boolean that controls whether the Node handler
   patches ``packages.envrc`` with ``PATH``/``NODE_PATH``).  They are at
   different levels and never interact.

**Built-in Variables:**

The ``direnv`` handler always writes these into ``packages.envrc``:

- ``IVPM_PROJECT`` - Path to project root directory
- ``IVPM_PACKAGES`` - Path to packages directory

They are available to your own ``env:`` directives via ``${IVPM_PROJECT}``
/ ``${IVPM_PACKAGES}`` (expanded by ``direnv`` at load time).

Variable Actions
----------------

IVPM supports four actions, each mapping to a ``direnv``/bash directive:

value
~~~~~

Set a variable to a literal value:

.. code-block:: yaml

    with:
      env:
        - name: BUILD_TYPE
          value: "debug"
      
        - name: MAX_JOBS
          value: "4"

**Result:**

.. code-block:: bash

    export BUILD_TYPE="debug"
    export MAX_JOBS="4"

**With lists** (space-separated):

.. code-block:: yaml

    with:
      env:
        - name: CFLAGS
          value:
            - "-O2"
            - "-Wall"
            - "-Werror"

**Result:**

.. code-block:: bash

    export CFLAGS="-O2 -Wall -Werror"

path
~~~~

Set a variable as a path (colon-separated):

.. code-block:: yaml

    with:
      env:
        - name: LD_LIBRARY_PATH
          path:
            - "${IVPM_PACKAGES}/lib1/lib"
            - "${IVPM_PACKAGES}/lib2/lib"
            - "/usr/local/lib"

**Result:**

.. code-block:: bash

    export LD_LIBRARY_PATH="${IVPM_PACKAGES}/lib1/lib:${IVPM_PACKAGES}/lib2/lib:/usr/local/lib"

path-append
~~~~~~~~~~~

Append to an existing path variable:

.. code-block:: yaml

    with:
      env:
        - name: PATH
          path-append:
            - "${IVPM_PACKAGES}/bin"
            - "${IVPM_PACKAGES}/tools/bin"

**Result** (emitted into ``packages.envrc``):

.. code-block:: bash

    # A leading ':' separator is added only when PATH is already set
    export PATH="${PATH:+$PATH:}${IVPM_PACKAGES}/bin:${IVPM_PACKAGES}/tools/bin"

path-prepend
~~~~~~~~~~~~

Prepend to an existing path variable:

.. code-block:: yaml

    with:
      env:
        - name: PYTHONPATH
          path-prepend:
            - "${IVPM_PROJECT}/src"
            - "${IVPM_PACKAGES}/mylib/src"

**Result** (uses the ``direnv`` stdlib ``path_add``, which prepends its
arguments and de-duplicates on re-source):

.. code-block:: bash

    path_add PYTHONPATH "${IVPM_PROJECT}/src" "${IVPM_PACKAGES}/mylib/src"

Variable Expansion
------------------

``${VAR}`` references are emitted verbatim and expanded by ``direnv``
(via bash) when the environment loads -- IVPM does not expand them
itself:

.. code-block:: yaml

    with:
      env:
        # Use built-in IVPM variables
        - name: PROJECT_SRC
          value: "${IVPM_PROJECT}/src"
      
        # Reference previously-set variables
        - name: LIB_PATH
          value: "${PROJECT_SRC}/lib"
      
        # Reference system environment
        - name: USER_HOME
          value: "${HOME}"

**Order matters:** Variables are processed in order, so you can reference 
earlier variables in later ones.

Complete Environment Example
-----------------------------

.. code-block:: yaml

    package:
      name: verification-project

      with:
        env:
          # Set project paths
          - name: PROJECT_ROOT
            value: "${IVPM_PROJECT}"

          - name: RTL_ROOT
            value: "${PROJECT_ROOT}/rtl"

          - name: TB_ROOT
            value: "${PROJECT_ROOT}/testbench"

          # Configure build
          - name: BUILD_TYPE
            value: "release"

          - name: CFLAGS
            value:
              - "-O2"
              - "-Wall"

          # Add tools to PATH
          - name: PATH
            path-prepend:
              - "${IVPM_PACKAGES}/tools/bin"
              - "${PROJECT_ROOT}/scripts"

          # Set library paths
          - name: LD_LIBRARY_PATH
            path:
              - "${IVPM_PACKAGES}/lib64"
              - "${IVPM_PACKAGES}/lib"

          # Configure Python
          - name: PYTHONPATH
            path-prepend: "${IVPM_PROJECT}/src"

Loading the Environment
-----------------------

The generated ``packages.envrc`` is consumed by ``direnv``.  Authorize it
once with ``direnv allow``; thereafter the environment loads automatically
whenever you ``cd`` into the project:

.. code-block:: bash

    $ direnv allow
    $ echo $PROJECT_ROOT
    /home/user/projects/myproject

    $ echo $PATH
    /home/user/projects/myproject/packages/tools/bin:/home/user/projects/myproject/scripts:...

To run a single command in the project environment without an interactive
shell, use ``direnv exec``:

.. code-block:: bash

    $ direnv exec . pytest

.. note::

   **Windows:** ``direnv`` evaluates ``packages.envrc`` with bash, so a bash
   is required even under PowerShell.  The supported setup is ``direnv`` +
   git-bash (bundled with Git-for-Windows).  For native PowerShell, add
   ``Invoke-Expression "$(direnv hook pwsh)"`` to your ``$PROFILE``; inside
   git-bash or WSL use ``direnv`` normally.

Or for a single command:

.. code-block:: bash

    $ direnv exec . echo \$PROJECT_ROOT
    /home/user/projects/myproject

Project Paths
=============

The ``paths`` section organizes project file paths by category for tools 
to discover.

Path Organization
-----------------

Paths are organized by:

1. **Kind** - High-level category (e.g., ``rtl``, ``dv``, ``docs``)
2. **Type** - Specific type within kind (e.g., ``vlog``, ``sv``, ``vhdl``)

Basic Structure
---------------

**Software project:**

.. code-block:: yaml

    package:
      name: web-service

      paths:
        src:
          python:
            - src/api
            - src/workers
          typescript:
            - frontend/src
        test:
          python:
            - test/unit
            - test/integration
        config:
          yaml:
            - deploy/k8s
            - deploy/terraform

**Hardware project:**

.. code-block:: yaml

    package:
      name: soc-design
      
      paths:
        rtl:                    # Kind: RTL source
          vlog:                 # Type: Verilog
            - rtl/common
            - rtl/peripherals
          sv:                   # Type: SystemVerilog
            - rtl/cpu
            - rtl/bus
        
        dv:                     # Kind: Design verification
          sv:                   # Type: SystemVerilog
            - tb/agents
            - tb/sequences
          python:               # Type: Python
            - tb/models

**Paths are relative to the project root** (directory containing ``ivpm.yaml``).

Common Path Categories
----------------------

**rtl** - RTL source files:

- ``vlog`` - Verilog files
- ``sv`` - SystemVerilog files
- ``vhdl`` - VHDL files

**dv** - Design verification:

- ``sv`` - SystemVerilog testbench
- ``python`` - Python testbench
- ``c`` - C/C++ models

**docs** - Documentation:

- ``rst`` - ReStructuredText
- ``md`` - Markdown
- ``pdf`` - PDF files

**data** - Data files:

- ``testdata`` - Test vectors
- ``config`` - Configuration files

**src** - Source code:

- ``python`` - Python source files
- ``typescript`` - TypeScript source files
- ``go`` - Go source files

**test** - Test suites:

- ``python`` - Python tests
- ``typescript`` - TypeScript tests
- ``shell`` - Shell-based integration tests

**config** - Configuration and deployment:

- ``yaml`` - YAML configuration files
- ``json`` - JSON configuration files
- ``toml`` - TOML configuration files

Complete Paths Example
-----------------------

.. code-block:: yaml

    package:
      name: uart-ip
      
      paths:
        # RTL source files
        rtl:
          sv:
            - rtl/uart_core
            - rtl/uart_tx
            - rtl/uart_rx
          vlog:
            - rtl/uart_apb_if
        
        # Verification
        dv:
          sv:
            - tb/uart_agent
            - tb/uart_monitor
            - tb/sequences
          python:
            - tb/models
            - tb/tests
        
        # Documentation
        docs:
          rst:
            - docs/source
          md:
            - docs/guides
        
        # IP-XACT or other metadata
        metadata:
          ipxact:
            - ip-xact/uart.xml
        
        # Scripts
        scripts:
          python:
            - scripts/build
          shell:
            - scripts/sim

Using Path Information
----------------------

Tools can query path information via the ``ivpm pkg-info`` command:

.. code-block:: bash

    # Get all RTL SystemVerilog paths
    $ ivpm pkg-info paths -k rtl --type sv my-package
    
    # Get all verification paths
    $ ivpm pkg-info paths -k dv my-package

This enables tools like FuseSoC, simulators, and build systems to 
discover source files automatically.

Practical Examples
==================

Example 1: Simulation Environment
----------------------------------

.. code-block:: yaml

    package:
      name: cpu-verification

      with:
        env:
          # Simulation variables
          - name: SIM_ROOT
            value: "${IVPM_PROJECT}"

          - name: WORK_DIR
            value: "${SIM_ROOT}/work"

          - name: LOG_DIR
            value: "${SIM_ROOT}/logs"

          # Simulator paths
          - name: PATH
            path-prepend:
              - "${IVPM_PACKAGES}/verilator/bin"
              - "${IVPM_PACKAGES}/gtkwave/bin"

          # Library paths for compiled libraries
          - name: LD_LIBRARY_PATH
            path:
              - "${IVPM_PACKAGES}/lib"
              - "${SIM_ROOT}/build/lib"

      paths:
        rtl:
          sv:
            - rtl/core
            - rtl/cache
            - rtl/mmu
        dv:
          sv:
            - tb/top
            - tb/agents
          python:
            - tb/tests

**Usage:**

.. code-block:: bash

    $ ivpm update
    $ direnv allow
    $ cd $WORK_DIR
    $ make sim

Example 2: Multi-Language Project
----------------------------------

.. code-block:: yaml

    package:
      name: mixed-project

      with:
        env:
          # Project structure
          - name: HDL_ROOT
            value: "${IVPM_PROJECT}/hdl"

          - name: SW_ROOT
            value: "${IVPM_PROJECT}/software"

          # Compilation flags
          - name: VLOG_FLAGS
            value:
              - "+incdir+${HDL_ROOT}/include"
              - "-timescale=1ns/1ps"

          - name: CFLAGS
            value:
              - "-I${SW_ROOT}/include"
              - "-Wall"

          # Tool configuration
          - name: PYTHONPATH
            path-prepend:
              - "${SW_ROOT}/python"
              - "${IVPM_PROJECT}/scripts"

      paths:
        rtl:
          vlog:
            - hdl/rtl
          sv:
            - hdl/interfaces
        
        sw:
          c:
            - software/drivers
            - software/firmware
          python:
            - software/python

Example 3: Team Development Setup
----------------------------------

.. code-block:: yaml

    package:
      name: team-project

      with:
        env:
          # Project info
          - name: PROJECT_NAME
            value: "TeamProject"

          - name: PROJECT_VERSION
            value: "1.0.0"

          # Shared tools (from packages/)
          - name: TOOL_ROOT
            value: "${IVPM_PACKAGES}/tools"

          - name: PATH
            path-prepend:
              - "${TOOL_ROOT}/bin"
              - "${IVPM_PROJECT}/scripts"

          # License servers
          - name: LM_LICENSE_FILE
            path:
              - "27000@license-server-1"
              - "27001@license-server-2"

          # Output directories
          - name: BUILD_DIR
            value: "${IVPM_PROJECT}/build"

          - name: REPORT_DIR
            value: "${IVPM_PROJECT}/reports"

Best Practices
==============

1. **Use built-in variables** (``IVPM_PROJECT``, ``IVPM_PACKAGES``) for portability
2. **Define variables in order** - reference order matters for expansion
3. **Use path-prepend for tools** - Ensure project tools override system tools
4. **Document custom variables** - Help team members understand the setup
5. **Keep paths relative** - Use ``${IVPM_PROJECT}`` for portability
6. **Group related variables** - Organize by purpose (build, test, docs)
7. **Test after changes** - Always verify with ``direnv exec . env``

See Also
========

- :doc:`getting_started` - Basic project setup
- :doc:`core_concepts` - Understanding IVPM's model
- :doc:`troubleshooting` - Solutions to common problems
- :doc:`handlers` - How handlers process packages
