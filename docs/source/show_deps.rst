Inspecting Dependencies (show deps)
=====================================

The ``ivpm show deps`` command introspects the **resolved** dependency graph of
the current project.  It reads ``packages/package-lock.json`` (when present)
and the ``ivpm.yaml`` files of installed sub-packages to produce a view that
faithfully reflects what is *actually on disk* after an ``ivpm update``.

.. contents:: On this page
   :local:
   :depth: 2

---

Flat table (default)
--------------------

Running ``ivpm show deps`` without any flags prints a colour-coded table where
every package appears **exactly once** — first-specifier-wins (the same
deduplication rule IVPM's resolver uses).

.. code-block:: bash

    $ ivpm show deps
    Name        Version   Specifier   Source   URL / commit
    ----------  --------  ----------  -------  --------------------
    pyyaml      6.0.1     root        pypi
    requests    2.31.0    root        pypi
    my-lib      abc1234   root        git      https://github.com/org/my-lib
    helper      -         my-lib      dir      packages/helper

Columns:

* **Name** — package name as it appears in ``ivpm.yaml``
* **Version** — resolved version (pypi) or ``-`` when not applicable
* **Specifier** — which package *first* declared this dependency (``root`` =
  the top-level project)
* **Source** — source type (``git``, ``pypi``, ``dir``, …)
* **URL / commit** — resolved URL and/or commit hash for git sources

Use ``--no-rich`` for plain text output without ANSI colours:

.. code-block:: bash

    $ ivpm show deps --no-rich

---

Dependency tree
---------------

Pass ``--tree`` (``-t``) to see the full hierarchy, including packages that
were shadowed because an ancestor already claimed them:

.. code-block:: bash

    $ ivpm show deps --tree --no-rich
    my-project
    ├── pyyaml       6.0.1    (pypi)
    ├── requests     2.31.0   (pypi)
    └── my-lib       abc1234  (git)
        ├── pyyaml   [shadowed by root]
        └── helper   -        (dir)

Shadowed entries are shown in the tree for completeness but are greyed out (or
bracketed in ``--no-rich`` mode).  They will not appear in the flat list.

---

Single-package detail
---------------------

Provide a package name as a positional argument to see full detail for that
package:

.. code-block:: bash

    $ ivpm show deps my-lib --no-rich
    Name:              my-lib
    Specifier:         root
    Source:            git
    URL:               https://github.com/org/my-lib.git
    Commit:            abc1234def5678...
    Also requested by: (none)

    $ ivpm show deps helper --no-rich
    Name:              helper
    Specifier:         my-lib
    Source:            dir
    URL:               packages/helper
    Also requested by: (none)

The command exits with status **1** if the package is not found.

---

Machine-readable JSON
---------------------

Add ``--json`` to get structured output for scripting and CI pipelines.

**Flat list** (default + ``--json``):

.. code-block:: bash

    $ ivpm show deps --json
    [
      {
        "name": "pyyaml",
        "specifier": "root",
        "source": "pypi",
        "version": "6.0.1",
        "url": null,
        "commit": null,
        "also_requested_by": []
      },
      ...
    ]

**Tree** (``--tree --json``):

.. code-block:: bash

    $ ivpm show deps --tree --json
    {
      "name": "my-project",
      "children": [
        {
          "name": "pyyaml",
          "specifier": "root",
          "source": "pypi",
          "version": "6.0.1",
          "shadowed": false,
          "children": []
        },
        ...
      ]
    }

**Single package** (``<name> --json``):

.. code-block:: bash

    $ ivpm show deps my-lib --json
    {
      "name": "my-lib",
      "specifier": "root",
      "source": "git",
      "url": "https://github.com/org/my-lib.git",
      "commit": "abc1234",
      "version": null,
      "also_requested_by": []
    }

Documentation fields
~~~~~~~~~~~~~~~~~~~~

Every dependency object additionally carries ``description`` and ``doc`` — the
prose declared on its manifest entry (see :doc:`documenting`), or ``null`` when
the entry declares none.  These come from the **declaring manifest**, never from
``package-lock.json``; see :doc:`package_lock`.

The tree form adds the root project's ``doc`` and a ``dep_set_info`` object
describing the selected dep-set:

.. code-block:: bash

    $ ivpm show deps --tree --json -d sim
    {
      "project": "demo",
      "description": "A demonstration workspace.",
      "doc": "Long-form prose about the project.\n",
      "dep_set": "sim",
      "dep_set_info": {
        "name": "sim",
        "description": "Base plus the simulator VIP.",
        "doc": "Use this one in CI.",
        "kind": "collection",
        "uses": ["base"],
        "contains": ["plainlib", "somelib", "vip"],
        "own": ["somelib", "vip"],
        "inherited_from": {"plainlib": "base"},
        "overrides": {
          "somelib": {
            "base": "base",
            "spec": "https://example.com/somelib.git@commit=a1b2c3d4e5f6",
            "displaced": {"url": "...", "commit": "a1b2c3d4e5f6"}
          }
        }
      },
      "deps": [ ... ]
    }

``contains`` is the full resolved leaf set, as before.  Beside it,
``own`` / ``inherited_from`` / ``overrides`` give the **inheritance delta** — the
three flavours described in :doc:`dependency_sets`:

``own``
    Names this dep-set declared in its own ``deps:`` (added *or* overriding).

``inherited_from``
    ``{package: base dep-set}`` for names supplied by a base and not declared
    here.  With a chain (``ci uses sim uses rtl``), the attribution is to the
    **direct** base — ``sim`` — not to the original declarer; walk ``uses``
    upward for the rest of the chain.

``overrides``
    ``{package: {base, spec, displaced}}`` for names declared here that
    displaced a base's entry.  ``displaced`` is the *replaced* spec, which is
    what lets a renderer show ``UVM_1_2 → UVM_2_0`` rather than a bare
    "overridden".

The same three keys appear per dep-set in
``ivpm show deps --from <manifest> --json``.

Useful ``jq`` recipes:

.. code-block:: bash

    # List all dependency names
    $ ivpm show deps --json | jq '.[].name'

    # Find packages not directly specified by root
    $ ivpm show deps --json | jq '[.[] | select(.specifier != "root")]'

    # Extract commit hashes for all git deps
    $ ivpm show deps --json | jq '[.[] | select(.commit) | {name, commit}]'

---

Selecting a dep-set
-------------------

By default ``ivpm show deps`` loads the ``default-dev`` dep-set from the root
``ivpm.yaml``.  Use ``-d`` / ``--dep-set`` to select a different one:

.. code-block:: bash

    $ ivpm show deps -d ci

---

Inspecting another project
--------------------------

Use ``-p`` / ``--project-dir`` to introspect a project outside the current
working directory:

.. code-block:: bash

    $ ivpm show deps -p /path/to/other/project

---

When the lock file is absent
----------------------------

If ``packages/package-lock.json`` does not exist (e.g. before the first
``ivpm update``), ``ivpm show deps`` still works but emits a warning and
populates only the fields declared in ``ivpm.yaml`` — resolved versions and
commit hashes will be missing.

.. code-block:: bash

    $ ivpm show deps --no-rich
    Warning: packages/package-lock.json not found; showing declared deps only.
    ...

Run ``ivpm update`` first to get a fully resolved view.

---

.. _show-bom:

Bill of materials (``ivpm show bom``)
--------------------------------------

``ivpm show bom`` reports one row per package in the resolved closure, joining
three things that otherwise live apart:

* what the manifest **declared** — name, source, prose, and the pin
  (``branch`` / ``tag`` / ``commit`` / ``version`` / ``module``);
* what ``package-lock.json`` **resolved** it to — ``version_resolved``,
  ``commit_resolved``, ``reproducible``, ``cache``, and patch fingerprints;
* what the package itself **publishes** — ``license``, ``homepage`` and
  ``documentation``.

.. code-block:: bash

    $ ivpm show bom
    demo  v1.0.0  (base)
      A demonstration workspace.

    Package   Src   Version / Ref  License      Repro  Patches            Declared by
    ------------------------------------------------------------------------------
    plainlib  pypi  1.2.3          BSD-3-Clause  yes                      root
    somelib   git   a1b2c3d4       MIT           yes    fix.patch(0123456) root

It performs **no resolution and no fetching** — it is a pure projection over
data that already exists, so it is safe to run in CI.

Where the metadata comes from
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``license`` / ``homepage`` / ``documentation`` are read from the installed
package's own ``ivpm.yaml``.  When it declares none, IVPM falls back to the
package's upstream manifest — ``pyproject.toml`` (``project.license``,
``project.urls.Homepage``, ``project.urls.Documentation``) or ``package.json``
(``license``, ``homepage``).  **The ``ivpm.yaml`` manifest always wins**;
upstream metadata only fills a field the manifest left unset.  Most Python and
Node dependencies therefore get a usable BOM row without restating anything.

JSON output
~~~~~~~~~~~

.. code-block:: bash

    $ ivpm show bom --json
    {
      "project": "demo",
      "version": "1.0.0",
      "dep_set": "base",
      "description": "A demonstration workspace.",
      "license": "Apache-2.0",
      "homepage": "https://demo.example",
      "documentation": "https://docs.demo.example",
      "maintainers": ["Alice <alice@example.com>"],
      "lock_available": true,
      "packages": [
        {
          "name": "somelib",
          "src": "git",
          "description": "Vendor DPI shim.",
          "doc": "Pinned to a commit rather than a tag ...\n",
          "declared": {"url": "https://example.com/somelib.git",
                       "commit": "a1b2c3d4e5f6"},
          "version_resolved": null,
          "commit_resolved": "a1b2c3d4e5f6789012345678",
          "reproducible": true,
          "cache": true,
          "patches": [{"name": "fix.patch", "md5": "0123456789abcdef",
                       "strip": 1, "directory": null}],
          "patchset_id": "deadbeef",
          "license": "MIT",
          "homepage": "https://somelib.example",
          "documentation": "https://docs.somelib.example",
          "specifier": "root",
          "dep_set": "base",
          "scope": ""
        }
      ]
    }

``reproducible`` is ``false`` for sources that cannot be pinned — a ``dir:``
dependency or a ``module:`` reference — and ``null`` when no lock file exists.

Diffing two of these between release tags is a supply-chain change report:

.. code-block:: bash

    $ git checkout v1.0 && ivpm show bom --json -o /tmp/bom-1.0.json
    $ git checkout v1.1 && ivpm show bom --json -o /tmp/bom-1.1.json
    $ diff <(jq -S . /tmp/bom-1.0.json) <(jq -S . /tmp/bom-1.1.json)

Options mirror ``show deps``: ``--json``, ``--no-rich``, ``-p/--project-dir``,
``-d/--dep-set`` and ``-o/--output``.
