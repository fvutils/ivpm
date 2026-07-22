#########
Variables
#########

Variables let you parameterize ``ivpm.yaml`` so that values like
branch names, versions, or feature flags can be changed from
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
       branch:  main
       version: 1.4.0

     dep-sets:
       - name: default
         deps:
           - name: my_lib
             url: https://github.com/acme/my_lib.git
             branch: ${{branch}}

           - name: my_tool
             url: https://github.com/acme/my_tool.git
             tag: v${{version}}

.. code-block:: bash

   # Use defaults
   ivpm update

   # Override branch
   ivpm update -Dbranch=develop

   # Override both
   ivpm update -Dbranch=develop -Dversion=1.5.0


Declaring Variables
===================

Variables are declared in the ``vars:`` block at the ``package:``
level, alongside ``name``, ``dep-sets``, ``with``, etc.

.. code-block:: yaml

   package:
     name: my_project
     vars:
       branch:  main
       retries: 3
       cache:   true

Each key is a variable name.  The value is the default.  Defaults are
always converted to strings internally, so YAML booleans (``true``)
and numbers (``3``) are fine.

**Rules:**

- Variable names must be valid identifiers: letters, digits, and
  underscores, starting with a letter or underscore.
- Every variable must have a default value.
- Variable defaults cannot reference other variables.


Referencing Variables
=====================

Use ``${{name}}`` in any scalar value anywhere below ``package:``:

.. code-block:: yaml

   tag: v${{version}}
   branch: ${{branch}}
   url: https://${{host}}/${{repo}}.git

References can be the entire value or embedded in a larger string.
Multiple references in one value are supported.

**Where references work:**

- Dependency option values (``branch:``, ``tag:``, ``url:``, etc.)
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

   ivpm update -Dbranch=develop -Dversion=1.5.0
   ivpm clone https://github.com/my/repo.git -Dbranch=develop

Specifying a variable not declared in ``vars:`` is a fatal error.

Environment Variables
---------------------

IVPM checks ``IVPM_VAR_<NAME>`` (uppercased) when a variable has no
``-D`` override.  Useful for CI/CD pipelines:

.. code-block:: bash

   export IVPM_VAR_BRANCH=develop
   ivpm update   # uses branch=develop

Persistence
-----------

Resolved variable values are saved in ``<deps-dir>/ivpm.json`` after
each ``ivpm update``.  On subsequent runs without ``-D``, the saved
values are used instead of the defaults.

.. code-block:: bash

   ivpm update -Dbranch=develop  # saves branch=develop
   ivpm update                   # still uses branch=develop
   ivpm update -Dbranch=main     # switches back

Precedence Order
----------------

From highest to lowest:

1. ``-D`` command line
2. ``IVPM_VAR_<NAME>`` environment variable
3. Persisted value from ``ivpm.json``
4. Default from ``vars:`` block


Examples
========

Parameterized branch for a dependency
--------------------------------------

.. code-block:: yaml

   package:
     name: my_app
     vars:
       branch: main
     dep-sets:
       - name: default
         deps:
           - name: my_lib
             url: https://github.com/acme/my_lib.git
             branch: ${{branch}}

.. code-block:: bash

   ivpm update                    # track main
   ivpm update -Dbranch=develop   # track develop

Shared version across dependencies
-----------------------------------

.. code-block:: yaml

   package:
     name: my_workspace
     vars:
       lib_ver:   1.4.0
       tool_ver:  2.1.0
       cache:     false
     dep-sets:
       - name: default
         deps:
           - name: my_lib
             url: https://github.com/acme/my_lib.git
             tag: v${{lib_ver}}
             cache: ${{cache}}
           - name: my_tool
             url: https://github.com/acme/my_tool.git
             tag: v${{tool_ver}}
             cache: ${{cache}}

.. code-block:: bash

   ivpm update -Dcache=true                         # CI: enable caching
   ivpm update -Dlib_ver=latest -Dtool_ver=latest   # latest sources

CI pipeline with environment variables
--------------------------------------

.. code-block:: bash

   # In CI config
   export IVPM_VAR_BRANCH=develop
   export IVPM_VAR_CACHE=true
   ivpm update
