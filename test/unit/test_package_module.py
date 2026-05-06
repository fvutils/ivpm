"""
Unit tests for PackageModule (src: module).

These tests use a FakeModulesInterface stub that returns canned
modulefile paths without calling real subprocesses.
"""
import os
import unittest
from unittest import mock

from .test_base import TestBase
from ivpm.modules_interface import ModulesInterface, ModulesVariant, ModulesError
from ivpm.pkg_types.package_module import PackageModule, _get_modules_interface
from ivpm.pkg_content_type import ModuleTypeData
from ivpm.project_ops_info import ProjectUpdateInfo


class FakeModulesInterface:
    """Stub that returns canned modulefile paths without calling subprocesses."""

    def __init__(self, paths=None, show_output=""):
        self._paths = paths or {}
        self._show_output = show_output
        self.variant = ModulesVariant.MODULES_4X

    def module_path(self, module):
        return self._paths.get(module)

    def is_avail(self, module):
        return module in self._paths

    def module_show(self, module):
        return self._show_output


class TestPackageModuleCreate(unittest.TestCase):
    """Tests for PackageModule.create() and process_options()."""

    def test_create_from_options(self):
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        self.assertEqual(pkg.name, "gcc")
        self.assertEqual(pkg.module, "gcc/15.2.0")
        self.assertEqual(pkg.src_type, "module")

    def test_missing_module_and_version(self):
        """create() without module: or version: -> fatal()."""
        with self.assertRaises(Exception):
            PackageModule.create("gcc", {}, None)

    def test_version_derives_module_specifier(self):
        """version: 2024.09 on name: vcs -> module specifier vcs/2024.09."""
        pkg = PackageModule.create("vcs", {"version": "2024.09"}, None)
        self.assertEqual(pkg.module, "vcs/2024.09")
        self.assertEqual(pkg.src_type, "module")

    def test_explicit_module_overrides_version(self):
        """module: overrides name/version derivation."""
        pkg = PackageModule.create("vcs", {"module": "vcs-tool/2024.09", "version": "2024.09"}, None)
        self.assertEqual(pkg.module, "vcs-tool/2024.09")

    def test_root_override(self):
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0", "root": "/custom/path"}, None)
        self.assertEqual(pkg.root_override, "/custom/path")

    def test_resolve_root_flag(self):
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0", "resolve-root": True}, None)
        self.assertTrue(pkg.resolve_root)

    def test_src_type_is_module(self):
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        self.assertEqual(pkg.src_type, "module")

    def test_implicit_module_type_data(self):
        """No explicit type: -> ModuleTypeData auto-added to type_data."""
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        self.assertTrue(any(isinstance(td, ModuleTypeData) for td in pkg.type_data))
        td = [t for t in pkg.type_data if isinstance(t, ModuleTypeData)][0]
        self.assertEqual(td.module, "gcc/15.2.0")
        self.assertTrue(td.load)


class TestPackageModuleUpdate(TestBase):
    """Tests for PackageModule.update() with a FakeModulesInterface."""

    def _make_update_info(self, fake_mi):
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        class FakeArgs:
            anonymous_git = None
        ui = ProjectUpdateInfo(args=FakeArgs(), deps_dir=deps_dir)
        ui.modules_interface = fake_mi
        return ui

    def test_update_sets_path_to_modulefile_dir(self):
        """update() sets pkg.path to the modulefile's parent directory (default)."""
        mf_path = os.path.join(self.data_dir, "module_leaf1", "ivpm.yaml")
        fake_mi = FakeModulesInterface(paths={"gcc/15.2.0": mf_path})
        ui = self._make_update_info(fake_mi)

        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        pkg.update(ui)

        self.assertEqual(pkg.path, os.path.dirname(mf_path))
        self.assertEqual(pkg.module_root, os.path.dirname(mf_path))
        self.assertEqual(pkg.modulefile_path, mf_path)

    def test_update_with_root_override(self):
        """root: /custom/path -> pkg.path set to override, not modulefile dir."""
        mf_path = os.path.join(self.data_dir, "module_leaf1", "ivpm.yaml")
        custom_root = self.data_dir
        fake_mi = FakeModulesInterface(paths={"gcc/15.2.0": mf_path})
        ui = self._make_update_info(fake_mi)

        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0", "root": custom_root}, None)
        pkg.update(ui)

        self.assertEqual(pkg.path, custom_root)

    def test_update_with_root_override_env_expansion(self):
        """root: $TOOL_ROOT/gcc -> env var expanded."""
        mf_path = os.path.join(self.data_dir, "module_leaf1", "ivpm.yaml")
        fake_mi = FakeModulesInterface(paths={"gcc/15.2.0": mf_path})
        ui = self._make_update_info(fake_mi)

        os.environ["TOOL_ROOT"] = self.data_dir
        try:
            pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0", "root": "$TOOL_ROOT"}, None)
            pkg.update(ui)
            self.assertEqual(pkg.path, self.data_dir)
        finally:
            del os.environ["TOOL_ROOT"]

    def test_update_loads_proj_info(self):
        """Root dir contains ivpm.yaml -> ProjInfo loaded and returned."""
        leaf_dir = os.path.join(self.data_dir, "module_leaf1")
        mf_path = os.path.join(leaf_dir, "ivpm.yaml")
        fake_mi = FakeModulesInterface(paths={"gcc/15.2.0": mf_path})
        ui = self._make_update_info(fake_mi)

        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        proj_info = pkg.update(ui)

        # module_leaf1 has ivpm.yaml, so ProjInfo should be returned
        self.assertIsNotNone(proj_info)

    def test_update_no_ivpm_yaml(self):
        """Root dir has no ivpm.yaml -> returns None (no error)."""
        no_ivpm_dir = os.path.join(self.data_dir, "module_no_ivpm")
        mf_path = os.path.join(no_ivpm_dir, "README.md")
        fake_mi = FakeModulesInterface(paths={"tool/1.0": mf_path})
        ui = self._make_update_info(fake_mi)

        pkg = PackageModule.create("tool", {"module": "tool/1.0"}, None)
        proj_info = pkg.update(ui)

        self.assertIsNone(proj_info)

    def test_update_module_not_available(self):
        """module_path() returns None -> fatal() with clear message."""
        fake_mi = FakeModulesInterface(paths={})
        ui = self._make_update_info(fake_mi)

        pkg = PackageModule.create("gcc", {"module": "nonexistent/1.0"}, None)
        with self.assertRaises(Exception) as ctx:
            pkg.update(ui)
        self.assertIn("nonexistent/1.0", str(ctx.exception))

    def test_sync_returns_skipped(self):
        """sync() returns SKIPPED with 'environment module' reason."""
        from ivpm.pkg_sync import SyncOutcome
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        result = pkg.sync(None)
        self.assertEqual(result.outcome, SyncOutcome.SKIPPED)
        self.assertIn("environment module", result.skipped_reason)


class TestPackageModuleResolveRoot(TestBase):
    """Tests for _resolve_root_from_show()."""

    def _make_update_info(self, fake_mi):
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        class FakeArgs:
            anonymous_git = None
        ui = ProjectUpdateInfo(args=FakeArgs(), deps_dir=deps_dir)
        ui.modules_interface = fake_mi
        return ui

    def test_resolve_root_setenv_home(self):
        """resolve-root: true with setenv *_HOME -> uses that path."""
        leaf_dir = os.path.join(self.data_dir, "module_leaf1")
        mf_path = os.path.join(leaf_dir, "ivpm.yaml")
        show_output = "setenv GCC_HOME %s\nprepend-path PATH %s/bin" % (leaf_dir, leaf_dir)
        fake_mi = FakeModulesInterface(
            paths={"gcc/15.2.0": mf_path},
            show_output=show_output)
        ui = self._make_update_info(fake_mi)

        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0", "resolve-root": True}, None)
        pkg.update(ui)

        self.assertEqual(pkg.path, leaf_dir)
