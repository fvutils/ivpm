################
Dependency Sets
################

What are Dependency Sets?
==========================

**Dependency sets** are named collections of package dependencies. They allow you to 
define different dependency profiles for different scenarios, such as development 
vs. release, or different build targets.

Why Use Dependency Sets?
=========================

Separate Concerns
-----------------

Development and release often need different dependencies:

**Development needs:**

- Testing frameworks (pytest, unittest)
- Debugging tools
- Documentation generators
- Linters and formatters
- Build tools

**Release needs:**

- Only runtime dependencies
- Minimal footprint
- No development tools

**Example:** A verification IP might depend on UVM libraries at runtime, but also 
need waveform viewers and coverage tools during development.

Multiple Build Targets
-----------------------

Different configurations may need different dependencies:

- FPGA vs ASIC builds
- Simulation vs synthesis
- Different test suites
- Platform-specific dependencies

Faster Updates
--------------

By selecting only the dependencies you need, ``ivpm update`` runs faster and uses 
less disk space.

Defining Dependency Sets
=========================

Basic Structure
---------------

Define dependency sets in your ``ivpm.yaml``:

.. code-block:: yaml

    package:
      name: my-project
      default-dep-set: default-dev  # Default when not specified
      
      dep-sets:
        - name: default           # Release dependencies
          deps:
            - name: runtime-lib
              url: https://github.com/org/runtime-lib.git
        
        - name: default-dev       # Development dependencies
          deps:
            - name: runtime-lib
              url: https://github.com/org/runtime-lib.git
            - name: pytest
              src: pypi
            - name: test-framework
              url: https://github.com/org/test-framework.git

Standard Names
--------------

While you can use any names, IVPM recognizes these standard names:

``default``
    Release/runtime dependencies. Use this for packages you're distributing.

``default-dev``
    Development dependencies. Includes everything from ``default`` plus 
    development tools.

Using Dependency Sets
=====================

Selecting a Set
---------------

**On the command line:**

.. code-block:: bash

    # Use development dependencies (default if default-dep-set is set)
    ivpm update
    
    # Explicitly use development dependencies
    ivpm update -d default-dev
    
    # Use release dependencies
    ivpm update -d default
    
    # Use custom dependency set
    ivpm update -d fpga-build

**Setting the default:**

.. code-block:: yaml

    package:
      name: my-project
      default-dep-set: default-dev

If ``default-dep-set`` is specified, that set is used when no ``-d`` option 
is given to ``ivpm update``.

Complete Examples
-----------------

**Example 1: Simple Dev/Release Split**

.. code-block:: yaml

    package:
      name: uart-ip
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            - name: wishbone-if
              url: https://github.com/vendor/wishbone.git
              branch: v1.0
        
        - name: default-dev
          deps:
            - name: wishbone-if
              url: https://github.com/vendor/wishbone.git
              branch: v1.0
            - name: cocotb
              src: pypi
            - name: vcd-tools
              url: https://github.com/tools/vcd.git

**Example 2: Multiple Build Targets**

.. code-block:: yaml

    package:
      name: soc-design
      default-dep-set: sim
      
      dep-sets:
        - name: sim
          deps:
            - name: cpu-core
              url: https://github.com/org/cpu.git
            - name: verilator
              url: https://github.com/verilator/verilator.git
        
        - name: fpga
          deps:
            - name: cpu-core
              url: https://github.com/org/cpu.git
            - name: xilinx-ips
              url: https://github.com/org/xilinx.git
        
        - name: asic
          deps:
            - name: cpu-core
              url: https://github.com/org/cpu.git
            - name: pdk-libs
              url: https://github.com/org/pdk.git

Hierarchical Dependency Sets
=============================

When a dependency has its own ``ivpm.yaml``, you can control which of *its* 
dependency sets is loaded.

Dependency Set Inheritance
---------------------------

By default, sub-packages inherit the parent's dependency set name:

.. code-block:: text

    Root Project
      dep-set: "default-dev"
        ↓
      Dependency A (uses "default-dev")
        ↓
      Sub-dependency A1 (uses "default-dev")

**Example:**

.. code-block:: yaml

    # Root project
    package:
      name: root-project
      dep-sets:
        - name: default-dev
          deps:
            - name: sub-package
              url: https://github.com/org/sub.git
              # No dep-set specified → inherits "default-dev"

When ``ivpm update -d default-dev`` runs:

1. Loads ``root-project`` with ``default-dev``
2. Fetches ``sub-package``
3. Loads ``sub-package`` with ``default-dev`` (inherited)

Overriding Dependency Sets
---------------------------

You can explicitly specify which dependency set a sub-package should use:

.. code-block:: yaml

    package:
      name: root-project
      dep-sets:
        - name: default-dev
          deps:
            - name: library-a
              url: https://github.com/org/library-a.git
              dep-set: default  # Use release deps from library-a
            
            - name: test-tools
              url: https://github.com/org/tools.git
              dep-set: default-dev  # Use dev deps from tools

**Use case:** Include a third-party library's release dependencies even when 
developing, to avoid pulling in all their development tools.

Default Dependency Set for Sub-Packages
----------------------------------------

You can set a default ``dep-set`` that all sub-packages will use:

.. code-block:: yaml

    package:
      name: root-project
      dep-sets:
        - name: default-dev
          default-dep-set: default  # All sub-packages use "default"
          deps:
            - name: library-a
              url: https://github.com/org/library-a.git
              # Uses "default" (from default-dep-set)
            
            - name: library-b
              url: https://github.com/org/library-b.git
              dep-set: default-dev  # Override: use dev deps

Dependency Set Diagram
======================

.. code-block:: text

    ┌──────────────────────────────────────────────────┐
    │ Root Project: ivpm update -d default-dev         │
    │   default-dep-set in dep-set: default            │
    └────────┬─────────────────────────────────────────┘
             │
             ├─ Library A (dep-set: not specified)
             │    → Uses "default" (from parent's default-dep-set)
             │    ├─ Sub-dep A1 → Uses "default" (inherited)
             │    └─ Sub-dep A2 → Uses "default" (inherited)
             │
             ├─ Library B (dep-set: default-dev)
             │    → Uses "default-dev" (explicitly overridden)
             │    └─ Sub-dep B1 → Uses "default-dev" (inherited)
             │
             └─ Tool C (dep-set: not specified)
                  → Uses "default" (from parent's default-dep-set)

Common Patterns
===============

Pattern 1: Shared Dependencies
-------------------------------

Include common dependencies in both sets:

.. code-block:: yaml

    dep-sets:
      - name: default
        deps:
          - name: core-lib
            url: https://github.com/org/core.git
      
      - name: default-dev
        deps:
          - name: core-lib
            url: https://github.com/org/core.git
          - name: pytest
            src: pypi

Pattern 2: Development Tools Only
----------------------------------

Keep development tools completely separate:

.. code-block:: yaml

    dep-sets:
      - name: default
        deps:
          - name: runtime-lib
            url: https://github.com/org/runtime.git
      
      - name: tools
        deps:
          - name: linter
            src: pypi
          - name: formatter
            src: pypi

Usage: ``ivpm update -d default && ivpm update -d tools``

Pattern 3: Conditional Dependencies
------------------------------------

Use different dependency sets for optional features:

.. code-block:: yaml

    dep-sets:
      - name: minimal
        deps:
          - name: core
            url: https://github.com/org/core.git
      
      - name: with-gui
        deps:
          - name: core
            url: https://github.com/org/core.git
          - name: gui-toolkit
            url: https://github.com/org/gui.git
      
      - name: with-plugins
        deps:
          - name: core
            url: https://github.com/org/core.git
          - name: plugin-system
            url: https://github.com/org/plugins.git

Best Practices
==============

1. **Use standard names** (``default``, ``default-dev``) for consistency
2. **Set default-dep-set** in your project root for developer convenience
3. **Keep release deps minimal** - only include what's needed at runtime
4. **Document custom sets** - explain when to use each set
5. **Control sub-dependencies** - use ``dep-set`` override to avoid pulling excess deps
6. **Test both profiles** - ensure ``default`` works without dev tools

Troubleshooting
===============

Missing Dependency Set
----------------------

**Error:** ``Dep-set <name> is not present in project <project>``

**Solution:** Check that:

1. The dep-set is defined in the project's ``ivpm.yaml``
2. The name matches exactly (case-sensitive)
3. If using ``dep-set`` override, the target package has that set

Package Not Loading
-------------------

**Issue:** Expected package not appearing in ``packages/``

**Check:**

1. Is it in the active dependency set?
2. Run ``ivpm update -d <set-name>`` with the correct set
3. Check if ``deps: skip`` is set on any dependencies

Wrong Dependencies Loaded
-------------------------

**Issue:** Getting development tools when you wanted release deps

**Solution:**

1. Check ``default-dep-set`` in root ``ivpm.yaml``
2. Explicitly specify: ``ivpm update -d default``
3. Check ``default-dep-set`` in each dependency set

See Also
========

- :doc:`core_concepts` - Understanding IVPM's model
- :doc:`package_types` - Complete dependency attribute reference
