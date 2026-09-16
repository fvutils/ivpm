"""
Tests that a HandlerFatalError raised from a *root* callback is reported, not
dumped as a traceback.

The leaf path has always converted it: package_updater re-raises it out of
on_leaf_post_load() and the batch driver turns it into a fatal() naming the
dependency.  The root path had no equivalent, so a handler reporting an
expected condition (a required tool missing, an external command failing) cost
the user a Python traceback on top of the diagnostic it had just rendered.

Coverage:
- HandlerFatalError.reported defaults False, is settable via the constructor
- both task_context() flavours stamp reported=True on their way out
- _dispatch_root converts an unreported error into a rendered SrcLoaderError
- _dispatch_root passes a reported error through untouched
- main() exits 1 without a traceback, and prints only what nothing else has
"""

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock

from ivpm.handlers.package_handler import PackageHandler, HandlerFatalError
from ivpm.msg import SrcLoaderError
from ivpm.project_ops import _dispatch_root
from ivpm.update_event import UpdateEventType

from .test_base import venv_python


class FakeDispatcher:
    def __init__(self):
        self.events = []

    def dispatch(self, event):
        self.events.append(event)


class FakeUpdateInfo:
    def __init__(self, dispatcher=None):
        self.event_dispatcher = dispatcher
        self.args = MagicMock()


class TestReportedFlag(unittest.TestCase):

    def test_defaults_unreported(self):
        e = HandlerFatalError("boom")
        self.assertFalse(e.reported)
        self.assertEqual(str(e), "boom")

    def test_constructor_accepts_reported(self):
        self.assertTrue(HandlerFatalError("boom", reported=True).reported)

    def test_top_level_task_context_marks_reported(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)
        h = PackageHandler()

        with self.assertRaises(HandlerFatalError) as ctx:
            with h.task_context(info, "t1", "Task 1"):
                raise HandlerFatalError("boom")

        self.assertTrue(ctx.exception.reported)
        self.assertIn(UpdateEventType.HANDLER_TASK_ERROR,
                      [e.event_type for e in dispatcher.events])

    def test_nested_task_context_marks_reported(self):
        dispatcher = FakeDispatcher()
        info = FakeUpdateInfo(dispatcher)
        h = PackageHandler()

        with self.assertRaises(HandlerFatalError) as ctx:
            with h.task_context(info, "t1", "Task 1") as task:
                with task.task_context("t2", "Task 2"):
                    raise HandlerFatalError("boom")

        self.assertTrue(ctx.exception.reported)

    def test_no_dispatcher_leaves_unreported(self):
        """Nothing rendered it, so the top-level handler still has to print."""
        info = FakeUpdateInfo(dispatcher=None)
        h = PackageHandler()

        with self.assertRaises(HandlerFatalError) as ctx:
            with h.task_context(info, "t1", "Task 1"):
                raise HandlerFatalError("boom")

        self.assertFalse(ctx.exception.reported)


class TestDispatchRoot(unittest.TestCase):

    def test_clean_callback_passes_through(self):
        calls = []
        _dispatch_root(lambda info: calls.append(info), "info")
        self.assertEqual(calls, ["info"])

    def test_unreported_error_becomes_srcloadererror(self):
        """fatal() renders it, so the message survives into main()'s handler."""
        def boom(info):
            raise HandlerFatalError("[boom] deliberate, expected failure")

        with self.assertRaises(SrcLoaderError) as ctx:
            _dispatch_root(boom, FakeUpdateInfo())
        self.assertIn("deliberate, expected failure", str(ctx.exception))

    def test_reported_error_passes_through_untouched(self):
        """Already on screen: re-raise rather than render a second copy."""
        original = HandlerFatalError("[boom] already said", reported=True)

        def boom(info):
            raise original

        with self.assertRaises(HandlerFatalError) as ctx:
            _dispatch_root(boom, FakeUpdateInfo())
        self.assertIs(ctx.exception, original)

    def test_other_exceptions_are_not_swallowed(self):
        def boom(info):
            raise RuntimeError("not a handler error")

        with self.assertRaises(RuntimeError):
            _dispatch_root(boom, FakeUpdateInfo())


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = os.path.join(_ROOT, "src")

# Replaces CmdUpdate with one that raises from where a root callback would, then
# runs `ivpm update` through the real main(). A subprocess is the only way to
# observe what actually reaches the interpreter -- in-process, an uncaught
# exception would just fail the test rather than print a traceback.
_DRIVER = """
import sys
from ivpm.handlers.package_handler import HandlerFatalError

reported = %r

class _Boom:
    def __call__(self, args):
        raise HandlerFatalError("[boom] deliberate, expected failure",
                                reported=reported)

import ivpm.cmds.cmd_update as cmd_update
cmd_update.CmdUpdate = _Boom

from ivpm.__main__ import main
sys.argv = ["ivpm", "update"]
main()
"""


class TestMainBackstop(unittest.TestCase):
    """main() is the backstop for paths the dispatch sites don't cover."""

    def _run(self, reported):
        return subprocess.run(
            [venv_python(), "-c", _DRIVER % reported],
            capture_output=True, text=True, cwd=_ROOT, check=False,
            env={**os.environ, "PYTHONPATH": _SRC})

    def test_exits_cleanly_without_traceback(self):
        result = self._run(False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("HandlerFatalError", result.stderr)
        self.assertIn("deliberate, expected failure", result.stderr)

    def test_reported_error_is_not_printed_twice(self):
        """The handler already rendered it; main() must not echo it."""
        result = self._run(True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("deliberate, expected failure", result.stderr)


if __name__ == "__main__":
    unittest.main()
