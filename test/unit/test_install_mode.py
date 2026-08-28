#****************************************************************************
#* test_install_mode.py
#*
#* Tests for InstallMode (WORKSPACE / TOOLCHAIN), the project-root accessor
#* pair, and the toolchain_support handler declaration. Pure unit tests -- no
#* subprocess, no network.
#****************************************************************************
import os
import sys
import unittest
from unittest import mock

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_UNIT_DIR))
sys.path.insert(0, os.path.join(_ROOT_DIR, "src"))

from ivpm.project_ops_info import (
    InstallMode, ProjectUpdateInfo, ToolchainModeError)
from ivpm.handlers.package_handler import PackageHandler, ToolchainSupport
from ivpm.handlers.package_handler_list import PackageHandlerList
from ivpm.handlers.handler_phases import HandlerPhase


class _Recorder(PackageHandler):
    """Minimal handler that records whether its root phase ran."""
    name = "recorder"
    phase = HandlerPhase.INTEGRATE

    ran = False   # class-level default: PackageHandler subclasses here are not
                  # themselves @dc.dataclass, so __post_init__ never fires.

    def on_root_post_load(self, update_info):
        self.ran = True


class _SupportedHandler(_Recorder):
    name = "supported-h"
    toolchain_support = ToolchainSupport.SUPPORTED


class _UnsupportedHandler(_Recorder):
    name = "unsupported-h"
    toolchain_support = ToolchainSupport.UNSUPPORTED


class TestProjectRootAccessors(unittest.TestCase):

    def test_workspace_mode_unchanged(self):
        ui = ProjectUpdateInfo(args=None, deps_dir="/proj/packages",
                               project_dir="/proj")
        self.assertEqual(ui.install_mode, InstallMode.WORKSPACE)
        self.assertEqual(ui.project_root(), "/proj")
        self.assertEqual(ui.project_root_or_none(), "/proj")

    def test_workspace_falls_back_to_deps_dir_parent(self):
        ui = ProjectUpdateInfo(args=None, deps_dir="/proj/packages")
        self.assertEqual(ui.project_root(), "/proj")

    def test_project_root_raises_in_toolchain(self):
        ui = ProjectUpdateInfo(args=None, deps_dir="/opt/eda",
                               install_mode=InstallMode.TOOLCHAIN)
        self.assertIsNone(ui.project_root_or_none())
        with self.assertRaises(ToolchainModeError):
            ui.project_root()

    def test_toolchain_ignores_a_stale_project_dir(self):
        """Even with project_dir populated, toolchain mode has no project."""
        ui = ProjectUpdateInfo(args=None, deps_dir="/opt/eda",
                               project_dir="/opt/eda",
                               install_mode=InstallMode.TOOLCHAIN)
        self.assertIsNone(ui.project_root_or_none())


class TestToolchainSupportDispatch(unittest.TestCase):

    def _dispatch(self, mode):
        sup, unsup = _SupportedHandler(), _UnsupportedHandler()
        lst = PackageHandlerList(handlers=[sup, unsup])
        ui = ProjectUpdateInfo(args=None, deps_dir="/opt/eda", install_mode=mode)
        with mock.patch("ivpm.utils.note") as note:
            lst.on_root_post_load(ui)
        return sup, unsup, note

    def test_both_run_in_workspace_mode(self):
        sup, unsup, _ = self._dispatch(InstallMode.WORKSPACE)
        self.assertTrue(sup.ran)
        self.assertTrue(unsup.ran)

    def test_unsupported_handler_skipped(self):
        sup, unsup, note = self._dispatch(InstallMode.TOOLCHAIN)
        self.assertTrue(sup.ran, "a SUPPORTED handler must still run")
        self.assertFalse(unsup.ran)
        # Never skip silently: a tool tree missing a handler's output with no
        # explanation is a support ticket.
        self.assertTrue(note.called)
        self.assertIn("unsupported-h", " ".join(
            str(a) for c in note.call_args_list for a in c.args))


class TestHandlerInfoReporting(unittest.TestCase):

    def test_default_handler_reports_supported(self):
        self.assertEqual(
            _SupportedHandler.handler_info().toolchain_support, "supported")

    def test_show_handler_reports_support(self):
        from ivpm.handlers.package_handler_agents import PackageHandlerAgents
        self.assertEqual(
            PackageHandlerAgents.handler_info().toolchain_support, "unsupported")


if __name__ == "__main__":
    unittest.main()
