################
Dependency Sets
################

Dependency sets are named collections of package dependencies that let you
maintain different profiles for different scenarios -- development vs release,
different build targets, or different feature sets.  For a conceptual
overview, see :doc:`core_concepts`.

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

Descriptions
------------

Both the ``package`` and individual dep-sets accept an optional ``description``
string — a one-line summary of what the package/catalog offers and what each
dep-set installs:

.. code-block:: yaml

    package:
      name: acme-tools
      description: Curated EDA toolchain bundles for the Acme flow
      dep-sets:
        - name: default
          description: Minimal set — simulator + waveform viewer
          deps:
            - name: sim
              url: https://github.com/acme/sim.git
        - name: gui-tools
          description: Adds the GUI debugger and schematic viewer
          deps:
            - name: debugger
              url: https://github.com/acme/debugger.git

Descriptions are purely informational. The package-level ``description`` is
shown by ``ivpm show deps`` (tree view and ``--json``); dep-set descriptions
make a manifest self-documenting for anyone browsing the available sets.

Standard Names
--------------

While you can use any names, IVPM recognizes these standard names:

``default``
    Release/runtime dependencies. Use this for packages you're distributing.

``default-dev``
    Development dependencies. Includes everything from ``default`` plus 
    development tools. This is no longer special - any name can be used.

Dep-Set Inheritance with ``uses``
----------------------------------

The ``uses`` field lets a dep-set inherit all packages from another dep-set
defined in the same ``ivpm.yaml``.  This avoids duplicating shared entries
across multiple sets.

**Merge rules**

- Every package from the *base* dep-set is copied into the *child* dep-set.
- If the same package name appears in both, the **child's definition wins**.
- Inheritance is resolved at parse time, so there is no runtime overhead.
- Chains of any depth are supported (``a`` uses ``b`` uses ``c`` …).
- A dep-set may name **several bases** (``uses: [a, b]``); they are merged
  left-to-right, so a later base overrides an earlier one and the child's own
  deps override them all.
- Cycles are detected and raise an error.
- Definition order does not matter; the base may be defined after the child.

**Basic example** — ``default-dev`` extends ``default``:

.. code-block:: yaml

    package:
      name: my-project
      default-dep-set: default-dev

      dep-sets:
        - name: default
          deps:
            - name: runtime-lib
              url: https://github.com/org/runtime-lib.git

        - name: default-dev
          uses: default          # inherit runtime-lib from above
          deps:
            - name: pytest
              src: pypi
            - name: test-framework
              url: https://github.com/org/test-framework.git

Running ``ivpm update -d default-dev`` installs ``runtime-lib``, ``pytest``,
and ``test-framework``.  Running ``ivpm update -d default`` installs only
``runtime-lib``.

**Overriding an inherited package** — pin a different version in the child:

.. code-block:: yaml

    dep-sets:
      - name: default
        deps:
          - name: mylib
            url: https://github.com/org/mylib.git
            branch: v1.0

      - name: default-dev
        uses: default
        deps:
          - name: mylib
            url: https://github.com/org/mylib.git
            branch: dev   # overrides the v1.0 branch from 'default'
          - name: pytest
            src: pypi

**Multi-level inheritance**:

.. code-block:: yaml

    dep-sets:
      - name: base
        deps:
          - name: core-lib
            src: pypi

      - name: dev
        uses: base
        deps:
          - name: pytest
            src: pypi

      - name: ci
        uses: dev
        deps:
          - name: coverage
            src: pypi
          # inherits core-lib (from base via dev) and pytest (from dev)

**Composing several dep-sets** — a dep-set can pull from more than one base by
giving ``uses`` a list.  This is the manifest-level counterpart to installing
several sets at once on the command line (``ivpm update -d sim -d gui``):

.. code-block:: yaml

    dep-sets:
      - name: sim
        deps:
          - name: simulator
            src: pypi

      - name: gui
        deps:
          - name: waveform-viewer
            src: pypi

      - name: everything
        uses: [sim, gui]   # merges both sets
        deps: []

Running ``ivpm update -d everything`` installs ``simulator`` and
``waveform-viewer``.  A package shared by ``sim`` and ``gui`` is installed once;
if their definitions differ, the later base in the list (``gui``) wins.

Per-Dep-Set Handler Configuration with ``with``
------------------------------------------------

A ``with:`` block controls handler behavior -- the Python venv mode, the Node
package manager, environment variables, direnv/agents/fusesoc settings, and so
on (see :doc:`python_packages`, :doc:`node_packages` and
:doc:`environment_paths`).  It is normally declared once at the ``package:``
level and applies to every ``ivpm update``.

An individual dep-set may also carry its own ``with:`` block.  When that dep-set
is the **selected install target**, its ``with:`` *refines* the package-level
one: the dep-set wins on any key it sets, and keys it leaves unset fall back to
the package-level value.  This lets one manifest describe, say, a ``uv`` venv
for day-to-day work and a system-Python install for CI:

.. code-block:: yaml

    package:
      name: my-project

      with:
        python:
          venv: uv                # default for every dep-set

      dep-sets:
        - name: default
          deps:
            - name: numpy
              src: pypi

        - name: ci
          uses: default
          with:
            python:
              venv: false               # override: reuse system Python
              system-site-packages: true
          deps: []

- ``ivpm update`` (or ``-d default``) builds a ``uv`` virtual environment.
- ``ivpm update -d ci`` skips the venv and uses the system Python.

The dep-set ``with:`` block accepts exactly the same keys as the package-level
one, and unknown keys are reported with a located error at parse time.

**Inheritance.** A dep-set's ``with:`` inherits through ``uses:`` just like its
packages do: base dep-sets are merged left-to-right and the dep-set's own
``with:`` overlays the result (own keys win).  Above, ``ci`` could omit
``system-site-packages`` and inherit it from a base that set it.

**Precedence** (highest wins): CLI flags (e.g. ``--py-uv``) → selected dep-set
``with:`` → package-level ``with:`` → built-in defaults.  The ``venv: false``
hard-skip rule still applies at whichever level sets it (see the priority table
in :doc:`python_packages`).

**Selecting several dep-sets.** When you install more than one dep-set at once
(``ivpm update -d a,b``), their ``with:`` blocks merge left-to-right, so the
later-named set wins on conflict -- matching how their packages merge.

**Exception: ``env`` is additive.** Every ``with:`` key above is *replaced* by
the overriding level, but ``with.env`` -- the list of environment-variable
directives -- is **concatenated** instead, package-level first.  A dep-set adds
to the environment and can override an individual variable (its directive is
emitted last, so it wins), but it can never clear or replace the inherited set.
The same holds for ``uses:`` inheritance and multi-dep-set selection.  See
:ref:`env-dep-set-scoped`.

Including Dep-Sets From Other Files
-----------------------------------

``uses:`` composes dep-sets *within a single file*.  To define a dep-set in a
**separate file** and merge it into your project, use the ``include:`` key --
see :doc:`multi_file`.  The two mechanisms are complementary:

- ``include:`` is **file composition** -- it merges partial ``package:`` bodies
  (including whole dep-sets) from sibling files.  Dep-sets merge by name, and a
  name defined in two files is an error.
- ``uses:`` is **dep-set inheritance** -- it copies packages from one dep-set
  into another.  It operates after includes are merged, so a dep-set may
  ``uses:`` a base contributed by an included file.

There is also a third, network-facing mechanism: a ``src: ivpm.yaml``
dependency (a *dep-set factory*) pulls one or more named dep-sets out of a
**remote** ``ivpm.yaml`` and folds them into the consuming dep-set.  In short:

- ``uses:`` -- inheritance **within a file**.
- ``include:`` -- composition **across local files** (see :doc:`multi_file`).
- ``src: ivpm.yaml`` -- consuming a dep-set **published elsewhere** (see
  :ref:`ivpm-yaml-factory` in :doc:`package_types`).

Using Dependency Sets
=====================

Selecting a Set
---------------

**On the command line:**

.. code-block:: bash

    # Use default dep-set (from default-dep-set or first dep-set)
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
is given to ``ivpm update``. If ``default-dep-set`` is not specified, the 
first dep-set listed in the file is used as the default.

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

**Example 3: Software Service Profiles**

.. code-block:: yaml

    package:
      name: data-platform
      default-dep-set: dev

      dep-sets:
        - name: api
          deps:
            - name: fastapi
              src: pypi
            - name: shared-models
              url: https://github.com/org/shared-models.git

        - name: worker
          deps:
            - name: celery
              src: pypi
            - name: shared-models
              url: https://github.com/org/shared-models.git

        - name: dev
          uses: api
          deps:
            - name: celery
              src: pypi
            - name: pytest
              src: pypi

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

See Also
========

- :doc:`core_concepts` - Understanding the update pipeline
- :doc:`package_types` - Complete dependency attribute reference
- :doc:`handlers` - How handlers process packages
- :doc:`troubleshooting` - Solutions to dependency set problems
