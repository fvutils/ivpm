######################
Nested Dependencies
######################

By default IVPM **flattens** every dependency: the whole transitive closure
lands in one ``packages/`` directory, deduplicated by name, and the first
resolver of a name wins.  That is the right default -- one copy of each
library, one obvious place to look.

Sometimes it is the wrong answer.  When two dependencies genuinely require
*different, irreconcilable versions* of the same package, flattening forces one
of them to run against a version it was never tested with.  ``deps-mode:
nested`` is the escape hatch: a package marked nested resolves its own
dependencies into its own deps-dir, independently of the rest of the workspace.

.. important::

   A nested scope is **not** a sub-workspace.  There is still exactly one root
   project, one ``package-lock.json``, one virtual environment, and one
   ``packages.envrc``; handlers process every package in the tree, nested ones
   included.  Nesting changes *where dependencies are placed and how names are
   deduplicated* -- nothing else.

Declaring a nested scope
========================

``deps-mode`` accepts ``flatten`` (the default) or ``nested``.
``hierarchical`` is accepted as a spelling of ``nested``.

It may be declared in three places:

**On a package** -- applies to everything that package pulls in:

.. code-block:: yaml

    package:
      name: toolB
      deps-dir: packages       # the deps-dir the nested scope uses
      deps-mode: nested

**On a dep-set** -- applies to the dependencies of that dep-set's packages:

.. code-block:: yaml

    package:
      name: my-project
      dep-sets:
        - name: default-dev
          deps-mode: nested
          deps:
            - name: toolB
              url: https://github.com/org/toolB.git

**On a single dependency entry** -- the consumer's decision about one package:

.. code-block:: yaml

    deps:
      - name: toolB
        url: https://github.com/org/toolB.git
        deps-mode: nested

Precedence
----------

For each package, the mode governing *its* dependencies is the first of:

1. the ``deps-mode`` on the **dependency entry** that pulled it in (the
   consumer always wins -- you can always override a package's own opinion);
2. the ``deps-mode`` in the package's **own manifest** (dep-set level first,
   then package level);
3. the mode **inherited** from the enclosing scope.

Not declaring ``deps-mode`` is different from declaring ``flatten``.  An
undeclared package inherits; an explicit ``flatten`` *stops* propagation, so
you can flatten one sub-tree inside an otherwise-nested one.

Propagation
-----------

``nested`` is sticky: it propagates down the tree until something explicitly
declares ``flatten``.  A package that says nothing at all, resolved inside a
nested scope, becomes a nested scope itself.

What it looks like on disk
==========================

Given a root project that depends on ``libA`` (v2) and ``toolB``, where
``toolB`` is nested and needs ``libA`` v1 plus ``libC``:

.. code-block:: text

    packages/
      libA/                     -> v2       (symlink to the cache, as usual)
      toolB/                                (a real directory -- see below)
        ivpm.yaml
        packages/
          libA/                 -> v1       (symlink to the cache)
          libC/
            ivpm.yaml
            packages/
              libD/             -> symlink to the cache

Both versions of ``libA`` coexist.  ``libC`` declared nothing, so it inherited
``nested`` and opened a scope of its own for ``libD``.

Caching
=======

A cache entry's identity is a function of its source and version -- never of
where it is placed -- so nesting adds no cache entries.  One package at one
version is one entry, whether it is used flat, nested, or both.

A cached package is normally materialized as a read-only symlink into the
shared cache.  A package acting as a nested **boundary** has to own its
directory so it can hold a deps-dir, so IVPM replaces the symlink with a
writable copy of the cache entry.  The cache entry itself is untouched and
still shared.  See :doc:`caching`.

The lock file
=============

``package-lock.json`` version 2 keys its ``packages`` map by *scope path*
rather than by bare package name, which is what lets it record two versions of
one package:

.. code-block:: text

    "packages": {
      "libA":                     { "name": "libA", ... },
      "toolB":                    { "name": "toolB", "deps_mode": "nested", ... },
      "toolB/packages/libA":      { "name": "libA", "scope": "toolB/packages", ... }
    }

In a flat workspace a scope path *is* the bare name, so a v2 lock for a flat
project is shape-identical to v1.  Version 1 lock files are still read.  See
:doc:`package_lock`.

Caveats
=======

Nesting is not Python isolation
-------------------------------

A workspace has **one** virtual environment, and one venv cannot hold two
versions of the same distribution.  If two scopes contribute the same Python
distribution, IVPM warns, names both scopes, and installs the one nearest the
root -- the other will not be importable.  Nesting isolates the *dependency
tree*, not ``sys.path``.  See :doc:`python_packages`.

Live-linked directory packages cannot be nested
-----------------------------------------------

A ``src: dir`` dependency with the default ``link: true`` is a symlink to your
own working copy.  IVPM refuses to nest it rather than either detaching your
edits (by copying) or writing a deps-dir into your source tree.  Set
``link: false`` on the dependency, or resolve it with ``deps-mode: flatten``.

Disk usage
----------

Two versions of a package means two copies on disk, and a cached boundary is a
copy rather than a symlink.  This is the cost of the escape hatch; it is why
``flatten`` remains the default.

Cycles and depth
================

Flattening quietly absorbs dependency cycles -- name deduplication stops the
descent.  Nesting makes them reachable, so IVPM guards explicitly.

When a package is about to be resolved into a scope whose ancestors already
provide *the same package at the same resolved version*, IVPM stops descending
and reports:

.. code-block:: text

    note: Dependency cycle elided at 'toolB/packages/libA': it is already
    provided by the enclosing scope '<root>'. Its dependencies were not
    resolved again.

The package is still materialized; only its sub-tree is skipped, because that
sub-tree already exists further up.  ``ivpm show deps`` marks such nodes.

Because repeating an identity terminates a chain, unbounded descent would
require unboundedly many *distinct* versions.  ``IVPM_MAX_DEP_DEPTH``
(default 32) is a backstop against that, and raises an error naming the chain
rather than eliding.  See :doc:`troubleshooting`.
