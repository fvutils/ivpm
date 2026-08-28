.. _remote-catalogs:

Remote Catalogs
===============

A *remote catalog* is an ``ivpm.yaml`` published at a location you can fetch — a
local path, a ``file://`` URL, or an ``http(s)://`` URL — whose dependency sets
enumerate named, described bundles of packages. Consumers can **browse** the
catalog and **install** a chosen dependency set into the current directory with
a single command, without first cloning a repository.

This builds on two pieces you already know:

- :doc:`dependency_sets` — each ``dep-set`` is a named bundle, and both the
  ``package`` and each ``dep-set`` accept an optional ``description``.
- ``ivpm update`` and ``ivpm show deps`` — the catalog workflow is exposed
  through a ``--from`` option on each, rather than as new commands.

Authoring a catalog
--------------------

A catalog is an ordinary ``ivpm.yaml``. Add ``description`` strings so consumers
can tell the sets apart:

.. code-block:: yaml

    package:
      name: acme-tools
      version: 2.3.0
      description: Curated EDA toolchain bundles for the Acme flow
      default-dep-set: default
      dep-sets:
        - name: default
          description: Minimal set — simulator + waveform viewer
          deps:
            - name: sim
              url: https://github.com/acme/sim.git
        - name: gui-tools
          description: Adds the GUI debugger and schematic viewer
          deps:
            - name: sim
              url: https://github.com/acme/sim.git
            - name: debugger
              url: https://github.com/acme/debugger.git

Host the file anywhere reachable over HTTP(S), or share it as a path.

.. note::

   Remote (URL) catalogs must be **self-contained**: an ``include:`` directive
   resolves against the local filesystem and is therefore rejected for manifests
   fetched over HTTP(S). Inline the contents instead. Local ``--from`` paths may
   use ``include:`` normally.

Browsing a catalog
------------------

``ivpm show deps --from <path-or-url>`` fetches and parses the manifest and lists
its catalog — the package description and every dependency set with its
description — **without** fetching any dependencies or touching the current
directory:

.. code-block:: bash

    $ ivpm show deps --from https://example.com/acme/ivpm.yaml

    acme-tools  v2.3.0
      Curated EDA toolchain bundles for the Acme flow

    Dependency sets:
      default    Minimal set — simulator + waveform viewer (default)
      gui-tools  Adds the GUI debugger and schematic viewer

    Install with:  ivpm update --from https://example.com/acme/ivpm.yaml -d <dep-set>

Add ``--json`` for machine-readable output.

Installing from a catalog
-------------------------

``ivpm update --from <path-or-url>`` installs a dependency set into the current
directory. Use ``-d`` to choose the set (otherwise the catalog's default set is
used):

.. code-block:: bash

    # Install the 'gui-tools' set into ./packages
    $ ivpm update --from https://example.com/acme/ivpm.yaml -d gui-tools

    # Place the resolved workspace under ./vendor instead
    $ ivpm update --from ./acme/ivpm.yaml --deps-dir vendor

Several dep-sets can be installed at once — repeat ``-d`` or give a
comma-separated list. Their packages are merged into the one workspace (a
package pulled by more than one set is installed once):

.. code-block:: bash

    $ ivpm update --from ./acme/ivpm.yaml -d default -d gui-tools
    $ ivpm update --from ./acme/ivpm.yaml -d default,gui-tools

When more than one set is installed, the ``source_manifest`` block records them
under a ``dep_sets`` list (rather than the singular ``dep_set``).

``--from`` may name the manifest file directly (``.../ivpm.yaml``) or just the
directory or host that contains it, in which case ``ivpm.yaml`` is looked for
within that location:

.. code-block:: bash

    # Equivalent to --from https://edapack.github.io/ivpm.yaml
    $ ivpm update --from https://edapack.github.io

    # Equivalent to --from ./acme/ivpm.yaml
    $ ivpm update --from ./acme

This works as long as ``<location>/ivpm.yaml`` exists.

The external manifest is **not** copied into your workspace. Instead, the
resolved ``package-lock.json`` records a ``source_manifest`` block naming the
``--from`` source and the installed dep-set, so the workspace is self-describing
(see :doc:`package_lock`). Because the source is recorded, a bare ``ivpm
update`` in that directory later replays it -- you do not have to repeat
``--from``.

Installing into a shared tool directory
---------------------------------------

A published catalog can be consumed two ways. ``ivpm update --from`` pulls a
dependency set into a *project*, under that project's deps-dir. ``ivpm
install`` assembles a *shared tool directory* instead, where the destination
directory itself is the deps-dir and several catalogs can be combined:

.. code-block:: bash

    $ ivpm install -o /opt/eda \
        --from https://edapack.github.io -d digital-sim \
        --from https://mycorp.internal/tools -d common

See :doc:`tool_directories`.

One driving manifest per workspace
----------------------------------

A workspace has exactly one driving manifest. If the target directory already
contains its own ``ivpm.yaml``, ``ivpm update --from`` refuses to run rather than
shadow it. To consume an external set from a directory that already has a
manifest, reference the external source **from your own** ``ivpm.yaml`` instead
(for example, as a ``src: ivpm.yaml`` dep-set factory — see :doc:`package_lock`)
so your local file remains the single source of truth.

Trust
-----

``ivpm update --from <url>`` fetches the manifest and then fetches whatever
git / HTTP / PyPI sources it names — the same capability (and the same trust
posture) as ``ivpm clone <git-url>``. Point ``--from`` only at sources you trust.
