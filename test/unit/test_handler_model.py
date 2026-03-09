"""
Tests for the root/leaf handler model introduced in the handler system redesign.

Coverage:
- HasType, HasSourceType, ALWAYS condition objects
- leaf_when / root_when condition evaluation in PackageHandlerList
- Phase ordering for root handlers
- Auto-skip of leaf-less handlers
- reset() called by on_root_pre_load
- Thread-safe leaf accumulation (concurrent calls)
- task_context() / TaskHandle progress event emission
- Nested task_context() correct parent_task_id chain
- Leaf error policy: log+continue vs HandlerFatalError abort
- Deprecated process_pkg / update shims
"""

import dataclasses as dc
import threading
import time
import unittest
from typing import ClassVar, List, Optional
from unittest.mock import MagicMock, call

from ivpm.handlers.package_handler import PackageHandler, HandlerFatalError, TaskHandle
from ivpm.handlers.package_handler_list import PackageHandlerList
from ivpm.handlers.handler_conditions import ALWAYS, HasType, HasSourceType
from ivpm.update_event import UpdateEventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakePkg:
    """Minimal package stub for testing."""
    def __init__(self, name="pkg", pkg_type=None, src_type=None, type_data=None):
        self.name = name
        self.pkg_type = pkg_type
        self.src_type = src_type
        self.type_data = type_data or []


class FakeDispatcher:
    """Records all dispatched events."""
    def __init__(self):
        self.events = []

    def dispatch(self, event):
        self.events.append(event)

    def types(self):
        return [e.event_type for e in self.events]


class FakeUpdateInfo:
    """Minimal ProjectUpdateInfo stub."""
    def __init__(self, dispatcher=None):
        self.event_dispatcher = dispatcher
        self.args = MagicMock()


class FakeTypeData:
    def __init__(self, type_name):
        self.type_name = type_name


# ---------------------------------------------------------------------------
# Condition tests
# ---------------------------------------------------------------------------

class TestAlways(unittest.TestCase):

    def test_always_true_for_pkg(self):
        self.assertTrue(ALWAYS(FakePkg()))

    def test_always_true_for_list(self):
        self.assertTrue(ALWAYS([]))

    def test_repr(self):
        self.assertEqual(repr(ALWAYS), "ALWAYS")


class TestHasType(unittest.TestCase):

    def test_matches_pkg_type(self):
        pkgs = [FakePkg(pkg_type="python"), FakePkg(pkg_type="other")]
        self.assertTrue(HasType("python")(pkgs))

    def test_no_match(self):
        pkgs = [FakePkg(pkg_type="other")]
        self.assertFalse(HasType("python")(pkgs))

    def test_empty_list(self):
        self.assertFalse(HasType("python")([]))

    def test_matches_type_data(self):
        pkgs = [FakePkg(type_data=[FakeTypeData("fusesoc")])]
        self.assertTrue(HasType("fusesoc")(pkgs))

    def test_repr(self):
        self.assertEqual(repr(HasType("python")), "HasType('python')")


class TestHasSourceType(unittest.TestCase):

    def test_leaf_mode_match(self):
        pkg = FakePkg(src_type="git")
        self.assertTrue(HasSourceType("git")(pkg))

    def test_leaf_mode_no_match(self):
        pkg = FakePkg(src_type="dir")
        self.assertFalse(HasSourceType("git")(pkg))

    def test_root_mode_match(self):
        pkgs = [FakePkg(src_type="dir"), FakePkg(src_type="git")]
        self.assertTrue(HasSourceType("git")(pkgs))

    def test_root_mode_no_match(self):
        pkgs = [FakePkg(src_type="dir")]
        self.assertFalse(HasSourceType("git")(pkgs))

    def test_repr(self):
        self.assertEqual(repr(HasSourceType("git")), "HasSourceType('git')")


# ---------------------------------------------------------------------------
# PackageHandler base class tests
# ---------------------------------------------------------------------------

class TestPackageHandlerBase(unittest.TestCase):

    def test_reset_called_by_on_root_pre_load(self):
        @dc.dataclass
        class MyHandler(PackageHandler):
            reset_count: int = 0
            def reset(self):
                self.reset_count += 1

        h = MyHandler()
        h.on_root_pre_load(FakeUpdateInfo())
        self.assertEqual(h.reset_count, 1)
        h.on_root_pre_load(FakeUpdateInfo())
        self.assertEqual(h.reset_count, 2)

    def test_process_pkg_shim(self):
        calls = []
        class MyHandler(PackageHandler):
            def on_leaf_post_load(self, pkg, update_info):
                calls.append((pkg, update_info))

        h = MyHandler()
        pkg = FakePkg()
        h.process_pkg(pkg)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], pkg)
        self.assertIsNone(calls[0][1])  # deprecated shim passes None

    def test_update_shim(self):
        calls = []
        class MyHandler(PackageHandler):
            def on_root_post_load(self, update_info):
                calls.append(update_info)

        h = MyHandler()
        info = FakeUpdateInfo()
        h.update(info)
        self.assertEqual(calls, [info])

    def test_get_lock_entries_default(self):
        self.assertEqual(PackageHandler().get_lock_entries("/tmp"), {})


# ---------------------------------------------------------------------------
# task_context() / TaskHandle tests
# ---------------------------------------------------------------------------

class TestTaskContext(unittest.TestCase):

    def test_start_end_events_emitted(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)

        h = PackageHandler()
        with h.task_context(info, "t1", "Task One"):
            pass

        types = dispatcher.types()
        self.assertIn(UpdateEventType.HANDLER_TASK_START, types)
        self.assertIn(UpdateEventType.HANDLER_TASK_END, types)
        self.assertNotIn(UpdateEventType.HANDLER_TASK_ERROR, types)
        start = dispatcher.events[0]
        self.assertEqual(start.task_id, "t1")
        self.assertEqual(start.task_name, "Task One")
        self.assertIsNone(start.parent_task_id)

    def test_error_event_on_exception(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)

        h = PackageHandler()
        with self.assertRaises(ValueError):
            with h.task_context(info, "t2", "Task Two"):
                raise ValueError("boom")

        types = dispatcher.types()
        self.assertIn(UpdateEventType.HANDLER_TASK_ERROR, types)
        self.assertNotIn(UpdateEventType.HANDLER_TASK_END, types)
        err = next(e for e in dispatcher.events if e.event_type == UpdateEventType.HANDLER_TASK_ERROR)
        self.assertEqual(err.task_id, "t2")
        self.assertIn("boom", err.task_message)

    def test_no_dispatcher_no_crash(self):
        info = FakeUpdateInfo(dispatcher=None)
        h = PackageHandler()
        with h.task_context(info, "t3", "Task Three"):
            pass  # must not raise

    def test_nested_task_has_correct_parent_task_id(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)

        h = PackageHandler()
        with h.task_context(info, "root", "Root Task") as root_task:
            with root_task.task_context("child", "Child Task"):
                pass

        events_by_id = {e.task_id: e for e in dispatcher.events
                        if e.event_type == UpdateEventType.HANDLER_TASK_START}
        self.assertIsNone(events_by_id["root"].parent_task_id)
        self.assertEqual(events_by_id["child"].parent_task_id, "root")

    def test_deeply_nested_tasks(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)

        h = PackageHandler()
        with h.task_context(info, "a", "A") as ta:
            with ta.task_context("b", "B") as tb:
                with tb.task_context("c", "C"):
                    pass

        starts = {e.task_id: e for e in dispatcher.events
                  if e.event_type == UpdateEventType.HANDLER_TASK_START}
        self.assertIsNone(starts["a"].parent_task_id)
        self.assertEqual(starts["b"].parent_task_id, "a")
        self.assertEqual(starts["c"].parent_task_id, "b")

    def test_progress_event(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)

        h = PackageHandler()
        with h.task_context(info, "p", "Progress") as task:
            task.progress("step 1", step=1, total=3)

        prog = [e for e in dispatcher.events if e.event_type == UpdateEventType.HANDLER_TASK_PROGRESS]
        self.assertEqual(len(prog), 1)
        self.assertEqual(prog[0].task_id, "p")
        self.assertEqual(prog[0].task_step, 1)
        self.assertEqual(prog[0].task_total, 3)
        self.assertEqual(prog[0].task_message, "step 1")


# ---------------------------------------------------------------------------
# PackageHandlerList routing tests
# ---------------------------------------------------------------------------

class TestPackageHandlerList(unittest.TestCase):

    def _make_list(self, *handlers):
        hl = PackageHandlerList()
        for h in handlers:
            hl.addHandler(h)
        return hl

    def test_leaf_post_load_called_for_each_pkg(self):
        seen = []
        class MyHandler(PackageHandler):
            def on_leaf_post_load(self, pkg, update_info):
                seen.append(pkg.name)

        hl = self._make_list(MyHandler())
        pkgs = [FakePkg(name=f"p{i}") for i in range(3)]
        info = FakeUpdateInfo()
        for pkg in pkgs:
            hl.on_leaf_post_load(pkg, info)
        self.assertEqual(seen, ["p0", "p1", "p2"])

    def test_leaf_less_handler_skipped_in_leaf(self):
        """A handler that doesn't override any leaf callback must not be called."""
        called = []
        class RootOnlyHandler(PackageHandler):
            def on_root_post_load(self, update_info):
                called.append("root")

        hl = self._make_list(RootOnlyHandler())
        hl.on_leaf_post_load(FakePkg(), FakeUpdateInfo())
        self.assertEqual(called, [])

    def test_leaf_when_filters_per_package(self):
        seen = []
        class GitHandler(PackageHandler):
            leaf_when = [HasSourceType("git")]
            def on_leaf_post_load(self, pkg, update_info):
                seen.append(pkg.name)

        hl = self._make_list(GitHandler())
        hl.on_leaf_post_load(FakePkg(name="git-pkg", src_type="git"), FakeUpdateInfo())
        hl.on_leaf_post_load(FakePkg(name="dir-pkg", src_type="dir"), FakeUpdateInfo())
        self.assertEqual(seen, ["git-pkg"])

    def test_root_when_skips_handler_with_no_matching_pkgs(self):
        called = []
        class PythonRootHandler(PackageHandler):
            root_when = [HasType("python")]
            def on_leaf_post_load(self, pkg, update_info):
                pass
            def on_root_post_load(self, update_info):
                called.append("root")

        hl = self._make_list(PythonRootHandler())
        # No python packages loaded — root_when should prevent root call
        hl.on_leaf_post_load(FakePkg(pkg_type="other"), FakeUpdateInfo())
        hl.on_root_post_load(FakeUpdateInfo())
        self.assertEqual(called, [])

    def test_root_when_runs_handler_when_pkg_matches(self):
        called = []
        class PythonRootHandler(PackageHandler):
            root_when = [HasType("python")]
            def on_leaf_post_load(self, pkg, update_info):
                pass
            def on_root_post_load(self, update_info):
                called.append("root")

        hl = self._make_list(PythonRootHandler())
        hl.on_leaf_post_load(FakePkg(pkg_type="python"), FakeUpdateInfo())
        hl.on_root_post_load(FakeUpdateInfo())
        self.assertEqual(called, ["root"])

    def test_root_pre_load_always_called(self):
        """on_root_pre_load must be called on all handlers regardless of root_when."""
        pre_called = []
        class CondHandler(PackageHandler):
            root_when = [HasType("nevermatches")]
            def on_root_pre_load(self, update_info):
                super().on_root_pre_load(update_info)
                pre_called.append("pre")
            def on_leaf_post_load(self, pkg, update_info):
                pass

        hl = self._make_list(CondHandler())
        hl.on_root_pre_load(FakeUpdateInfo())
        self.assertEqual(pre_called, ["pre"])

    def test_phase_ordering(self):
        order = []

        class HandlerA(PackageHandler):
            phase = 2
            def on_leaf_post_load(self, pkg, update_info):
                pass
            def on_root_post_load(self, update_info):
                order.append("A")

        class HandlerB(PackageHandler):
            phase = 0
            def on_leaf_post_load(self, pkg, update_info):
                pass
            def on_root_post_load(self, update_info):
                order.append("B")

        class HandlerC(PackageHandler):
            phase = 1
            def on_leaf_post_load(self, pkg, update_info):
                pass
            def on_root_post_load(self, update_info):
                order.append("C")

        hl = self._make_list(HandlerA(), HandlerB(), HandlerC())
        hl.on_root_post_load(FakeUpdateInfo())
        self.assertEqual(order, ["B", "C", "A"])

    def test_reset_clears_accumulated_packages(self):
        seen_counts = []

        class Accumulator(PackageHandler):
            root_when = [HasType("x")]
            def on_leaf_post_load(self, pkg, update_info):
                pass
            def on_root_post_load(self, update_info):
                pass

        hl = self._make_list(Accumulator())
        info = FakeUpdateInfo()
        # First "update run"
        for _ in range(3):
            hl.on_leaf_post_load(FakePkg(pkg_type="x"), info)
        seen_counts.append(len(hl._all_pkgs))

        # Reset (simulates a new run)
        hl.on_root_pre_load(info)
        seen_counts.append(len(hl._all_pkgs))

        self.assertEqual(seen_counts, [3, 0])


# ---------------------------------------------------------------------------
# Thread-safe leaf accumulation test
# ---------------------------------------------------------------------------

class TestConcurrentLeafAccumulation(unittest.TestCase):

    def test_concurrent_leaf_calls_are_thread_safe(self):
        N = 200

        @dc.dataclass
        class AccumulatorHandler(PackageHandler):
            items: list = dc.field(default_factory=list)
            def on_leaf_post_load(self, pkg, update_info):
                with self._lock:
                    self.items.append(pkg.name)

        handler = AccumulatorHandler()
        hl = PackageHandlerList()
        hl.addHandler(handler)

        errors = []
        barrier = threading.Barrier(N)

        def leaf_call(i):
            try:
                barrier.wait(timeout=5)
                hl.on_leaf_post_load(FakePkg(name=f"pkg-{i}"), FakeUpdateInfo())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=leaf_call, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"Errors during concurrent leaf calls: {errors}")
        self.assertEqual(len(handler.items), N)
        self.assertEqual(len(hl._all_pkgs), N)


# ---------------------------------------------------------------------------
# Leaf error policy tests
# ---------------------------------------------------------------------------

class TestLeafErrorPolicy(unittest.TestCase):
    """
    PackageUpdater catches leaf handler exceptions per policy:
    - HandlerFatalError  → re-raised (aborts update)
    - Other exceptions   → log + continue

    These tests verify the exception types are raised and caught correctly
    from within a handler, not the PackageUpdater integration.
    """

    def test_handler_fatal_error_propagates(self):
        class BadHandler(PackageHandler):
            def on_leaf_post_load(self, pkg, update_info):
                raise HandlerFatalError("auth failed")

        h = BadHandler()
        with self.assertRaises(HandlerFatalError):
            h.on_leaf_post_load(FakePkg(), FakeUpdateInfo())

    def test_regular_exception_propagates_by_default(self):
        class FlakyHandler(PackageHandler):
            def on_leaf_post_load(self, pkg, update_info):
                raise RuntimeError("transient")

        h = FlakyHandler()
        with self.assertRaises(RuntimeError):
            h.on_leaf_post_load(FakePkg(), FakeUpdateInfo())

    def test_handler_fatal_error_is_exception(self):
        self.assertIsInstance(HandlerFatalError("x"), Exception)


# ---------------------------------------------------------------------------
# Handler metadata ClassVar tests
# ---------------------------------------------------------------------------

class TestHandlerMetadata(unittest.TestCase):

    def test_classvar_metadata_accessible_from_instance(self):
        class MyHandler(PackageHandler):
            name = "myhandler"
            description = "Test handler"
            phase = 5

        h = MyHandler()
        self.assertEqual(type(h).name, "myhandler")
        self.assertEqual(type(h).description, "Test handler")
        self.assertEqual(type(h).phase, 5)

    def test_default_phase_zero(self):
        class DefaultHandler(PackageHandler):
            pass
        self.assertEqual(type(DefaultHandler()).phase, 0)

    def test_default_leaf_when_none(self):
        class DefaultHandler(PackageHandler):
            pass
        self.assertIsNone(type(DefaultHandler()).leaf_when)

    def test_default_root_when_none(self):
        class DefaultHandler(PackageHandler):
            pass
        self.assertIsNone(type(DefaultHandler()).root_when)


if __name__ == "__main__":
    unittest.main()
