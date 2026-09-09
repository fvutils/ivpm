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
- Variable defaults cannot reference other variables.  The one exception is
  a ``match`` node, which may key off a platform builtin -- see
  :ref:`variables-match`.
- Names beginning with ``ivpm_`` are reserved -- see
  :ref:`variables-platform-builtins`.


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

*Derived* variables are the exception: platform builtins and ``match``
results are never saved, because they describe the machine rather than a
choice you made.  See :ref:`variables-match`.

Precedence Order
----------------

From highest to lowest:

1. ``-D`` command line
2. ``IVPM_VAR_<NAME>`` environment variable
3. Persisted value from ``ivpm.json``
4. Default from ``vars:`` block


.. _variables-platform-builtins:

Platform Builtins
=================

IVPM probes the running system and pre-declares nine variables describing
it.  They are ordinary variables -- same syntax, same precedence -- whose
default happens to come from the probe rather than from ``vars:``.

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Variable
     - Example
     - Notes
   * - ``ivpm_os``
     - ``linux``
     - ``linux``, ``macos``, or ``windows``
   * - ``ivpm_arch``
     - ``arm64``
     - ``x86_64`` or ``arm64``, spelled the same on every OS
   * - ``ivpm_platform``
     - ``linux-x86_64``
     - ``{os}-{arch}``; follows an override of either
   * - ``ivpm_libc``
     - ``glibc``
     - ``glibc``, ``musl``, or empty off Linux
   * - ``ivpm_libc_version``
     - ``2.39``
     - empty when unknown -- **read the warning below**
   * - ``ivpm_libc_major``
     - ``2``
     - empty when unknown
   * - ``ivpm_libc_minor``
     - ``39``
     - empty when unknown
   * - ``ivpm_distro``
     - ``ubuntu``
     - Linux only; empty when unknown
   * - ``ivpm_distro_version``
     - ``24.04``
     - empty when unknown

Every value is a string.  Unknown is the empty string, never the text
``None``.

.. code-block:: yaml

   url: https://example.com/tool-${{ivpm_os}}-${{ivpm_arch}}.tar.gz

**The** ``ivpm_`` **prefix is reserved.**  Declaring a variable whose name
starts with ``ivpm_`` in your own ``vars:`` block is a fatal error.
Shadowing a builtin is always a mistake, and one that is expensive to
diagnose after the fact.

**Builtins are overridable**, which is what makes cross-platform resolution
possible -- fetching the Linux artifact on a Mac to populate a container
image, or testing a manifest's Windows path from CI:

.. code-block:: bash

   ivpm update -Divpm_os=linux -Divpm_arch=x86_64
   IVPM_VAR_IVPM_OS=windows ivpm update

``ivpm_platform`` is recomputed from the resolved ``ivpm_os`` and
``ivpm_arch``, so it follows an override of either.  (Overriding
``ivpm_platform`` itself still wins outright.)  This matters beyond
tidiness: it is the value the lock file records as ``resolved_on``, so a
cross-resolved dependency is tagged with the platform it was resolved *for*
rather than the machine that resolved it.

.. warning::

   ``ivpm_libc_version`` describes **this machine**, not the artifact you
   want.  Building a manylinux tag from it --

   .. code-block:: yaml

      # WRONG: resolves to manylinux_2_39_x86_64, which will not exist
      url: .../pkg-manylinux_${{ivpm_libc_major}}_${{ivpm_libc_minor}}_x86_64.tar.gz

   -- produces a filename that 404s everywhere while looking correct.
   Publishers deliberately build against an *old* glibc so the artifact runs
   on new systems, so the tag you need is "the newest one whose requirement
   is at most mine", not "mine".

   Use ``ivpm_libc_version`` for reporting, for a family-keyed ``match`` on
   ``ivpm_libc``, and for floor checks.  Ordered compatibility selection over
   a candidate list is a separate mechanism; ``gh-rls`` already does it for
   GitHub release assets (see :doc:`github_releases`).


.. _variables-match:

Selecting a Value with ``match``
================================

``match`` selects one of several values by exact string equality.  It is
most useful in ``vars:``, where it turns a platform fact into the token a
URL needs:

.. code-block:: yaml

   package:
     name: my-project

     vars:
       emsdk_hash: f04ea239d533260dd1db760dd2d668d5f9a88d6b
       p:   { match: { on: "${{ivpm_os}}",   cases: { linux: linux, macos: mac, windows: win } } }
       sfx: { match: { on: "${{ivpm_arch}}", cases: { x86_64: "", arm64: "-arm64" } } }
       ext: { match: { on: "${{ivpm_os}}",   cases: { windows: zip }, default: tar.xz } }

     dep-sets:
       - name: wasm-build
         deps:
           - name: emsdk
             src: url
             cache: true
             url: https://storage.googleapis.com/webassembly/emscripten-releases-builds/${{p}}/${{emsdk_hash}}/wasm-binaries${{sfx}}.${{ext}}

Three small tables express the naming, which is a function of OS and arch
*independently*.  One five-row cross-product table would be where a row gets
forgotten.

.. list-table::
   :header-rows: 1
   :widths: 15 15 70

   * - Key
     - Required
     - Meaning
   * - ``on``
     - yes
     - A string, substituted before matching.  Usually one ``${{...}}``
       reference.
   * - ``cases``
     - yes
     - Mapping of exact-match key to value.  A value may be any YAML value,
       including a nested ``match``.
   * - ``default``
     - no
     - Value used when no case key equals ``on``.

**Semantics:**

- **Exact string equality only.**  Both sides are compared as strings, so an
  unquoted YAML ``24.04`` works as a case key.  There are no comparison
  operators, no regex, and no boolean combinators -- deliberately, and
  permanently: see the ``ivpm_libc_version`` warning above for the problem
  that would be expressed wrongly with them.
- **No matching case and no** ``default`` **is a fatal error**, naming the
  value that was matched and the cases that were available.  Yielding empty
  silently is how a construct like this rots.
- **Values are not restricted to scalars.**  Outside ``vars:``, a case may
  produce a mapping or a list -- an ``env:`` block, say.  A ``vars:`` entry
  must still end up a scalar.
- **Nesting is allowed**, evaluated innermost-first.  Two levels covers
  OS-by-arch; more than that is a smell.
- A mapping is a ``match`` node only when ``match`` is its **only** key.  Any
  other mapping is data and is left alone.

**Restriction:** inside ``vars:``, ``on:`` may reference platform builtins
only.  Referencing another user variable is a fatal error.  A ``match``
elsewhere in the document is evaluated after all variables are resolved and
has no such restriction.

Derived Variables Are Not Persisted
-----------------------------------

A variable is **derived** when its value is a function of the environment:
every ``ivpm_*`` builtin, and every variable produced by a ``match``.

Derived variables are **not written to** ``<deps-dir>/ivpm.json``, and are
re-evaluated on every run.  This is not an oversight -- persisted values sit
*above* defaults in precedence, so a persisted derived value would beat the
``match`` on a different machine.  Resolve on Linux, move the tree to a Mac
(or run the same checkout in a Linux container from a Mac host), and a
persisted ``p: linux`` would quietly select the Linux toolchain on macOS,
with no error anywhere.

Persistence is for pinning a *choice*, so if your variable "does not stick",
that is why.  A ``-D`` override of a derived variable is honoured for that
invocation but still not persisted; to make an override permanent, use
``IVPM_VAR_*`` or pass ``-D`` each time.


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
