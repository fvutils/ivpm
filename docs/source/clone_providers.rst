################
Clone Providers
################

Overview
========

``ivpm clone`` creates a new workspace from a source locator.  By default the
locator is a Git URL, but the mechanism is **pluggable**: a *clone provider*
teaches ``ivpm clone`` how to obtain a workspace from some other kind of source
-- a different version-control system, an artifact server, a snapshot store, and
so on.

A provider is selected automatically from the locator.  Two mechanisms decide
which provider handles a given locator:

* **Dedicated URL scheme.** A provider may own one or more schemes such as
  ``myvcs://`` or ``snapshot://``.  A locator using an owned scheme always routes to
  that provider.
* **Claiming.** For generic locators (``https://host/path`` and the like) each
  provider is asked how strongly it *claims* the locator.  The strongest
  claimant wins.

After the provider materializes the working tree, ``ivpm clone`` runs
``ivpm update`` in the new workspace if it contains an ``ivpm.yaml`` -- exactly
as before.  Providers are only responsible for producing the tree.

``ivpm clone`` also records which provider produced the workspace in the lock
file's ``root`` block, so ``ivpm status`` can report the state of the *root
project* (not just its dependencies).  For a workspace it did not create,
``ivpm status`` falls back to **probing** the installed providers to recognize
the root on disk.  See :ref:`root-project-status` below and the ``status``
command in :doc:`reference`.

Discovering providers
======================

To see which providers are installed and what options each accepts::

    ivpm show clone-providers            # list all providers
    ivpm show clone-providers git        # options for a specific provider
    ivpm clone --help                    # common options + provider summary
    ivpm clone myvcs:// --help           # options for the 'myvcs' provider

Example listing:

.. code-block:: text

    $ ivpm show clone-providers
    Provider   Default  Schemes    Description
    git        yes       (by URL)   Clone a git/GitHub repository into a new workspace
    myvcs                myvcs://   Check out a myvcs repository

Selecting a provider
====================

Resolution precedence (highest first):

#. ``--provider NAME`` -- force a specific provider explicitly.
#. **Dedicated scheme** -- the locator's ``scheme://`` is owned by a provider.
#. **Strongest claim** -- among providers that claim the locator, the strongest
   wins.  The built-in git provider claims unmistakable git locators
   (``git@host:path``, ``ssh://``, ``git://``, ``*.git``) strongly and generic
   ``https://`` / local paths weakly, so a purpose-built provider that
   recognizes a URL outranks the git fallback.

It is an **error** when two providers claim a locator equally strongly; use a
dedicated scheme or ``--provider`` to disambiguate.  A dedicated scheme always
beats a claim.

Provider-specific options
=========================

A provider can accept its own command-line options.  These appear after the
source locator, e.g.::

    ivpm clone myvcs://repo -branch abc -node xyz

Here ``-branch`` and ``-node`` belong to the ``myvcs`` provider.  IVPM keeps the
provider's options separate from the common ``clone`` options, so a provider is
free to use its own naming conventions (including single-dash long options like
``-branch``) without colliding with IVPM's flags.

.. note::

   The git-specific flags ``--ssh``, ``--anonymous`` and ``--git-auth-order``
   belong to the **git** provider (see :doc:`git_integration`).  They remain
   accepted directly on ``ivpm clone`` for backward compatibility, but their
   documented home is ``ivpm clone --provider git --help`` /
   ``ivpm show clone-providers git``.  ``--branch`` is a *common* option (most
   version-control providers understand "check out this branch/ref").

.. _config-forwarding:

Forwarding configuration from a provider
========================================

A provider's source of truth often knows things the checked-out tree does not
-- default permissions, the deps-dir to use, or a whole configuration when the
tree carries no ``ivpm.yaml`` of its own.  A provider forwards this to the
post-clone ``ivpm update`` by returning a ``CloneRootConfig`` on its
``CloneResult`` (``CloneResult.root_config``).  This is a **core** mechanism,
available to any provider.

``CloneRootConfig`` has two fields:

``default_package``
    A synthesized ``package:`` mapping, used **only** when the cloned tree has
    no ``ivpm.yaml``.  It is fed through the normal yaml reader, so it reuses
    all dep-set / ``with:`` / deps-dir parsing with no new format.

``handler_overlay``
    Handler configuration merged **underneath** the effective
    ``handler_configs`` -- the workspace's own ``ivpm.yaml`` always wins on a
    conflict.  Applied whether or not the tree has an ``ivpm.yaml``.  The merge
    is generic: a handler the manifest does not configure is adopted wholesale;
    dicts merge recursively (local keys win); lists union order-stably (overlay
    values first); scalars keep the local value.

``ivpm clone`` also persists ``root_config`` into the lock's ``root.config``
block, so a bare workspace (see below) can reproduce its driving configuration
on a later ``ivpm update``.

.. _bare-workspaces:

Bare workspaces (no root ``ivpm.yaml``)
=======================================

Some sources produce a workspace that has **no root ``ivpm.yaml``**.  IVPM
still supports the read-only operations on such a *bare* workspace:

* ``ivpm status`` and ``ivpm sync`` operate off the lock file.  Because the
  deps-dir name is not known from a manifest, IVPM **discovers** it: it scans
  the immediate children of the workspace for a directory containing a valid
  ``package-lock.json`` (one carrying IVPM's lock version).  Conventional names
  (``import`` > ``packages`` > ``deps``) break a tie; a genuinely ambiguous tree
  (two unrelated valid locks) is reported rather than guessed.

* ``ivpm update`` works on a bare workspace **when the workspace is
  self-describing** -- i.e. the provider forwarded a configuration
  (``root_config`` on its ``CloneResult``) that ``ivpm clone`` persisted into the
  lock's ``root.config`` block.  On a subsequent ``ivpm update`` with no
  ``ivpm.yaml``, IVPM reads ``root.config`` back and reproduces the driving
  configuration (the synthesized ``default_package`` plus any ``handler_overlay``)
  -- so a config-forwarding provider's bare workspaces refresh normally.  A bare
  workspace that carries **no** ``root.config`` (a non-forwarding clone, or one
  created before this record existed) still cannot be updated and fails with a
  clear message directing you to re-clone.

.. _writing-a-clone-provider:

Writing a Clone Provider
========================

A clone provider is a class that extends
:class:`ivpm.clone.clone_provider.CloneProvider`.  Implement the following:

``provider_info()`` (classmethod)
    Return a ``CloneProviderInfo`` describing the provider -- its ``name``,
    ``description``, owned ``schemes`` and (optionally) ``notes``.  Used by
    ``ivpm show clone-providers``.

``schemes()``
    The dedicated URL schemes the provider owns, without ``://`` (e.g.
    ``["myvcs"]``).  A scheme match beats any claim.  Return ``[]`` if the
    provider has no dedicated scheme.

``claim(src)``
    Return how strongly the provider claims a generic locator, using the
    ``ClaimStrength`` constants ``NONE`` / ``WEAK`` / ``STRONG``.  Be
    conservative: return ``STRONG`` only for locators you positively recognize
    so purpose-built providers compose cleanly with the git fallback.

``options()``
    Declare the provider's CLI options *declaratively* as a list of
    ``CloneOption``.  This single declaration drives both the argument parser
    and the ``ivpm show`` / ``--help`` output, so documentation can never drift
    from behavior.

``default_workspace_name(src)``
    Optionally derive a default workspace directory name from the locator.
    Return ``None`` to accept IVPM's basename heuristic.

``clone(req)``
    Materialize the working tree at ``req.target_dir`` and return a
    ``CloneResult``.  ``req`` (a ``CloneRequest``) carries the parsed
    ``provider_args``, the common ``branch``, and an ``event_dispatcher`` for
    progress reporting.  Do **not** run ``ivpm update`` -- ``ivpm clone`` does
    that after ``clone()`` returns.

``probe(root_dir)`` *(optional)*
    Return how strongly the provider recognizes an *on-disk* working tree at
    ``root_dir``, using the ``ClaimStrength`` constants.  This is the
    directory-shaped analogue of ``claim()`` (which matches a *locator*
    string), used by ``ivpm status`` to describe the root project when the lock
    did not record which provider produced it.  Default: ``NONE`` (a provider
    that skips this simply never describes a root by probing).

``root_status(root_dir)`` *(optional)*
    Return a ``PkgVcsStatus`` (with ``is_root=True``) describing the VCS state
    of the root working tree, or ``None`` if it cannot be determined.  Must be
    **read-only and fast** -- local queries only, no network.  Default:
    ``None``.

Complete example
----------------

.. code-block:: python

    # src/mycompany/ivpm_myvcs.py
    import os
    from ivpm.clone.clone_provider import (
        CloneProvider, CloneOption, CloneResult, ClaimStrength,
    )
    from ivpm.show.info_types import CloneProviderInfo


    class MyVcsCloneProvider(CloneProvider):

        @classmethod
        def provider_info(cls):
            return CloneProviderInfo(
                name="myvcs",
                description="Check out a myvcs repository",
                schemes=["myvcs"],
                notes="Locator form: myvcs://<repo>",
            )

        def schemes(self):
            return ["myvcs"]

        def options(self):
            return [
                CloneOption(flags=["-branch"], dest="branch",
                            help="Branch to check out",
                            required=True, metavar="BRANCH"),
                CloneOption(flags=["-node"], dest="node",
                            help="Build node to attach", default="head",
                            metavar="NODE"),
            ]

        def default_workspace_name(self, src):
            # myvcs://my.repo -> my.repo
            return src.split("://", 1)[-1] or None

        def clone(self, req):
            repo = req.src.split("://", 1)[-1]
            branch = req.provider_args.branch
            node = req.provider_args.node
            os.makedirs(req.target_dir, exist_ok=True)
            # ... run the myvcs checkout into req.target_dir ...
            return CloneResult(ok=True, resolved_revision=node)

        def probe(self, root_dir):
            # Recognize a myvcs checkout by its marker directory.
            if os.path.isdir(os.path.join(root_dir, ".myvcs")):
                return ClaimStrength.STRONG
            return ClaimStrength.NONE

        def root_status(self, root_dir):
            from ivpm.pkg_status import PkgVcsStatus
            # ... query the myvcs checkout for its branch/node/dirty state ...
            return PkgVcsStatus(
                name="(root)", src_type="myvcs", path=root_dir, vcs="myvcs",
                branch="main", commit="node-42", is_root=True)

Registering via entry points
----------------------------

IVPM discovers providers through the ``ivpm.clone_providers`` entry-point
group.  Add to your ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."ivpm.clone_providers"]
    myvcs = "mycompany.ivpm_myvcs:MyVcsCloneProvider"

Each value points to a **class** extending ``CloneProvider``.  After installing
your package (``pip install -e .``), run ``ivpm show clone-providers`` to
confirm IVPM discovered it.

Reporting progress
------------------

Emit ``UpdateEvent`` objects through ``req.event_dispatcher`` so your provider
integrates with the ``ivpm clone`` progress display, mirroring the built-in git
provider.  See :class:`ivpm.update_event.UpdateEvent`.

.. _root-project-status:

Reporting root status
---------------------

``ivpm status`` shows a header for the *root* project, described by a clone
provider.  Two optional hooks make your provider participate:

* ``probe(root_dir)`` recognizes your checkout **on disk** (the directory
  analogue of ``claim()``), and ``root_status(root_dir)`` returns its state as
  a ``PkgVcsStatus`` with ``is_root=True``.

How the provider is selected:

* If the workspace was created by ``ivpm clone``, the provider name recorded in
  the lock file's ``root`` block is used directly -- the reliable fast path.
* Otherwise IVPM **probes** every installed provider and takes the strongest
  ``probe()`` tier.  As with ``claim()``, be conservative: return ``STRONG``
  only for an unmistakable on-disk signature (the git provider keys on a real
  ``.git``).  A **tie** at the top tier omits the root line rather than erroring
  (status is informational), so recording the type at clone time -- which then
  wins outright -- is the dependable path.

Returning ``None`` from ``root_status()`` (or declining in ``probe()``) cleanly
omits the root header; it is never an error for the root type to be unknown.

Testing your provider
---------------------

* Assert your ``claim()`` classification for representative locators.
* Assert the option table matches the parser -- build the parser via
  ``build_arg_parser()`` and confirm it accepts your declared options.
* Drive ``clone()`` into a temporary directory and confirm the tree is
  produced (and, if applicable, that an ``ivpm.yaml`` triggers the post-clone
  update).
* Assert ``probe()`` classifies a representative checkout, and drive
  ``root_status()`` on a temporary tree, checking the reported branch/dirty
  state.

.. note::

   A clone provider (how the *root* workspace is obtained by ``ivpm clone``) is
   distinct from a **package source** (how *dependencies* are fetched during
   ``ivpm update``; see :doc:`package_types`).  A provider may reuse the same
   low-level VCS code as its package-source sibling, but the two extension
   points serve different roles.

See Also
========

- :doc:`git_integration` -- the built-in git provider's authentication/transport
- :doc:`package_types` -- package *sources* (the ``ivpm.sources`` sibling)
- :doc:`extending_ivpm` -- other IVPM extension points (handlers, site config)
