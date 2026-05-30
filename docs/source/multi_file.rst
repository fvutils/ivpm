####################################
Splitting ``ivpm.yaml`` Across Files
####################################

A single ``ivpm.yaml`` mixes several audiences and lifecycles: project
identity, admin/handler configuration, and the dependency lists that
developers touch day to day.  The ``include:`` key lets you split these
concerns across multiple files while still presenting IVPM with one logical
``package:`` definition.

For pulling a dependency set out of a *remote* ``ivpm.yaml``, see the
:ref:`dep-set factory <multi-file-factory>` note at the end of this page.


Why Split a File
================

Different parts of an ``ivpm.yaml`` change at very different rates and are
often owned by different people:

============================================  ===========================  ================
Concern                                       Who edits it                 How often
============================================  ===========================  ================
Handler/runtime config (``with``, ``vars``,   Project admin / tooling      Rarely
``paths``, ``env``)                           owner
Dependency lists (``dep-sets[*].deps``)       Developers                   Frequently
Project identity (``name``, ``version``)      Project owner                Rarely
============================================  ===========================  ================

A common goal is to keep admin/header settings *out* of the file developers
routinely edit when adding or bumping packages.  ``include:`` makes that
possible without inventing a second schema -- every file is just a partial
``package:`` body.


The ``include:`` Key
====================

``include:`` is a list of file paths, resolved **relative to the including
file**.  Each included file is parsed as a partial ``package:`` body and
merged into the includer:

.. code-block:: yaml

    # ivpm.yaml  -- developer-edited; PRs land here
    package:
      name: my-project
      include:
        - ivpm.admin.yaml
      dep-sets:
        - name: default
          deps:
            - name: pyyaml
              src: pypi

.. code-block:: yaml

    # ivpm.admin.yaml  -- CODEOWNERS-protected
    package:
      with:
        python:
          venv: project
      vars:
        tool_ver: "1.2.3"
      env:
        - name: PROJECT_ROOT
          path: .

Includes may themselves include other files; nesting is flattened before any
merge happens.  Variable (``${{var}}``) resolution runs **after** the full
merge, so an included file may reference a variable defined by the includer.


Merge Rules
===========

The including file is always **local** and wins on conflict.  "Local wins"
applies transitively: the file nearest the root overrides values from files it
pulls in.

- ``name``, ``version`` -- May **not** be set by an include; identity is
  anchored to the root file.  An include that sets either is an error.
- ``type`` -- May be set by an include (it is configuration, not identity).
  On conflict the root wins.
- ``dep-sets`` -- Merged **by name**.  A dep-set name defined in two files is
  an **error** (reporting both locations).  ``deps`` are never concatenated
  across files -- each dep-set has exactly one owning file.
- ``with``, ``vars`` -- Deep-merged.  On scalar conflict, the includer wins.
- ``paths`` -- Deep-merged as a map; leaf lists append.
- ``env``, ``env-sets``, ``setup-deps`` -- List-append (the include's items
  follow the local items).
- ``deps-dir``, ``default-dep-set`` and other scalars -- Local wins; adopted
  from the include only if the includer does not set them.

Source locations are preserved across files, so error messages still point at
the correct ``file:line:column`` -- even when the conflicting values come from
different files.


Recommended Convention
======================

Split along the admin/developer seam and protect the admin file with
CODEOWNERS:

.. code-block:: text

    ivpm.yaml          # developer-edited: identity + dep-sets, includes the admin file
    ivpm.admin.yaml    # CODEOWNERS-protected: with/vars/paths/env

Developers add and bump packages in ``ivpm.yaml`` without touching -- or
needing review authority over -- the handler/environment configuration in
``ivpm.admin.yaml``.


Errors
======

- **Cyclic include** -- a file that (transitively) includes itself is fatal.
  Two files reached by independent paths (a diamond) are allowed; only a true
  cycle is rejected.
- **Duplicate dep-set** -- the same dep-set name defined in two included files
  (or twice within one file) is fatal, reporting both locations.
- **Identity in an include** -- an include that sets ``name`` or ``version``
  is fatal.
- **Missing include file** -- a referenced path that does not exist is fatal.


.. _multi-file-factory:

Beyond Includes: Dep-Set Factories
==================================

``include:`` composes files on the **local filesystem** into one package
definition.  A related, complementary mechanism -- a package *source* that
pulls a named dep-set out of a **remote** ``ivpm.yaml`` -- is described in
:ref:`ivpm-yaml-factory` (:doc:`package_types`).  Use ``include:`` for file
composition within a project, and the ``src: ivpm.yaml`` source to consume a
dep-set published elsewhere.


See Also
========

- :doc:`dependency_sets` -- defining dep-sets and ``uses:`` inheritance
- :doc:`variables` -- ``${{var}}`` resolution across merged files
- :doc:`core_concepts` -- the update pipeline
