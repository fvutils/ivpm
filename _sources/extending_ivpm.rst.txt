########################
Writing Custom Handlers
########################

IVPM is designed to be extended with custom **package handlers**. A handler is a
Python class that observes packages as they are loaded and performs actions -- such
as setting up a virtual environment, writing IDE integration files, or invoking a
downstream tool. IVPM discovers handlers through Python `entry points`_, so any
installed package can contribute new handlers without modifying IVPM itself.

For an overview of what handlers are, how they fit into the update pipeline,
and documentation of the three built-in handlers (Python, Direnv, Skills),
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
    from ivpm.handlers import PackageHandler, HandlerFatalError, ALWAYS, HasType

    @dc.dataclass
    class MyHandler(PackageHandler):

        # --- Metadata (class-level, not instance attributes) ---
        name:        ClassVar[str]  = "my-handler"
        description: ClassVar[str]  = "Does something useful"
        phase:       ClassVar[int]  = 0       # lower = earlier in root phase

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
    Integer ordering key for the **root** phase. Handlers with lower phase
    numbers run first. Leaf phase ordering is determined by package fetch order,
    not by this value. Default: ``0``.

``leaf_when``
    A list of **leaf conditions** (see below), or ``None`` to always run as a
    leaf handler. Use ``[]`` (empty list) to opt out of leaf dispatch entirely.

``root_when``
    A list of **root conditions** (see below), or ``None`` to always run as a
    root handler. Use ``[]`` to opt out of root dispatch entirely.


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

To abort an entire update run from inside a leaf callback, raise
``HandlerFatalError``:

.. code-block:: python

    from ivpm.handlers import HandlerFatalError

    def on_leaf_post_load(self, pkg, update_info):
        if not self._check(pkg):
            raise HandlerFatalError(f"Required file missing in {pkg.name}")

Non-fatal exceptions logged inside a leaf callback are caught and reported as
warnings; the run continues with remaining packages.


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

    from ivpm.handlers import PackageHandler, HasType

    @dc.dataclass
    class FuseSocHandler(PackageHandler):

        name:        ClassVar[str]  = "fusesoc"
        description: ClassVar[str]  = "Collect FuseSoC core libraries"
        phase:       ClassVar[int]  = 10

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
            out = pathlib.Path(update_info.deps_dir) / ".." / "fusesoc.conf"
            with self.task_context(update_info, "fusesoc-write", "Writing FuseSoC config") as task:
                task.progress(f"Writing {len(self._lib_paths)} library paths")
                with open(out, "w") as f:
                    for p in self._lib_paths:
                        f.write(f"[cores]\nlocation = {p}\n\n")

Register it:

.. code-block:: toml

    [project.entry-points."ivpm.handlers"]
    fusesoc = "myext.fusesoc_handler:FuseSocHandler"


Handler Ordering
=================

IVPM loads handlers in this order:

1. Built-in handlers (Python, Direnv, Skills) -- all at phase ``0``
2. Extension handlers discovered via ``ivpm.handlers`` entry points, in
   installation order

Within the root phase, handlers with the same phase number run in the order they
were registered. Leaf callbacks always run concurrently with no guaranteed
ordering.

To run after all built-in handlers, use ``phase = 10`` or higher. To run before
a built-in, use a negative phase (though this is rarely needed).


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


See Also
========

- :doc:`handlers` -- Built-in handler documentation and the handler summary table
- :doc:`package_types` -- Package source types and content types
