########################
Writing Custom Handlers
########################

IVPM is designed to be extended with custom **package handlers**. A handler is a
Python class that observes packages as they are loaded and performs actions -- such
as setting up a virtual environment, writing IDE integration files, or invoking a
downstream tool. IVPM discovers handlers through Python `entry points`_, so any
installed package can contribute new handlers without modifying IVPM itself.

For an overview of what handlers are, how they fit into the update pipeline,
and documentation of the three built-in handlers (Python, Direnv, Agents),
see :doc:`handlers`.

.. _entry points: https://packaging.python.org/en/latest/specifications/entry-points/


Overview
========

IVPM handlers participate in two phases of every ``update``/``clone`` run:

**Leaf phase**
    Called once per package, on a worker thread, as each package is fetched and
    made available on disk. Leaf callbacks run concurrently -- one per fetched
    package -- so they are well-suited to lightweight per-package detection tasks.

**Root phase**
    Called once per run, on the main thread, after *all* packages have been
    fetched. Root callbacks see the full package list and are used for heavier
    work such as creating virtual environments or generating toolchain files.

Both phases are optional -- a handler may implement only the one(s) it needs.


The ``PackageHandler`` Base Class
==================================

All handlers extend ``ivpm.handlers.PackageHandler``:

.. code-block:: python

    import dataclasses as dc
    from typing import ClassVar, List, Optional
    from ivpm.handlers import PackageHandler, HandlerFatalError, HandlerPhase, ALWAYS, HasType

    @dc.dataclass
    class MyHandler(PackageHandler):

        # --- Metadata (class-level, not instance attributes) ---
        name:        ClassVar[str]  = "my-handler"
        description: ClassVar[str]  = "Does something useful"

        # --- Root-phase ordering (see "Handler Ordering" below) ---
        phase:       ClassVar[str]       = HandlerPhase.INTEGRATE  # named phase
        run_after:   ClassVar[List[str]] = []   # handler names / "phase:<name>" to run after
        run_before:  ClassVar[List[str]] = []   # handler names / "phase:<name>" to run before

        # --- When to activate (see Conditions section below) ---
        leaf_when:   ClassVar[Optional[List]] = None   # None = always run as leaf
        root_when:   ClassVar[Optional[List]] = None   # None = always run as root

        # --- Per-run state (cleared by reset()) ---
        _found_pkgs: list = dc.field(default_factory=list, init=False, repr=False)

        def reset(self):
            """Called automatically at the start of each run."""
            self._found_pkgs = []

        # --- Leaf callback ---
        def on_leaf_post_load(self, pkg, update_info):
            if (pkg.path / "my-marker.txt").exists():
                with self._lock:
                    self._found_pkgs.append(pkg)

        # --- Root callback ---
        def on_root_post_load(self, update_info):
            for pkg in self._found_pkgs:
                print(f"Processing {pkg.name}")


Class-level Metadata
---------------------

``name``
    Short identifier for the handler, used in log messages and entry-point
    registration. Required.

``description``
    Human-readable description shown in verbose output.

``phase``
    The named **phase** this handler's root work belongs to -- one of the
    ``HandlerPhase`` values: ``PREPARE``, ``ENVIRONMENT``, ``INSTALL``,
    ``INTEGRATE``, ``FINALIZE`` (run in that order). Default:
    ``HandlerPhase.INTEGRATE``. Phases are **barriers**: every handler in one
    phase completes before any handler in the next begins. A legacy integer is
    still accepted and mapped onto a named phase, but new handlers should use a
    ``HandlerPhase`` value. Leaf phase ordering is determined by package fetch
    order, not by this value. See `Handler Ordering`_.

``run_after``
    A list of ordering constraints that must run **before** this handler. Each
    entry is either a handler name (e.g. ``"python"``) or a phase reference
    (e.g. ``"phase:install"``, meaning "after all INSTALL handlers"). A target
    naming a handler that is not installed is ignored with a warning, so it is
    safe to reference optional handlers. Default: ``[]``.

``run_before``
    Like ``run_after``, but these targets must run **after** this handler.
    Accepts handler names and ``"phase:<name>"`` references (e.g.
    ``"phase:integrate"``, meaning "before any INTEGRATE handler"). Default:
    ``[]``.

``leaf_when``
    A list of **leaf conditions** (see below), or ``None`` to always run as a
    leaf handler. Use ``[]`` (empty list) to opt out of leaf dispatch entirely.

``root_when``
    A list of **root conditions** (see below), or ``None`` to always run as a
    root handler. Use ``[]`` to opt out of root dispatch entirely.

``toolchain_support``
    Whether this handler's root phase may run when the deps-dir is the root --
    a :doc:`shared tool directory <tool_directories>` built by ``ivpm
    install``. ``ToolchainSupport.SUPPORTED`` (the default) or
    ``ToolchainSupport.UNSUPPORTED``. See `Working Without a Project Root`_.


Callbacks
----------

``reset()``
    Clear per-run accumulated state. Called automatically by
    ``on_root_pre_load()`` at the start of every run. Override this to reset
    any lists or counters that accumulate across leaf callbacks.

``on_leaf_pre_load(pkg, update_info)``
    Called before a package is fetched. Rarely needed; ``on_leaf_post_load`` is
    usually the right choice.

``on_leaf_post_load(pkg, update_info)``
    Called after a package is ready on disk. The package directory exists and can
    be inspected. Runs concurrently -- always use ``with self._lock:`` when writing
    to shared handler state.

``on_root_pre_load(update_info)``
    Called before any packages start loading. Calls ``reset()`` automatically.
    Override this only if you need additional setup before leaf callbacks begin.

``on_root_post_load(update_info)``
    Called after all packages have been fetched. Runs on the main thread. This is
    where long-running work (venv creation, codegen, etc.) belongs.

``on_destroy(remove_info)``
    Called by ``ivpm destroy`` to tear down derived artifacts this handler
    created (a venv, ``node_modules``, generated activation files) -- the inverse
    of ``on_root_post_load()``. ``remove_info`` carries ``deps_dir`` and
    ``dry_run``; honor ``dry_run`` (report, change nothing). Return the list of
    removed paths, or ``None``. Default: no-op, so a handler that creates no
    removable artifact needs no override.

``get_lock_entries(deps_dir) -> dict``
    Return extra top-level keys to merge into the project's lock file. Called
    after ``on_root_post_load()``. Default returns ``{}``.

``build(build_info)``
    Called by ``ivpm build``. Override to perform package build steps.

``add_options(subcommands)``
    Register handler-specific CLI flags. ``subcommands`` is a ``dict`` mapping
    subcommand name -> argparse subparser. Called during CLI parser setup.


Conditions
==========

Conditions control when a handler is active. They are plain callables stored in
``leaf_when`` / ``root_when`` class variables. IVPM provides three built-in
conditions:

.. code-block:: python

    from ivpm.handlers import ALWAYS, HasType, HasSourceType

``ALWAYS``
    Sentinel condition that always returns ``True``. Useful as an explicit
    marker that a handler is intentionally unconditional.

``HasType(type_name)``
    **Root condition.** Returns ``True`` if any loaded package has the given
    type, determined by either:

    * ``pkg.pkg_type`` -- set dynamically by a leaf handler
    * ``pkg.type_data`` -- set from the ``type:`` field in ``ivpm.yaml``

    Example -- only run the root phase when at least one Python package was
    detected:

    .. code-block:: python

        root_when = [HasType("python")]

``HasSourceType(src_type)``
    **Dual-mode condition.** When used in ``leaf_when``, receives a single
    package and returns ``True`` if its source type matches. When used in
    ``root_when``, receives the full package list and returns ``True`` if any
    package matches.

    Example -- only inspect git-sourced packages:

    .. code-block:: python

        leaf_when = [HasSourceType("git")]

You may also write your own conditions as any callable:

.. code-block:: python

    def has_cmake(pkg):
        """True if the package contains a CMakeLists.txt."""
        return (pkg.path / "CMakeLists.txt").exists()

    class MyCMakeHandler(PackageHandler):
        leaf_when = [has_cmake]
        root_when = [HasType("cmake")]


All conditions in a list are **AND'd** -- all must be ``True`` for the handler to
be active.


Thread Safety
=============

Leaf callbacks run concurrently. The base class provides ``self._lock``
(a ``threading.Lock``) for synchronising writes to accumulated state:

.. code-block:: python

    def on_leaf_post_load(self, pkg, update_info):
        if self._is_relevant(pkg):
            with self._lock:          # required when writing shared state
                self._found_pkgs.append(pkg)

Read-only access inside a single leaf callback does not require the lock.


Progress Reporting
==================

Handlers can report progress to the TUI using ``task_context()``:

.. code-block:: python

    def on_root_post_load(self, update_info):
        steps = list(self._found_pkgs)
        with self.task_context(update_info, "my-handler-setup", "Setting up MyTool") as task:
            for i, pkg in enumerate(steps):
                task.progress(f"Processing {pkg.name}", step=i + 1, total=len(steps))
                self._process(pkg)

``task_context(info, task_id, task_name)``
    Context manager that emits ``HANDLER_TASK_START`` on entry,
    ``HANDLER_TASK_END`` on clean exit, and ``HANDLER_TASK_ERROR`` on exception
    (then re-raises). Returns a ``TaskHandle``.

``task.progress(message, step=None, total=None)``
    Emit a ``HANDLER_TASK_PROGRESS`` event. The TUI displays the most recent
    message and, when ``step``/``total`` are provided, a fraction like ``2/5``.

``task.task_context(task_id, task_name)``
    Create a **nested** child task displayed under the parent in the TUI.

If no TUI is active (e.g. in non-interactive mode), ``task_context()`` and
``task.progress()`` are no-ops -- it is always safe to call them.

Fatal Errors
============

To abort an entire update run, raise ``HandlerFatalError``.  It is valid from
both leaf and root callbacks:

.. code-block:: python

    from ivpm.handlers import HandlerFatalError

    def on_leaf_post_load(self, pkg, update_info):
        if not self._check(pkg):
            raise HandlerFatalError(f"Required file missing in {pkg.name}")

IVPM treats it as a report of an *expected* failure, so the user sees the
message and nothing else -- never a traceback.  How the message reaches them
depends on where the raise happened:

* **Inside a** ``task_context()`` -- the task's error event carries the message
  and the TUI has already rendered it.  IVPM marks the exception ``reported``
  and stays quiet, so nothing is printed twice.
* **Outside one** -- e.g. a pre-flight check for a required tool on ``PATH``,
  which runs before any task opens.  Nothing has been shown yet, so IVPM
  prints the message itself.

Either way the exit status is 1.  Run with ``ivpm --log-level DEBUG`` to see
the originating traceback.

Any other exception, from a leaf or a root callback, is treated as a bug in
the handler and ends the run.  The user sees one line naming the handler, the
callback (and, for a leaf callback, the package) and the file the stack trace
was written to -- not the traceback itself -- and ``ivpm`` exits with status
70, so a script can tell an internal error from a user error (status 1).


Registering a Handler via Entry Points
=======================================

IVPM discovers handlers through the ``ivpm.handlers`` entry-point group.
Add the following to your ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."ivpm.handlers"]
    my-handler = "mypkg.my_handler:MyHandler"

Or, if you use ``setup.cfg``:

.. code-block:: ini

    [options.entry_points]
    ivpm.handlers =
        my-handler = mypkg.my_handler:MyHandler

Each value must point to a **class** that extends ``PackageHandler``.
IVPM instantiates the class once per update run.

After installing your package (``pip install -e .``), run
``ivpm show handler`` to confirm that IVPM discovered your handler correctly.
IVPM will also automatically load ``MyHandler`` on every ``update`` or
``clone`` run.


Complete Example
================

The following example shows a handler that detects packages containing
FuseSoC ``.core`` files and writes a consolidated library list:

.. code-block:: python

    # src/myext/fusesoc_handler.py
    import dataclasses as dc
    import pathlib
    from typing import ClassVar, List, Optional

    from ivpm.handlers import PackageHandler, HandlerPhase, HasType

    @dc.dataclass
    class FuseSocHandler(PackageHandler):

        name:        ClassVar[str]  = "fusesoc"
        description: ClassVar[str]  = "Collect FuseSoC core libraries"
        phase:       ClassVar[str]  = HandlerPhase.INTEGRATE

        # Activate root phase only when FuseSoC packages were detected
        root_when:   ClassVar[Optional[List]] = [HasType("fusesoc")]

        _lib_paths: list = dc.field(default_factory=list, init=False, repr=False)

        def reset(self):
            self._lib_paths = []

        def on_leaf_post_load(self, pkg, update_info):
            cores = list(pathlib.Path(pkg.path).rglob("*.core"))
            if cores:
                pkg.pkg_type = "fusesoc"   # marks package for HasType("fusesoc")
                with self._lock:
                    self._lib_paths.append(str(pkg.path))

        def on_root_post_load(self, update_info):
            # Never derive the project root from the deps-dir by hand: in a
            # tool directory the deps-dir IS the root, and '..' would escape it.
            project_root = update_info.project_root_or_none()
            if project_root is None:
                return          # no project: nothing to write a fusesoc.conf into
            out = pathlib.Path(project_root) / "fusesoc.conf"
            with self.task_context(update_info, "fusesoc-write", "Writing FuseSoC config") as task:
                task.progress(f"Writing {len(self._lib_paths)} library paths")
                with open(out, "w") as f:
                    for p in self._lib_paths:
                        f.write(f"[cores]\nlocation = {p}\n\n")

Register it:

.. code-block:: toml

    [project.entry-points."ivpm.handlers"]
    fusesoc = "myext.fusesoc_handler:FuseSocHandler"


Working Without a Project Root
==============================

Most workspaces have a project directory with the deps-dir beneath it. A
:doc:`shared tool directory <tool_directories>` does not: there, the deps-dir
**is** the root. A handler that needs the project root must ask for it rather
than compute it, and there are two accessors on ``update_info`` because there
are two honest answers to "what do I do without one".

``update_info.project_root_or_none()``
    Returns the project root, or ``None`` in toolchain mode. Use this when the
    handler has something sensible to do either way -- typically writing its
    deps-dir output unconditionally and skipping only the project-scoped part.

``update_info.project_root()``
    Returns the project root, or **raises** ``ToolchainModeError`` in toolchain
    mode. Use this when the handler genuinely cannot function without a
    project. Raising is the point: the alternative is silently writing a
    project-scoped artifact into a shared tool tree, where it is both wrong and
    invisible.

Do **not** reach for ``update_info.project_dir`` directly, and never derive the
root as ``os.path.dirname(deps_dir)`` -- that is the expression these accessors
exist to replace, and it escapes the tool directory.

If every artifact your root phase produces is project-scoped, declare that
instead of branching, and the dispatcher will skip you with a note:

.. code-block:: python

    from ivpm.handlers.package_handler import PackageHandler, ToolchainSupport

    class MyProjectScopedHandler(PackageHandler):
        name = "myhandler"
        toolchain_support = ToolchainSupport.UNSUPPORTED

Leaf callbacks are unaffected: they operate on a package inside the deps-dir,
which exists in both modes.


Handler Ordering
=================

Root callbacks run in an order computed from two things: each handler's
**phase** and its relative **constraints**. Built-in and extension handlers are
ordered together by the same rules -- an extension handler is not forced to run
after every built-in.

**Phases.** Every handler belongs to one of five ordered, barrier-separated
phases:

.. code-block:: text

    PREPARE  ->  ENVIRONMENT  ->  INSTALL  ->  INTEGRATE  ->  FINALIZE

Because phases are barriers, *all* handlers in one phase finish before *any*
handler in the next starts. A handler in ``INTEGRATE`` can therefore assume the
managed Python venv (built in ``INSTALL``) already exists, without naming the
``python`` handler explicitly.

**Relative constraints.** Within (or across) phases, use ``run_after`` /
``run_before`` to order relative to a specific handler or phase:

.. code-block:: python

    # Run after the python handler, whatever phase it lands in:
    phase      = HandlerPhase.INTEGRATE
    run_after  = ["python"]

    # Or relative to a whole phase:
    run_before = ["phase:integrate"]   # finish before any INTEGRATE handler

A constraint that names a handler which is not installed is ignored with a
warning, so referencing an optional handler is safe. Constraints that form a
cycle (directly, or by contradicting the phase order) abort the run with a
clear error before any handler executes.

**Tie-break.** Handlers in the same phase with no constraint between them run
in a deterministic, reproducible order (by name) -- never by entry-point load
order.

**Inspecting the result.** Run ``ivpm show handler --order`` to print the fully
resolved execution order, and ``ivpm show handler <name>`` to see a handler's
phase and constraints.

Leaf callbacks always run concurrently with no guaranteed ordering; only the
root phase is ordered.

To run after the built-in install step, use ``phase = HandlerPhase.INTEGRATE``
(the default).  To run after a *specific* built-in regardless of its phase, add
``run_after = ["python"]``.  To run before a whole phase, use
``run_before = ["phase:integrate"]``.


Testing Your Handler
====================

The simplest way to test a handler in isolation is with the stubs already used
by IVPM's own test suite:

.. code-block:: python

    import threading, unittest
    from ivpm.handlers import PackageHandler

    class FakeUpdateInfo:
        def __init__(self):
            self.event_dispatcher = None
            self.deps_dir = "/tmp/fake-deps"

    class FakePkg:
        def __init__(self, name, path="/tmp/pkg"):
            self.name = name
            self.path = pathlib.Path(path)
            self.pkg_type = None

    class TestMyHandler(unittest.TestCase):
        def test_detects_marker(self):
            h = MyHandler()
            pkg = FakePkg("test-pkg", path="/path/with/marker")
            info = FakeUpdateInfo()
            h.on_leaf_post_load(pkg, info)
            self.assertEqual(pkg.pkg_type, "my-type")


.. _package-preparers:

Contributing a Package Preparer
===============================

A **package preparer** runs immediately before a package is populated, with the
package's resolved settings and its concrete target directory in hand. It can
configure that location and it can refuse it. Handlers run *after* a package is
on disk; a preparer runs *before* anything is written for it.

The motivating case is permissions. If the target directory already exists with
the right group and the setgid bit, everything the fetch creates inside it
inherits that group as it is written -- replacing an O(files) ``chgrp -R``
post-pass with one ``chmod`` per package.

Preparers are registered through the ``ivpm.pkg_preparers`` entry-point group.
IVPM ships none, so nothing changes until you install one. Use
``ivpm show preparers`` to see what is registered and in what order it runs.

.. code-block:: python

    # acme_ivpm/disk_policy.py
    import grp, os, stat
    from ivpm.prepare import PackagePreparer, PrepareResult

    class DiskPolicyPreparer(PackagePreparer):
        name = "disk-policy"
        description = "Create each package directory with its site group"

        def on_session_start(self, update_info):
            # Once per run, on the main thread -- not once per package.
            self._member_of = {g.gr_name for g in grp.getgrall()
                               if os.getlogin() in g.gr_mem}

        def prepare(self, req):
            if req.is_virtual:            # a factory occupies no directory
                return PrepareResult.ok()

            cfg = req.config              # this preparer's own 'with:' block
            group = cfg.get("groups", {}).get(req.pkg.name,
                                              cfg.get("default-group"))
            if group is None:
                return PrepareResult.ok()
            if group not in self._member_of:
                return PrepareResult.deny(
                    "cannot set group '%s' on %s: not a member"
                    % (group, req.target_dir),
                    hint="request membership of '%s'" % group)

            os.makedirs(req.target_dir, exist_ok=True)
            os.chown(req.target_dir, -1, grp.getgrnam(group).gr_gid)
            mode = os.stat(req.target_dir).st_mode
            os.chmod(req.target_dir, mode | stat.S_ISGID)
            return PrepareResult.ok()

.. code-block:: toml

    # pyproject.toml of the extension
    [project.entry-points."ivpm.pkg_preparers"]
    disk-policy = "acme_ivpm.disk_policy:DiskPolicyPreparer"

Configuration comes from the project's ``with:`` block, keyed by the preparer's
``name`` -- the same mechanism plugin handlers use. Each preparer sees only its
own block:

.. code-block:: yaml

    package:
      name: my-proj
      with:
        disk-policy:
          default-group: eng
          groups:
            fast-dsp: dsp-restricted

Where policy must hold regardless of what a project manifest says, read it from
a site configuration (below) instead of, or in addition to, ``with:``.

Rules a preparer must follow
----------------------------

**Be idempotent.** ``prepare()`` runs again on every update for any package that
is re-fetched or reconciled. ``makedirs(exist_ok=True)`` plus an unconditional
``chown``/``chmod`` is the shape to aim for.

**Be thread-safe.** ``prepare()`` runs on the fetch worker threads, up to
``--jobs`` at a time. Put expensive or stateful work in ``on_session_start()``,
which runs once on the main thread, and cache the result on ``self``.

**Never call** ``os.umask()``. It is process-global, so setting it from a worker
thread affects every other package being fetched concurrently. setgid propagates
group ownership but *not* permission bits; if you need group-write inheritance,
set a POSIX default ACL on the directory (``setfacl -d -m g:eng:rwX``), which
inherits permission bits as well, or have the site set umask once at process
start.

**Prepare a location, do not choose content.** Rewriting ``req.pkg.url`` or
otherwise redirecting the package is out of contract; that belongs in a source
provider.

What a preparer sees, and when
------------------------------

``req.decision`` carries the load decision -- whether the package is about to be
fetched (``FETCH``), re-examined because it is patched (``RECONCILE``), or left
alone (``REUSE``). By default ``prepare()`` is called only for the first two,
since nothing is being written for a reused package. Set ``always = True`` on
the class to be consulted for every package regardless, and branch on
``req.decision``.

``req.target_dir`` is the exact directory that will be written, and it is
scope-correct: a dependency resolved under ``deps-mode: nested`` reports its
nested location. ``req.cache_dir`` is separate, because with caching enabled the
bytes land there first and are linked into the deps-dir.

Returning ``PrepareResult.deny(...)`` aborts the update and reports the refusal
against the dependency's location in ``ivpm.yaml``. Every registered preparer is
consulted before the run fails, so a package with several problems reports them
together. A preparer that raises an unexpected exception is treated as a
refusal -- a preparer that crashed did not prepare anything.

Raising ``PrepareDenied`` from ``on_session_start()`` aborts the whole run
before the root deps-dir is created, which is the right place to reject a
read-only filesystem or an exhausted quota once rather than per package.

Protecting cached content
-------------------------

With caching enabled, a package's bytes are fetched into the cache and only
*linked* into ``req.target_dir``, so changing the group or mode of
``target_dir`` protects nothing that is cached. To protect cached content,
return a ``ProtectionPolicy`` with the result:

.. code-block:: python

    from ivpm.protection import ProtectionPolicy

    def prepare(self, req):
        policy = ProtectionPolicy.for_group("dsp-restricted",
                                            dir_mode=0o2770, file_mode=0o660)
        return PrepareResult.ok(policy=policy)

The policy is part of the cache path: entries live under
``<cache>/<pkg>/protect_p<digest>/<version>``, where the digest covers the gid,
both modes and any default ACL. (Releases 2.36–2.40 named these
``@protect.<digest>``; IVPM still recognises those but no longer writes them.) Two users who resolve to
different policies get separate copies instead of evicting, or being locked out
of, each other's. Use the same modes for everyone who shares a group; different modes mean
different partitions.

Directories get ``dir_mode`` plus setgid. Files get ``file_mode``, with execute
carried over ``chmod X``-style: if the source file has any execute bit, execute
is added for each class that ``file_mode`` grants read to. With
``file_mode=0o660``, a script comes out ``0o770`` and a data file ``0o660``,
both with write removed once the entry is sealed. setuid, setgid and sticky are
never carried over, and ``file_mode`` must not contain them.

A preparer that returns a policy should normally set ``always = True``, so that
the policy is also attached to packages that are reused rather than fetched.


.. _clone-preparers:

Contributing a Clone Preparer
=============================

A **clone preparer** is the root-project counterpart of a package preparer. It
runs when ``ivpm clone`` creates a workspace, before the clone provider writes
anything. It can refuse the clone, and it can set up the workspace directory --
its group, mode or default ACL -- so that everything the clone and the
post-clone update write inherits those settings.

IVPM ships none, and it never interprets site policy. Which groups exist, which
sources are export-restricted and where workspaces may live are the extension's
business. Core only runs the hooks, checks that a prepared directory is still
empty, and cleans up after a failed clone.

Clone preparers are registered through the ``ivpm.clone_preparers`` entry-point
group. Use ``ivpm show clone-preparers`` to see what is installed, in what
order it runs, and which stages each one implements. ``ivpm clone --help`` also
lists them.

Two stages
----------

A clone preparer overrides ``check()``, ``prepare()``, or both:

``check(req)``
    Decide whether the clone may proceed. It **must not write anything**. Every
    applicable preparer's ``check()`` runs, and all refusals are reported
    together.

``prepare(req)``
    Runs only when no ``check()`` refused. It may create ``req.target_dir`` and
    its missing parents, and set metadata on them. It runs in ``order``, and
    the first refusal stops the stage.

Each returns a ``ClonePrepareResult``: ``ok()`` (or ``None``) to proceed,
``warn(message, hint)`` to proceed with a warning, or ``deny(message, hint)``
to refuse. A refusal stops the clone before anything is fetched:

.. code-block:: text

    clone of 'git@github.com:acme/chip.git' was refused by the 'acme-export' clone preparer
      export-restricted sources may not be cloned here
      Hint: clone into an export-controlled area

``applies(req)`` (default ``True``) lets a preparer opt out of a clone
entirely. ``order`` (default 100, lower first, ties broken by name) sets the
order for both stages. One instance is created per clone, so ``prepare()`` can
use what ``check()`` worked out and cached on ``self``.

Example
-------

.. code-block:: python

    # acme_ivpm/workspace_policy.py
    import grp, os
    from ivpm.clone import (ClonePreparer, ClonePrepareResult,
                            ManifestLoadError)

    class WorkspacePolicy(ClonePreparer):
        name = "acme-workspace"
        description = "Refuse restricted sources; create the workspace in its group"

        def check(self, req):
            try:
                cfg = req.config()        # this preparer's 'with:' block
            except ManifestLoadError as e:
                return ClonePrepareResult.warn(
                    "cannot read the workspace policy: %s" % e)
            if cfg.get("export") == "restricted" \
                    and not req.target_dir.startswith("/proj/ec/"):
                return ClonePrepareResult.deny(
                    "export-restricted sources may not be cloned here",
                    hint="clone under /proj/ec/")
            self._group = cfg.get("group")
            if self._group is not None:
                try:
                    self._gid = grp.getgrnam(self._group).gr_gid
                except KeyError:
                    return ClonePrepareResult.deny(
                        "unknown group '%s'" % self._group)
                if self._gid not in os.getgroups():
                    return ClonePrepareResult.deny(
                        "you are not a member of '%s'" % self._group,
                        hint="request membership of '%s'" % self._group)

        def prepare(self, req):
            if self._group is None or req.target_nonempty:
                return None
            os.makedirs(req.target_dir, exist_ok=True)
            os.chown(req.target_dir, -1, self._gid)
            os.chmod(req.target_dir, 0o2770)   # setgid: the group is inherited

.. code-block:: toml

    # pyproject.toml of the extension
    [project.entry-points."ivpm.clone_preparers"]
    acme-workspace = "acme_ivpm.workspace_policy:WorkspacePolicy"

The configuration is in the source's own ``ivpm.yaml``, keyed by the
preparer's ``name``. It can be set for the package and overridden per dep-set,
as for any other ``with:`` block:

.. code-block:: yaml

    package:
      name: chip
      with:
        acme-workspace:
          group: chip-eng
      dep-sets:
        - name: default-dev
          deps: [...]
        - name: secure
          with:
            acme-workspace:
              export: restricted
          deps: [...]

What a preparer sees
--------------------

``req`` is a ``ClonePrepareRequest``:

``src``, ``url``, ``provider``
    The locator as typed, the URL after ``IVPM_GIT_URL_MAP`` rewriting, and the
    clone provider that will run.

``target_dir``
    The absolute workspace directory. It usually does not exist yet. With
    ``--here``, or when the git provider reuses an existing checkout, it may
    already hold content; ``target_nonempty`` says so, as it was before any
    ``prepare()`` ran.

``branch``, ``tag``, ``revision``, ``dep_set``, ``cli_overrides``, ``args``
    What was asked for on the command line: the ref, the ``-d`` dep-sets, the
    ``-D`` definitions, and the full argument namespace.

``read_file(path)``
    The text of a file in the source, relative to its root, at the requested
    ref -- without cloning. ``None`` when the file does not exist, or when the
    provider cannot read files before cloning. The built-in git provider can;
    see :doc:`clone_providers`.

The source's manifest is available through three helpers, all built on
``read_file()``. They see the manifest at the requested branch, tag or
revision -- the one the post-clone update will install from:

``load_root_package()``
    The root ``ivpm.yaml`` with its ``include:`` files merged and ``-D`` values
    applied, as a ``ProjInfo``. ``None`` when the source has no ``ivpm.yaml``
    or the provider cannot read it. Parsed once per clone, however many
    preparers ask.

``selected_dep_sets()``
    The dep-set name(s) the post-clone update will install: the ``-d`` values,
    else ``default-dep-set``, else the first dep-set.

``config(name=None)``
    The ``with: <name>:`` block as the post-clone update will see it: the
    package-level block overlaid by the selected dep-sets' blocks. With no
    argument, the calling preparer's own block. ``{}`` when there is no
    manifest or no such block. The result is a copy.

All three raise ``ManifestLoadError`` when the manifest exists but cannot be
loaded -- malformed YAML, an ``include:`` that is missing or outside the
source tree, an unknown ``-d`` dep-set. The message is the diagnostic IVPM
would have printed; nothing is printed. Normally turn it into a WARN, as the
example does: refusing would stop someone from cloning a broken branch in order
to fix it. The post-clone update reads the manifest again and reports the
problem then. Only the root manifest is read; dependency manifests are not
fetched.

Rules a clone preparer must follow
----------------------------------

**Write nothing in** ``check()``. Every check runs before any prepare, so that
a refusal from any preparer means no preparer has touched the disk.

**Leave** ``target_dir`` **empty.** ``prepare()`` may create the directory and
its parents and set group, mode, default ACLs or xattrs on them, but must not
create entries inside it -- the provider populates it. IVPM checks this, and
refuses the clone if a preparer left anything behind, leaving the directory
in place for inspection.

**Never call** ``os.umask()``. It is process-wide, and the post-clone update
fetches on worker threads. Use setgid on the directory to propagate the group,
and a default ACL (``setfacl -d -m g:eng:rwX``) to propagate permission bits.

**Respect an existing workspace.** When ``target_nonempty`` is true, the
directory belongs to the user. Changing its metadata is the preparer's choice,
and IVPM does not undo it.

**Treat the result as a head start, not a guarantee.** ``prepare()`` acts on
the manifest as it was read before the clone, and a branch can move before the
clone happens. Post-clone handlers and package preparers remain
authoritative. If two clone preparers set the same metadata, the later one in
``order`` wins; IVPM does not merge them.

Failure and cleanup
-------------------

Clone preparers fail closed:

- A preparer that raises in ``applies()``, ``check()`` or ``prepare()`` refuses
  the clone. The message names the preparer, the method and the exception;
  ``ivpm --log-level DEBUG clone ...`` shows the traceback. Return ``warn()``
  instead when your preparer cannot reach a verdict and the clone should go
  ahead.
- A clone preparer that is installed but fails to import stops ``ivpm clone``.
  A preparer exists to validate or prepare where a workspace is created, so
  IVPM will not clone without it; fix or uninstall the distribution that
  provides it.

Before ``prepare()`` runs, IVPM records which components of ``target_dir``'s
path do not exist yet. If ``prepare()`` refuses or raises, or the clone itself
fails, IVPM removes those directories again, deepest first, but only if they
are still empty -- it never deletes content, and never removes a directory
that existed before. Once the clone has succeeded, nothing is rolled back: a
failure in the post-clone update leaves the populated workspace, as it does
without preparers.

Prompts and credentials
-----------------------

Clone preparers run before the progress display starts, so a preparer may
prompt on the terminal; ``req.prompt_callback`` is ``None``.

``read_file()`` contacts the source before the clone does. Over https with no
credential helper (and no ``gh`` login), git may therefore ask for credentials
twice: once for the pre-clone read, once for the clone. With ssh, a
credential helper or ``gh auth login``, it does not ask.


Contributing a Site Configuration
=================================

A **site configuration** lets an organization override IVPM's defaults --
the cache directory, how IVPM installs itself into new virtual environments,
and the default git authentication order. The recommended way to ship one is as
an **extension** that declares an ``ivpm.site_config`` entry point. This composes
cleanly with a stock install: users ``pip install ivpm`` as normal, then install
your extension to enforce site policy -- no patched ``site_config.py`` and no
reserved module name.

Write a :class:`~ivpm.site_config.SiteConfig` subclass and override only the
methods you care about:

.. code-block:: python

    # src/acme_ivpm/site_config.py
    from ivpm.site_config import SiteConfig

    class AcmeSiteConfig(SiteConfig):
        """Acme Corp site policy."""

        def get_default_cache_dir(self) -> str:
            return "/opt/acme/ivpm-cache"      # return "" to disable caching

        def get_ivpm_install_args(self) -> list:
            return ["/opt/acme/ivpm-custom.whl"]   # install IVPM from an internal wheel

        def get_default_git_auth_order(self) -> list:
            return ["ssh"]                     # never use gh on the corporate network

Register it via the ``ivpm.site_config`` entry-point group:

.. code-block:: toml

    [project.entry-points."ivpm.site_config"]
    acme = "acme_ivpm.site_config:AcmeSiteConfig"

The entry-point target may be a ``SiteConfig`` **subclass** (instantiated on
demand) or a zero-argument **callable** that returns a ``SiteConfig`` instance.

.. note::

   The historical ``ivpm_site_config`` module (a top-level package exposing a
   ``get_config()`` function) is still honored, but the entry-point group above
   is preferred because it does not require owning a specific module name and is
   discovered the same way as every other IVPM extension.

Selecting the active config
---------------------------

IVPM resolves a *single* active site config from everything registered:
``DefaultSiteConfig`` first, then a legacy ``ivpm_site_config`` module, then
each ``ivpm.site_config`` entry point. By default the **last-registered** config
wins, so installing one extension is enough to take over -- the common case.

When more than one config is registered, pin the active one by name (use
``ivpm show site-config`` to see the registered names):

* the ``IVPM_SITE_CONFIG_NAME`` environment variable (highest priority), or
* a top-level ``site-config: <name>`` key in a user or site config file
  (``~/.config/ivpm/config.yaml`` or ``/etc/ivpm/config.yaml``).

A name that matches no registered config is ignored with a warning, and
resolution falls back to last-registered.

Inspecting what is registered
-----------------------------

After installing your extension, run::

    ivpm show site-config

to list every registered config (the active one is flagged), and the
**effective settings** the active config applies -- resolved cache directory,
``ivpm`` install arguments, git auth order, and the config files that were
loaded. ``ivpm show site-config <name>`` shows the detail for one config, and
``--json`` emits the same information for tooling.


Handlers That Install Content
=============================

If your handler passes package-contributed inputs to an external installer
(pip, npm, cargo, ...), it must be able to attribute a failure back to the
dependency that caused it and to the ``ivpm.yaml`` line that imported it.  A
user who is shown only the installer's output has no next step -- the failing
input is usually contributed by a transitive dependency they never wrote down.

Two obligations:

1. Record every emitted input against its contributing ``Package``, at the
   point it is emitted.
2. Provide a way to identify the culprit that does **not** depend on parsing
   the installer's output.  ``ivpm.content_attrib.isolate`` does this by
   re-running inputs individually.

Run installers through ``ivpm.installer_run.run_installer`` rather than
``subprocess`` directly: it captures output unconditionally, so a quiet run
still has the evidence a failure report needs.

See :ref:`handler-content-attribution` in :doc:`handlers` for the API and
worked examples.

See Also
========

- :doc:`handlers` -- Built-in handler documentation and the handler summary table
- :doc:`package_types` -- Package source types and content types
- :doc:`clone_providers` -- Pluggable ``ivpm clone`` source providers (which
  also describe the root project for ``ivpm status`` via ``probe()`` /
  ``root_status()``), and what a provider owes clone preparers
- :doc:`package_lock` -- The lock file, and the load decision that reads it
- :doc:`caching` -- Customizing the cache through a site configuration
- :doc:`git_integration` -- Site-managed git authentication defaults
