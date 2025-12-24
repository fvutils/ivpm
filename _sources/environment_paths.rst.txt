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

Environment Sets
----------------

Define environment variables in ``ivpm.yaml`` using **env-sets**:

.. code-block:: yaml

    package:
      name: my-project
      
      env-sets:
        - name: project
          env:
            - name: MY_VAR
              value: "hello world"
            - name: PROJECT_ROOT
              value: "${IVPM_PROJECT}"

**Built-in Variables:**

IVPM provides these variables automatically:

- ``IVPM_PROJECT`` - Path to project root directory
- ``IVPM_PACKAGES`` - Path to packages directory
- ``IVPM_HOME`` - Path to packages directory (deprecated alias)

Variable Actions
----------------

IVPM supports four actions for setting environment variables:

value
~~~~~

Set a variable to a literal value:

.. code-block:: yaml

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

    env:
      - name: PATH
        path-append:
          - "${IVPM_PACKAGES}/bin"
          - "${IVPM_PACKAGES}/tools/bin"

**Result:**

.. code-block:: bash

    # If PATH already exists
    export PATH="${PATH}:${IVPM_PACKAGES}/bin:${IVPM_PACKAGES}/tools/bin"
    
    # If PATH doesn't exist
    export PATH="${IVPM_PACKAGES}/bin:${IVPM_PACKAGES}/tools/bin"

path-prepend
~~~~~~~~~~~~

Prepend to an existing path variable:

.. code-block:: yaml

    env:
      - name: PYTHONPATH
        path-prepend:
          - "${IVPM_PROJECT}/src"
          - "${IVPM_PACKAGES}/mylib/src"

**Result:**

.. code-block:: bash

    # If PYTHONPATH already exists
    export PYTHONPATH="${IVPM_PROJECT}/src:${IVPM_PACKAGES}/mylib/src:${PYTHONPATH}"
    
    # If PYTHONPATH doesn't exist
    export PYTHONPATH="${IVPM_PROJECT}/src:${IVPM_PACKAGES}/mylib/src"

Variable Expansion
------------------

IVPM expands variables referenced with ``${VAR}`` syntax:

.. code-block:: yaml

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
      
      env-sets:
        - name: project
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

Using Environment Sets
-----------------------

Environment variables are automatically applied when using ``ivpm activate``:

.. code-block:: bash

    $ ivpm activate
    (venv) $ echo $PROJECT_ROOT
    /home/user/projects/myproject
    
    (venv) $ echo $PATH
    /home/user/projects/myproject/packages/tools/bin:/home/user/projects/myproject/scripts:...

Or for a single command:

.. code-block:: bash

    $ ivpm activate -c "echo \$PROJECT_ROOT"
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
      
      env-sets:
        - name: project
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
    $ ivpm activate
    (venv) $ cd $WORK_DIR
    (venv) $ make sim

Example 2: Multi-Language Project
----------------------------------

.. code-block:: yaml

    package:
      name: mixed-project
      
      env-sets:
        - name: project
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
      
      env-sets:
        - name: project
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

Troubleshooting
===============

Environment Variable Not Set
-----------------------------

**Problem:** Variable not available after ``ivpm activate``

**Check:**

1. Is ``env-sets`` defined in ``ivpm.yaml``?
2. Did you run ``ivpm update`` after changing ``ivpm.yaml``?
3. Are you using ``ivpm activate``?

**Solution:**

.. code-block:: bash

    $ cat ivpm.yaml  # Verify env-sets
    $ ivpm update
    $ ivpm activate -c "env | grep MY_VAR"

Variable Expansion Not Working
-------------------------------

**Problem:** ``${VAR}`` appears literally instead of being expanded

**Common causes:**

1. Variable referenced before it's defined
2. Typo in variable name
3. Variable not in environment when IVPM runs

**Solution:**

.. code-block:: yaml

    env:
      # Define base variables first
      - name: BASE_DIR
        value: "/opt/project"
      
      # Then reference them
      - name: LIB_DIR
        value: "${BASE_DIR}/lib"

Path-prepend Not Working
-------------------------

**Problem:** New paths not appearing at start of PATH

**Check order:**

.. code-block:: bash

    $ ivpm activate -c "echo \$PATH" | tr ':' '\n'

If your paths aren't first, check if something else is modifying PATH 
after IVPM (e.g., in ``.bashrc``).

Best Practices
==============

1. **Use built-in variables** (``IVPM_PROJECT``, ``IVPM_PACKAGES``) for portability
2. **Define variables in order** - reference order matters for expansion
3. **Use path-prepend for tools** - Ensure project tools override system tools
4. **Document custom variables** - Help team members understand the setup
5. **Keep paths relative** - Use ``${IVPM_PROJECT}`` for portability
6. **Group related variables** - Organize by purpose (build, test, docs)
7. **Test after changes** - Always verify with ``ivpm activate -c "env"``

See Also
========

- :doc:`getting_started` - Basic project setup
- :doc:`core_concepts` - Understanding IVPM's model
