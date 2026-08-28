.. _tool-directories:

Shared Tool Directories
=======================

A *tool directory* is a shared, project-independent tree of tools assembled
from one or more :doc:`remote catalogs <remote_catalogs>`. Several projects --
or a whole team, or a CI fleet -- point at it instead of each fetching their
own copy of a simulator.

``ivpm install`` builds one:

.. code-block:: bash

    $ ivpm install -o /opt/eda \
        --from https://edapack.github.io -d digital-sim -d digital-formal \
        --from https://mycorp.internal/tools -d common

The defining property is that **the destination directory is the deps-dir**.
There is no ``packages`` level in between:

.. code-block:: text

    /opt/eda/
      packages.envrc          <- source this to get the tools on PATH
      package-lock.json
      ivpm.json
      verilator/
      yosys/
      python/

Consumers use it by sourcing the generated envrc, exactly as they would a
project's own:

.. code-block:: bash

    $ source_env /opt/eda/packages.envrc     # from a project .envrc
    # or, without direnv:
    $ direnv exec /opt/eda <command>

``install`` vs ``update``
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 26 26 28

   * -
     - ``update``
     - ``update --from``
     - ``install``
   * - Driving manifest
     - the local ``ivpm.yaml``
     - one external manifest
     - **one or more** external manifests
   * - Where deps land
     - ``<project>/packages/``
     - ``<cwd>/packages/``
     - **the outdir itself**
   * - Writes above the deps-dir
     - yes (``.agents/``, ``fusesoc.conf``, …)
     - yes
     - **never**

Use ``update --from`` to pull a catalog into a project. Use ``install`` when
the result is not part of any project and will be shared.

The source-spec grammar
-----------------------

``install`` accepts several sources, each with its own selections. Options are
either **global** or **per-source**, and the rule is positional:

* Everything before the first ``--from`` is **global**: ``-o``, ``--resolve``,
  ``--on-collision``, ``--on-project-ref``, ``--root-var``, and the usual
  ``--py-skip-install`` family.
* ``-d``/``--dep-set``, ``-D``/``--define`` and ``--as`` are **per-source**:
  they apply to the ``--from`` they follow, and only to it.

.. code-block:: bash

    $ ivpm install -o /opt/eda \
        --from https://edapack.github.io -d digital-sim -d digital-formal \
        --from https://mycorp.internal/tools -d common --as corp

Here ``digital-sim`` and ``digital-formal`` come from edapack, ``common``
comes from the corporate catalog, and neither selection leaks into the other.

A per-source option before the first ``--from`` is an error rather than a
silent promotion to global -- promoting it would apply one source's dep-set
selection to every source, which is the opposite of what was typed:

.. code-block:: text

    $ ivpm install -o /opt/eda -d digital-sim --from https://edapack.github.io
    fatal: -d applies to a single source and must follow a --from.
           Move it after the --from it belongs to.

``-o``/``--outdir`` is **required**. It is deliberately not spelled
``--deps-dir``: on ``update`` that flag names a subdirectory *relative to* the
project root, whereas here the value is a path that *becomes* the deps-dir.

Source aliases
--------------

Each source gets an alias, used to name it in diagnostics. Precedence:

1. ``--as NAME``
2. the manifest's ``package.name``
3. a slug of the URL (``https://edapack.github.io`` → ``edapack-github-io``)

Two sources resolving to the same alias is an error, raised before anything is
fetched -- ambiguous aliases would make every collision message below
unactionable.

Collisions
----------

Two sources providing the same package name with different definitions is a
real ambiguity. IVPM stops:

.. code-block:: text

    error: package 'verilator' is provided by two sources with different definitions

      edapack    git url=https://github.com/verilator/verilator version=5.020
      corp       git url=https://git.mycorp.internal/verilator version=5.028

      Resolve this specific package:   --resolve verilator=corp
      Or set a policy for all of them: --on-collision=last-wins

Two sources declaring the *same* package identically are not a collision: the
package is installed once, silently.

There are two escapes, and the message always prints both.

``--resolve <package>=<source>``
  Surgical, and the one to prefer. Names the source whose definition wins for
  that package. The winner's definition is taken **whole** -- there is no
  field-level merging, because a half-merged package definition is not
  something either catalog author ever tested. Repeatable.

  A ``--resolve`` for a package no source disagrees about is a **warning**
  (it is stale; drop it). A ``--resolve`` for a package no source provides at
  all is an **error** -- that is a typo, not a stale flag.

``--on-collision={error,first-wins,last-wins}``
  Blunt, and applies to every collision at once. ``first-wins`` and
  ``last-wins`` follow ``--from`` order. Both still emit a **warning per
  collision**: the policy says how to break a tie, not that ties are
  unremarkable.

Sources may also disagree about *configuration* rather than a package -- two
different ``with.node.manager`` values, say. Those are reported the same way,
but only ``--on-collision`` can resolve them; ``--resolve`` is keyed by package
name and has nothing to say about a config key. The error message says so.

Naming the tool directory
-------------------------

``packages.envrc`` exports the tool directory's path as ``IVPM_PACKAGES``. In
a shared toolchain that name reads oddly -- the directory is a tool install,
not a project's packages -- and downstream scripts often already expect a
site-specific name. ``--root-var`` supplies one:

.. code-block:: bash

    $ ivpm install -o /opt/eda --root-var TOOLS_ROOT --from https://edapack.github.io

.. code-block:: bash

    # /opt/eda/packages.envrc
    export TOOLS_ROOT=/opt/eda
    export IVPM_PACKAGES=${TOOLS_ROOT}
    source_env ./verilator/export.envrc

``IVPM_PACKAGES`` is still exported, as an alias of the new name, so catalog
manifests that reference ``${IVPM_PACKAGES}`` keep resolving to the same path.
The two names are always the same directory; ``--root-var`` chooses what to
call it, it does not relocate anything.

The name must be a shell variable name (``[A-Za-z_][A-Za-z0-9_]*``).
``IVPM_PROJECT`` is rejected: it means "the project root", which a tool
directory does not have.

The name is recorded in the lock, so a replay reproduces it -- and so does a
re-run that supplies ``--from`` without ``--root-var``, since consumers
reference the name and silently dropping it would break them. To go back to
the bare default, say so explicitly:

.. code-block:: bash

    $ ivpm install -o /opt/eda --root-var IVPM_PACKAGES

``--root-var`` is a global option: it describes the directory, not any one
source, so it belongs before the first ``--from``.

Ordering
--------

``--from`` order is significant. It determines the order in which packages and
``env:`` directives are emitted into ``packages.envrc``, and therefore
``PATH`` precedence: **the first source wins**. The order is recorded in the
lock, so a replay reproduces it exactly.

Limitations
-----------

**One shared virtual environment.** All sources contribute Python requirements
to a single venv in ``<outdir>/python``. IVPM does not attempt to reconcile
version constraints between them -- that is pip's or uv's job, and a conflict
surfaces as a resolver error from that tool rather than as an IVPM collision.
If two catalogs need incompatible Python dependencies, they need separate tool
directories.

**No ``IVPM_PROJECT``.** A tool directory has no project root, so
``packages.envrc`` does not export ``IVPM_PROJECT`` and a manifest that
references ``${IVPM_PROJECT}`` in an ``env:`` directive is referring to
something that does not exist. By default this is an error:

.. code-block:: text

    error: env setting 'ACME_CFG' references ${IVPM_PROJECT}, which has no
    value in a tool directory (there is no project root).
      Substitute the tool directory: --on-project-ref=expand
      Or drop the setting entirely:  --on-project-ref=drop

Both escapes warn. ``expand`` substitutes the outdir, which is *not* what the
manifest meant but is often close enough; ``drop`` removes the setting.

``${IVPM_PACKAGES}`` is unaffected and stays correct -- it is the outdir.

**Project-scoped handlers do not run.** See :doc:`handlers`. The skip is always
announced.

Refreshing
----------

Re-running with no ``--from`` replays the install recorded in the outdir's
``package-lock.json`` -- same sources, same dep-sets, same collision
resolutions, same order:

.. code-block:: bash

    $ ivpm install -o /opt/eda

Supplying ``--from`` **replaces** the recorded spec rather than adding to it,
after printing what changed. A tool tree that silently accumulates sources
across invocations is worse than one that makes you say so:

.. code-block:: text

    note: Replacing the recorded install spec:
      + added    https://mycorp.internal/tools (common)
      ~ dep-sets https://edapack.github.io: digital-sim -> digital-sim, digital-formal

Inspecting and removing
-----------------------

``ivpm status`` and ``ivpm sync`` work inside a tool directory: they recognize
that the directory is itself a deps-dir.

.. code-block:: bash

    $ cd /opt/eda && ivpm status
    $ ivpm status -p /opt/eda        # equivalently

``ivpm destroy --deps-only -p /opt/eda`` removes the tools and the lock/state
but keeps the directory. Plain ``ivpm destroy /opt/eda`` removes the whole
tree. See :doc:`destroy`.

.. warning::

   A collision that appears for the first time *during a replay* -- because an
   upstream catalog moved -- is a hard error, and will fail an unattended
   refresh. That is deliberate: silently changing which source provides a tool
   is worse than a failed cron job. Pin the outcome with ``--resolve``, which
   is recorded in the lock and replayed with everything else.
