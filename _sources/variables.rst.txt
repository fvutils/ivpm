#########
Variables
#########

Variables let you parameterize ``ivpm.yaml`` so that values like
wacfg names, changelist numbers, or feature flags can be changed from
the command line without editing the file.

Overview
========

Declare variables in a ``vars:`` block, reference them with ``${{name}}``
anywhere in the file, and override them with ``-D`` on the command line.
Every variable has a default, so the file always works standalone.

.. code-block:: yaml

   package:
     name: my_project
     vars:
       wacfg:  default
       cl:     7716052

     dep-sets:
       - name: default
         deps:
           - name: my_env
             src: cbwa
             wacfg: ${{wacfg}}

           - name: smn
             src: p4_mkwa
             codeline: smn15
             branch: smn15_main
             changelist: ${{cl}}

.. code-block:: bash

   # Use defaults
   ivpm update

   # Override wacfg
   ivpm update -Dwacfg=export

   # Override both
   ivpm update -Dwacfg=rtl_only -Dcl=8000000


Declaring Variables
===================

Variables are declared in the ``vars:`` block at the ``package:``
level, alongside ``name``, ``dep-sets``, ``with``, etc.

.. code-block:: yaml

   package:
     name: my_project
     vars:
       wacfg:   default
       version: 7716052
       cache:   true

Each key is a variable name.  The value is the default.  Defaults are
always converted to strings internally, so YAML booleans (``true``)
and numbers (``7716052``) are fine.

**Rules:**

- Variable names must be valid identifiers: letters, digits, and
  underscores, starting with a letter or underscore.
- Every variable must have a default value.
- Variable defaults cannot reference other variables.


Referencing Variables
=====================

Use ``${{name}}`` in any scalar value anywhere below ``package:``:

.. code-block:: yaml

   changelist: ${{cl}}
   branch: ${{codeline}}_main
   url: https://${{host}}/${{repo}}.git

References can be the entire value or embedded in a larger string.
Multiple references in one value are supported.

**Where references work:**

- Dependency option values (``wacfg:``, ``changelist:``, ``url:``, etc.)
- ``with:`` section values
- ``env:`` section values
- ``paths:`` section values

**Escaping:**

To produce a literal ``${{`` in output, write ``$${{``:

.. code-block:: yaml

   literal: $${{not_a_variable}}   # produces "${{not_a_variable}}"

**Error handling:**

Referencing an undeclared variable is a fatal error at parse time,
catching typos immediately.


Overriding Variables
====================

Command Line (``-D``)
---------------------

The ``-D`` flag is available on ``ivpm update`` and ``ivpm clone``.
It can be repeated:

.. code-block:: bash

   ivpm update -Dwacfg=export -Dcl=8000000
   ivpm clone https://github.com/my/repo.git -Dwacfg=rtl_only

Specifying a variable not declared in ``vars:`` is a fatal error.

Environment Variables
---------------------

IVPM checks ``IVPM_VAR_<NAME>`` (uppercased) when a variable has no
``-D`` override.  Useful for CI/CD pipelines:

.. code-block:: bash

   export IVPM_VAR_WACFG=export
   ivpm update   # uses wacfg=export

Persistence
-----------

Resolved variable values are saved in ``<deps-dir>/ivpm.json`` after
each ``ivpm update``.  On subsequent runs without ``-D``, the saved
values are used instead of the defaults.

.. code-block:: bash

   ivpm update -Dwacfg=export    # saves wacfg=export
   ivpm update                   # still uses wacfg=export
   ivpm update -Dwacfg=default   # switches back

Precedence Order
----------------

From highest to lowest:

1. ``-D`` command line
2. ``IVPM_VAR_<NAME>`` environment variable
3. Persisted value from ``ivpm.json``
4. Default from ``vars:`` block


Examples
========

Parameterized wacfg for cbwa
-----------------------------

.. code-block:: yaml

   package:
     name: nbio_soc
     vars:
       wacfg: default
     dep-sets:
       - name: default
         deps:
           - name: nbio_env
             src: cbwa
             env-dir: _env/local
             wacfg: ${{wacfg}}

.. code-block:: bash

   ivpm update                   # full development
   ivpm update -Dwacfg=export    # minimal build

Shared version across dependencies
-----------------------------------

.. code-block:: yaml

   package:
     name: soc_workspace
     vars:
       smn_cl:    7716052
       iohub_cl:  8398261
       cache:     false
     dep-sets:
       - name: default
         deps:
           - name: smn
             src: p4_mkwa
             codeline: smn15
             branch: smn15_main
             changelist: ${{smn_cl}}
             cache: ${{cache}}
           - name: iohubutils
             src: p4_mkwa
             codeline: iohubutils
             branch: iohubutils_main
             changelist: ${{iohub_cl}}
             cache: ${{cache}}

.. code-block:: bash

   ivpm update -Dcache=true                    # CI: enable caching
   ivpm update -Dsmn_cl=latest -Diohub_cl=latest  # latest sources

CI pipeline with environment variables
--------------------------------------

.. code-block:: bash

   # In CI config
   export IVPM_VAR_WACFG=export
   export IVPM_VAR_CACHE=true
   ivpm update
