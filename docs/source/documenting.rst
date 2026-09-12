########################
Documenting a Project
########################

A manifest records what a workspace *is made of*.  The keys on this page let it
also record *why* — why a dependency is there, why it is pinned where it is,
what a dep-set is for, and what an environment variable contracts with.

Everything here is **inert**.  No key on this page influences resolution,
fetching, caching, or the contents of ``package-lock.json``; adding prose to a
manifest cannot change what lands on disk.

.. contents:: On this page
   :local:
   :depth: 2

``description`` and ``doc``
===========================

Two keys, at three levels.

``description``
    A one-line summary.  It is what fills a table cell — in ``ivpm show deps``,
    in ``ivpm show bom``, in a generated catalog page.  Keep it short and write
    it as a noun phrase: *"UVM 1.2 base class library"*, not *"This package
    contains..."*.

``doc``
    A long-form body, normally a YAML block scalar.  This is where the
    reasoning goes: why this commit and not that tag, what the patches fix,
    which issue tracks the pin.

Both are available on the **package**, on each **dep-set**, and on each
**dependency**:

.. code-block:: yaml

    package:
      name: acme-flow
      description: Acme's RTL-to-GDS flow environment.
      doc: |
        Everything the flow needs at a fixed, reproducible revision. Start from
        the ``sim`` dep-set for day-to-day work; ``ci`` additionally pins the
        checkers.

      dep-sets:
        - name: sim
          description: Simulator plus the UVM base class library.
          doc: |
            The everyday development set. Adds nothing that requires a license
            server, so it works on a laptop.
          deps:
            - name: uvm
              description: UVM 1.2 base class library.
              url: https://github.com/accellera/uvm.git
              tag: UVM_1_2

            - name: somelib
              description: Vendor DPI shim.
              doc: |
                Pinned to a commit rather than a tag because upstream retags.
                The two patches below restore ``--std=c++17`` compatibility;
                see ISSUE-4412.
              url: https://github.com/foo/somelib.git
              commit: a1b2c3d
              patches:
                - patches/cxx17-shim.patch

``doc`` is opaque
-----------------

IVPM stores ``doc`` **verbatim** and never interprets it.  The markup dialect —
reStructuredText, MyST, Markdown, plain prose — is chosen by whatever renders
it, not by IVPM.  Nothing is validated, normalised, or reflowed, so a body
containing ``:ref:`` roles, raw HTML or ``${{...}}``-looking text survives
unchanged.

Because a block scalar preserves its trailing newline and internal blank lines,
what you write is exactly what a renderer receives.

Project metadata
================

Four optional keys at the ``package:`` level describe the project itself:

.. code-block:: yaml

    package:
      name: acme-flow
      license: Apache-2.0
      homepage: https://acme.example/flow
      documentation: https://docs.acme.example/flow
      maintainers:
        - Alice Chen <alice@acme.example>
        - Bob Diaz <bob@acme.example>

``license``, ``homepage`` and ``documentation`` are free-form strings;
``maintainers`` is a list of strings.  They appear in ``ivpm show bom`` and in
``ivpm show --schema``.

``documentation`` is cross-project doc-linking
-----------------------------------------------

``documentation`` does a second job beyond filling a BOM column.  A dependency
that is itself an IVPM project declares where its docs live **once, in its own
manifest**; every consumer's generated documentation then links there with no
consumer-side configuration at all.  Point it at the rendered docs, not the
repository.

Upstream metadata is used as a fallback
----------------------------------------

For a package that also carries a ``pyproject.toml`` or a ``package.json``,
``ivpm show bom`` falls back to the metadata already declared there
(``project.license``, ``project.urls.Homepage``, ``project.urls.Documentation``;
``license``, ``homepage``) when the ``ivpm.yaml`` manifest is silent.

**The manifest always wins.**  Upstream metadata only fills a field the
manifest left unset — it can never override one it declares.

Documenting the environment
===========================

``with.env`` directives and ``paths`` sets are the workspace's contract with
everything that runs inside it, so both take a ``description``:

.. code-block:: yaml

    package:
      name: acme-flow
      with:
        env:
        - name: DESIGN_ROOT
          description: Root of the RTL tree; consumed by the filelist generator.
          value: ${{ ivpm_project_dir }}/rtl
      paths:
        systemverilog:
          description: Include directories published to downstream consumers.
          incdirs: [ rtl/include ]

On an ``env`` directive the ``description`` sits alongside the setting
(``value`` / ``path`` / ``path-append`` / ``path-prepend``) and never reaches
the generated ``packages.envrc``.  On a path-set it names the set as a whole and
is not treated as a path kind.

What is *not* recorded
======================

Documentation keys are deliberately **not** written into
``package-lock.json``.  The lock records what you *got* — resolved commits,
versions, the platform that resolved them — while prose is what you *declared*.
Keeping them apart means a doc-only edit produces no lock churn and no
re-fetch.  See :doc:`package_lock`.

Seeing it back
==============

* ``ivpm show deps <name>`` prints a dependency's ``description`` and ``doc``.
* ``ivpm show deps --json`` / ``--tree --json`` carry both, plus the dep-set's
  own prose and its inheritance delta.  See :doc:`show_deps`.
* ``ivpm show bom`` joins the prose with resolved identity, license and
  documentation links.  See :ref:`show-bom`.
* ``ivpm show --schema`` emits a JSON Schema that includes every key on this
  page, so an editor configured with ``$schema`` completes them as you type.

Rendering it elsewhere
======================

``sphinx-ivpm`` is a Sphinx extension that consumes these surfaces —
``ivpm show deps --json`` and ``ivpm show bom --json`` — to generate project
documentation: dep-sets as variants, the inheritance lattice, the bill of
materials, and the environment contract.  IVPM's job is to *expose* the data
faithfully; choosing a markup dialect and a page layout is the extension's.
