"""
Tests that an unexpected exception from a handler is reported as an internal
error -- one line and a distinct exit status -- rather than a traceback.

IVPM's own error types (fatal()'s SrcLoaderError, HandlerFatalError) are user
reports and have their own handling. Anything else is a bug, and used to reach
the interpreter as a raw traceback (root callbacks, main()) or be logged on the
silent debug channel while the run carried on (leaf callbacks).

Coverage:
- InternalError names handler and action, and writes the stack trace to a file
- PackageHandlerList names the handler that raised; IVPM errors pass through
- _dispatch_leaf wraps unexpected errors and passes IVPM errors through
- the batch driver does not recast an InternalError as "Failed to update"
- main() exits 70 with no traceback for a handler raising RuntimeError, and
  for an unexpected exception outside any handler
"""

import asyncio
import os
import subprocess
import sys
import unittest

from ivpm.handlers.package_handler import PackageHandler, HandlerFatalError
from ivpm.handlers.package_handler_list import PackageHandlerList
from ivpm.internal_error import (InternalError, EXIT_INTERNAL_ERROR,
                                 EXIT_USER_ERROR)
from ivpm.msg import SrcLoaderError
from ivpm.package import Package
from ivpm.package_updater import PackageUpdater, _dispatch_leaf

from .test_base import TestBase, venv_python


class _BoomHandler(PackageHandler):
    name = "boom"
    leaf_when = None
    root_when = None

    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    def on_root_post_load(self, update_info):
        raise self._exc

    def on_leaf_post_load(self, pkg, update_info):
        raise self._exc


def _raise(exc):
    try:
        raise exc
    except Exception as e:
        return e


class TestInternalError(TestBase):

    def setUp(self):
        super().setUp()
        self._tmpdir = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = self.testdir
        import tempfile
        tempfile.tempdir = None

    def tearDown(self):
        import tempfile
        if self._tmpdir is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = self._tmpdir
        tempfile.tempdir = None
        super().tearDown()

    def test_summary_names_handler_action_and_trace(self):
        e = InternalError(_raise(AttributeError("'NoneType' has no 'startswith'")),
                          "running on_root_post_load", "python")
        line = e.summary()
        self.assertNotIn("\n", line)
        self.assertIn("internal error in handler 'python'", line)
        self.assertIn("while running on_root_post_load", line)
        self.assertIn("AttributeError", line)
        self.assertIsNotNone(e.trace_path)
        self.assertIn(e.trace_path, line)
        with open(e.trace_path) as fp:
            trace = fp.read()
        self.assertIn("Traceback", trace)
        self.assertIn("_raise", trace)

    def test_handler_list_names_the_handler(self):
        hl = PackageHandlerList()
        hl.addHandler(_BoomHandler(RuntimeError("bug")))
        with self.assertRaises(InternalError) as ctx:
            hl.on_root_post_load(None)
        self.assertEqual(ctx.exception.handler, "boom")
        self.assertIn("on_root_post_load", ctx.exception.action)

    def test_handler_list_passes_ivpm_errors_through(self):
        for exc in (HandlerFatalError("expected"), SrcLoaderError("expected")):
            hl = PackageHandlerList()
            hl.addHandler(_BoomHandler(exc))
            with self.assertRaises(type(exc)):
                hl.on_root_post_load(None)

    def test_leaf_dispatch_names_the_package(self):
        h = _BoomHandler(KeyError("k"))
        with self.assertRaises(InternalError) as ctx:
            _dispatch_leaf(h.on_leaf_post_load, Package("pkg-a"), None)
        self.assertEqual(ctx.exception.handler, "boom")
        self.assertIn("'pkg-a'", ctx.exception.action)

    def test_leaf_dispatch_passes_handler_fatal_error(self):
        h = _BoomHandler(HandlerFatalError("expected"))
        with self.assertRaises(HandlerFatalError):
            _dispatch_leaf(h.on_leaf_post_load, Package("pkg-a"), None)

    def test_batch_driver_reraises_internal_error(self):
        """Not recast as a 'Failed to update <pkg>' user error."""
        original = InternalError(_raise(RuntimeError("bug")), "testing", "boom")
        updater = PackageUpdater(self.testdir, PackageHandlerList(), load=False)

        async def fail(pkg, scope, semaphore):
            raise original
        updater._update_pkg_async = fail

        # The semaphore is built inside the running loop: on Python 3.9 an
        # asyncio primitive binds the current loop at construction, and there
        # is none outside asyncio.run().
        async def run():
            return await updater._process_batch_parallel(
                [(Package("pkg-a"), None)], asyncio.Semaphore(1))

        with self.assertRaises(InternalError) as ctx:
            asyncio.run(run())
        self.assertIs(ctx.exception, original)


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = os.path.join(_ROOT, "src")

# Runs `ivpm update` through the real main() with CmdUpdate replaced. In
# 'handler' mode the replacement dispatches a root callback that raises
# RuntimeError, the way a buggy handler would; in 'command' mode the command
# itself raises, outside any handler. A subprocess is the only way to observe
# what reaches the interpreter.
_DRIVER = """
import sys
from ivpm.handlers.package_handler import PackageHandler
from ivpm.handlers.package_handler_list import PackageHandlerList
from ivpm.project_ops import _dispatch_root

mode = %r

class _Buggy(PackageHandler):
    name = "buggy"
    def on_root_post_load(self, update_info):
        path = None
        path.startswith("file://")

class _Cmd:
    def __call__(self, args):
        if mode == "handler":
            hl = PackageHandlerList()
            hl.addHandler(_Buggy())
            _dispatch_root(hl.on_root_post_load, None)
        else:
            {}["missing"]

import ivpm.cmds.cmd_update as cmd_update
cmd_update.CmdUpdate = _Cmd

from ivpm.__main__ import main
sys.argv = ["ivpm", "update"]
main()
"""


class TestMainInternalError(TestBase):

    def _run(self, mode):
        return subprocess.run(
            [venv_python(), "-c", _DRIVER % mode],
            capture_output=True, text=True, cwd=_ROOT, check=False,
            env={**os.environ, "PYTHONPATH": _SRC, "TMPDIR": self.testdir})

    def _trace_files(self):
        return [f for f in os.listdir(self.testdir)
                if f.startswith("ivpm-internal-error-")]

    def test_handler_runtime_error_has_no_traceback(self):
        result = self._run("handler")
        self.assertEqual(result.returncode, EXIT_INTERNAL_ERROR, result.stderr)
        self.assertNotEqual(EXIT_INTERNAL_ERROR, EXIT_USER_ERROR)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("internal error in handler 'buggy'", result.stderr)
        self.assertIn("on_root_post_load", result.stderr)
        self.assertEqual(1, len(result.stderr.strip().splitlines()),
                         result.stderr)

        files = self._trace_files()
        self.assertEqual(1, len(files))
        self.assertIn(files[0], result.stderr)
        with open(os.path.join(self.testdir, files[0])) as fp:
            self.assertIn("Traceback", fp.read())

    def test_error_outside_handlers_has_no_traceback(self):
        result = self._run("command")
        self.assertEqual(result.returncode, EXIT_INTERNAL_ERROR, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("internal error while running 'ivpm update'",
                      result.stderr)
        self.assertIn("KeyError", result.stderr)
        self.assertEqual(1, len(self._trace_files()))


if __name__ == "__main__":
    unittest.main()
