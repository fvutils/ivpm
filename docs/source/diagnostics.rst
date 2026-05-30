###########
Diagnostics
###########

When IVPM reads your ``ivpm.yaml`` it reports problems the way a compiler does:
with the exact source location, a short message, and (for errors) an excerpt of
the offending line.  This page explains how to read those messages and how the
diagnostic system behaves.

Anatomy of a message
====================

A diagnostic looks like this::

    ivpm.yaml:8:7: error: dependency 'fusesoc' has no recognizable source type
          url: http://github.com/olofk/fusesoc.git
          ^

* ``ivpm.yaml:8:7`` -- the **file**, **line**, and **column** (all 1-based).
  Most terminals and editors let you click or jump to this location.
* ``error`` -- the **severity** (see below).
* the **message** describing the problem.
* for errors, an **excerpt** of the source line with a caret (``^``) under the
  exact column.

Some diagnostics add follow-up ``note:`` lines that point at related locations
(for example, "previously declared here").

Severity levels
===============

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Severity
     - Meaning
   * - ``note``
     - Informational; no action required.
   * - ``warning``
     - Something looks off but processing continues.
   * - ``error``
     - A real problem.  IVPM reports it and (at the end of the affected phase)
       stops with a non-zero exit code.
   * - ``fatal``
     - An unrecoverable problem.  IVPM reports it and stops immediately.

Warnings and errors are **always shown**, regardless of the ``--log-level``
setting (see below).  When IVPM exits because of an error or fatal diagnostic it
returns a non-zero exit status and prints **no Python traceback** -- the
diagnostic is the message.

``--log-level`` versus diagnostics
==================================

These are two different channels:

* **Diagnostics** (notes, warnings, errors, fatals about *your project*) are
  always written to standard error.
* **Logging** (``--log-level INFO|DEBUG|WARN|NONE``, default ``NONE``) controls
  IVPM's internal *developer* tracing only.  Raising the log level adds
  diagnostic detail about IVPM's own execution; it does **not** turn
  project diagnostics on or off -- those are always visible.

Rich versus plain output
========================

When standard output is a terminal, IVPM renders diagnostics in colour and, during
``ivpm update`` / ``ivpm sync``, prints them above the live progress display so
they do not corrupt it.  Pass ``--no-rich`` (or redirect output to a file / pipe)
to get plain, uncoloured text suitable for logs and CI.

Examples
========

Unknown key at the package level::

    ivpm.yaml:3:3: fatal: Unknown tag 'bogus_key' at package level in ivpm.yaml.
     Valid tags: default-dep-set, dep-sets, deps, deps-dir, dev-deps, env,
     env-sets, name, paths, setup-deps, type, vars, version, with
      bogus_key: 1
      ^

Missing required ``name`` on a dependency::

    ivpm.yaml:6:9: fatal: Missing 'name' key in dependency
            - src: pypi
            ^

A dependency with neither ``src`` nor ``url``::

    ivpm.yaml:6:11: fatal: no src specified for package orphan and no URL specified
            - name: orphan
              ^

A YAML syntax error is reported the same way, pointing at where the parser got
stuck::

    ivpm.yaml:2:9: error: while parsing a flow node, expected the node content
      name: [unclosed
            ^

For extension authors
=====================

If you write a custom handler or content type, emit located diagnostics through
:mod:`ivpm.msg`.  The functions accept an optional *location* argument, which may
be a parsed YAML value (every mapping, sequence, and ``str``/``int``/``float``
scalar carries a ``.srcinfo`` attribute) or a :class:`ivpm.yamlsrc.SrcInfo`::

    from ivpm.msg import warning, error, fatal

    def create_data(self, with_opts, si):
        for key in with_opts:
            if key not in known:
                # point at the offending value's location
                fatal("Unknown parameter '%s' for type 'mytype'" % key,
                      with_opts.get(key))

Semantics:

* ``warning(msg, loc=None)`` / ``error(msg, loc=None)`` -- render and record;
  ``error`` does **not** raise, so several problems can be reported in one pass.
* ``fatal(msg, loc=None)`` -- render and raise ``SrcLoaderError`` (caught at the
  CLI boundary for a clean, traceback-free exit).
* When *loc* is omitted, the message is printed without a location prefix, so
  existing single-argument calls keep working.

