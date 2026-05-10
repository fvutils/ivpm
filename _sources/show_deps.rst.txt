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
