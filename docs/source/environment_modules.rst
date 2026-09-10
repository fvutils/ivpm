####################
Environment Modules
####################

IVPM can treat an `Environment Modules
<https://modules.readthedocs.io/>`_ modulefile as a dependency.  The
``src: module`` source type resolves a modulefile to a directory on disk
and sets the package path, and the :ref:`modules handler
<handler-modules>` emits a ``module load`` line for it into
``packages/modules.envrc``.

This is how a project declares "I need the site's ``gcc/15.2.0``" or "I
need the modulefile that ships in ``etc/modulefiles/``" alongside its git
and PyPI dependencies, instead of documenting it in a README and hoping
everyone runs the right ``module load`` by hand.

When to use module dependencies
===============================

Use a module dependency when the tool is **already installed** and the
site publishes a modulefile for it.  IVPM does not install the tool; it
records which module the workspace expects and arranges for it to be
loaded when the environment is entered.

For tools IVPM should fetch and install itself, use a fetching source
type instead -- see :doc:`package_types`.

Two ways to name a module
=========================

A module dependency names its modulefile in exactly one of two ways.  The
*key* is the discriminator -- IVPM never inspects the value to guess which
form you meant:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Key
     - Meaning
   * - ``module:``
     - A **logical specifier** (``gcc/15.2.0``), looked up through the
       modules system against ``MODULEPATH``
   * - ``modulefile:``
     - A **path to a modulefile** on disk, used directly

``module:`` and ``modulefile:`` are mutually exclusive; so are
``modulefile:`` and ``version:``.  Declaring both is an error rather than
a silent preference.

Logical specifiers (``module:``)
--------------------------------

.. code-block:: yaml

    deps:
      - name: gcc
        src: module
        module: gcc/15.2.0

The specifier is resolved by asking the modules system where the
modulefile lives, so the module must be visible on ``MODULEPATH`` and a
modules installation must be present.  If it is not available, the update
fails with the specifier named in the message.

``version:`` is shorthand for the common ``<name>/<version>`` layout:

.. code-block:: yaml

    # equivalent to  module: vcs/2024.09
    - name: vcs
      src: module
      version: "2024.09"

Modulefile paths (``modulefile:``)
----------------------------------

.. code-block:: yaml

    deps:
      # relative to the ivpm.yaml that declares it
      - name: mytool
        src: module
        modulefile: etc/modulefiles/mytool/1.0

      # environment variables and ~ are expanded
      - name: othertool
        src: module
        modulefile: $SITE_MODULES/othertool/1.0

      # absolute paths work as written
      - name: thirdtool
        src: module
        modulefile: /opt/modulefiles/thirdtool/2.1

A relative ``modulefile:`` is resolved against the directory of the
``ivpm.yaml`` that declared it -- **not** the current directory and not
the root project.  That is the same rule every other relative path in
IVPM follows, so a dependency's own modulefiles keep working when the
package is consumed from somewhere else.

The path must name an existing file.  A path that does not exist, or that
names a directory, is a fatal error; the directory case says so
explicitly and points you at ``module:``.

**No modules installation is required for this form.**  The path is
declared, not looked up, so a project whose module dependencies are all
``modulefile:`` resolves on a machine with no Modules or Lmod at all.
(The one exception is ``resolve-root: true``, described below, which does
run ``module show``.)

Which one to use
----------------

Use ``module:`` when you want the site's resolution rules -- module
aliases, default versions, ``MODULEPATH`` ordering, and hierarchical
layouts all keep working.

Use ``modulefile:`` when the modulefile is not on ``MODULEPATH``: it is
vendored in the project, produced by another IVPM dependency, or lives in
a site directory nobody has run ``module use`` on.

Choosing the root directory
===========================

``update()`` sets the package's path so downstream handlers can discover
content in it.  The root is chosen by the first rule that applies:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Setting
     - Root
   * - ``root: <path>``
     - Used as written (environment variables expanded).  Wins over
       everything else
   * - ``resolve-root: true``
     - Parsed out of ``module show`` output: ``setenv *_HOME``, then
       ``prepend-path PATH .../bin`` (its parent), then ``set root``
   * - *(default)*
     - The modulefile's parent directory

``resolve-root:`` falls back to the modulefile's directory when it cannot
determine a prefix -- including when there is no modules installation to
run ``module show`` at all.

.. code-block:: yaml

    - name: gcc
      src: module
      module: gcc/15.2.0
      resolve-root: true     # use $GCC_HOME, not the modulefile dir

    - name: mytool
      src: module
      modulefile: etc/modulefiles/mytool/1.0
      root: /opt/mytool/1.0  # say it outright

Generated output
================

The modules handler writes ``packages/modules.envrc`` with one
``module load`` line per dependency, sorted by package key:

.. code-block:: sh

    # Generated by IVPM modules handler -- do not edit manually
    module load gcc/15.2.0
    module load /home/user/proj/etc/modulefiles/mytool/1.0

A ``module:`` dependency contributes its logical specifier; a
``modulefile:`` dependency contributes the resolved absolute path.  There
is no ``module use`` line -- see the requirements below.

``packages/packages.envrc`` (written by the :ref:`direnv handler
<handler-direnv>`) is patched with a sentinel-wrapped section that sources
it:

.. code-block:: sh

    # --- ivpm:modules begin ---
    source_env ./modules.envrc
    # --- ivpm:modules end ---

Both the file and the sentinel section are removed when the last module
dependency goes away, so a shrinking dep-set does not leave a stale
``module load`` behind.

To suppress the ``module load`` line while still resolving the package --
useful when you only want the root directory for handler discovery -- set
``load: false``:

.. code-block:: yaml

    - name: mytool
      src: module
      modulefile: etc/modulefiles/mytool/1.0
      type:
        module:
          load: false

Lock entries
============

``packages/package-lock.json`` records each module dependency under
``modules``, tagged with the key it was declared with:

.. code-block:: json

    "modules": {
      "gcc":    { "module": "gcc/15.2.0" },
      "mytool": { "modulefile": "/home/user/proj/etc/modulefiles/mytool/1.0" }
    }

See :doc:`package_lock`.

Requirements
============

* The ``module:`` form requires a working modules installation
  (Environment Modules 3.x/4.x or Lmod) with the module visible on
  ``MODULEPATH``.
* The ``modulefile:`` form requires **Environment Modules 4.x or Lmod** at
  environment-entry time.  ``module load <path>`` is not supported by
  Environment Modules 3.x, which can only load a module by name after its
  directory has been added to ``MODULEPATH``.  Resolution itself needs no
  modules installation at all -- only the generated ``module load`` line
  does.

Troubleshooting
===============

``Module '<spec>' is not available (module_path returned None)``
    The modules system does not know that specifier.  Check
    ``module avail <spec>`` and ``MODULEPATH``.  If the modulefile is on
    disk but not on ``MODULEPATH``, switch the dependency to
    ``modulefile:``.

``modulefile '<path>' does not exist (resolved to <abs path>)``
    The message shows both what you wrote and where it resolved to.  A
    relative path resolves against the declaring ``ivpm.yaml``, so check
    that directory rather than the one you ran ``ivpm`` from.

``modulefile: '<path>' resolves to a directory``
    ``modulefile:`` takes the modulefile itself, not the directory holding
    it -- ``etc/modulefiles/mytool/1.0``, not ``etc/modulefiles/mytool``.
    If you meant a logical name, use ``module:``.

``module load`` of a path fails on Environment Modules 3.x
    3.x cannot load a modulefile by path.  Add the directory to
    ``MODULEPATH`` yourself and use the logical form instead::

        # in the project's own .envrc, before packages.envrc
        module use ./etc/modulefiles

    .. code-block:: yaml

        - name: mytool
          src: module
          module: mytool/1.0

.. seealso::

   :doc:`handlers`
       The modules handler, its phase, and its ordering against direnv.

   :doc:`package_types`
       All source types, including ``module``.
