"""
Handler teardown tests for `ivpm destroy`: PackageHandler.on_destroy().
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.handlers.package_handler import PackageHandler
from ivpm.handlers.package_handler_python import PackageHandlerPython
from ivpm.handlers.package_handler_node import PackageHandlerNode
from ivpm.project_ops_info import ProjectRemoveInfo


def _info(deps_dir, dry=False, keep_venv=False):
    return ProjectRemoveInfo(args=None, deps_dir=deps_dir,
                             dry_run=dry, keep_venv=keep_venv)


class TestDestroyHandlers(TestBase):

    def test_base_handler_on_destroy_is_noop(self):
        h = PackageHandler()
        self.assertIsNone(h.on_destroy(_info(self.testdir)))

    def _mk(self, sub):
        deps = os.path.join(self.testdir, "packages")
        d = os.path.join(deps, sub)
        os.makedirs(d)
        with open(os.path.join(d, "marker"), "w") as fp:
            fp.write("x")
        return deps, d

    def test_python_handler_removes_venv(self):
        deps, python_dir = self._mk("python")
        removed = PackageHandlerPython().on_destroy(_info(deps))
        self.assertEqual(removed, [python_dir])
        self.assertFalse(os.path.exists(python_dir))

    def test_python_handler_dry_run_keeps_venv(self):
        deps, python_dir = self._mk("python")
        removed = PackageHandlerPython().on_destroy(_info(deps, dry=True))
        self.assertEqual(removed, [python_dir])
        self.assertTrue(os.path.isdir(python_dir))

    def test_python_handler_keep_venv(self):
        deps, python_dir = self._mk("python")
        removed = PackageHandlerPython().on_destroy(_info(deps, keep_venv=True))
        self.assertIsNone(removed)
        self.assertTrue(os.path.isdir(python_dir))

    def test_python_handler_no_venv_is_noop(self):
        deps = os.path.join(self.testdir, "packages")
        os.makedirs(deps)
        self.assertIsNone(PackageHandlerPython().on_destroy(_info(deps)))

    def test_node_handler_removes_node_dir(self):
        deps, node_dir = self._mk("node")
        removed = PackageHandlerNode().on_destroy(_info(deps))
        self.assertEqual(removed, [node_dir])
        self.assertFalse(os.path.exists(node_dir))

    def test_node_handler_dry_run(self):
        deps, node_dir = self._mk("node")
        removed = PackageHandlerNode().on_destroy(_info(deps, dry=True))
        self.assertEqual(removed, [node_dir])
        self.assertTrue(os.path.isdir(node_dir))
