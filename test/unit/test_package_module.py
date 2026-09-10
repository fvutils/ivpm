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
from ivpm.yamlsrc.srcinfo import SrcInfo


class FakeModulesInterface:
    """Stub that returns canned modulefile paths without calling subprocesses."""

    def __init__(self, paths=None, show_output=""):
        self._paths = paths or {}
        self._show_output = show_output
        self.variant = ModulesVariant.MODULES_4X
        self.show_calls = []

    def module_path(self, module):
        return self._paths.get(module)

    def is_avail(self, module):
        return module in self._paths

    def module_show(self, module):
        self.show_calls.append(module)
        return self._show_output


class ExplodingModulesInterface:
    """Stub that fails any use -- asserts the modules system is not consulted.

    Stands in for "this machine has no Modules/Lmod installation at all",
    which is a supported configuration for the ``modulefile:`` form.
    """

    variant = ModulesVariant.UNKNOWN

    def module_path(self, module):
        raise AssertionError(
            "module_path(%r) called; modulefile: must not consult the "
            "modules system" % module)

    def is_avail(self, module):
        raise AssertionError("is_avail(%r) called" % module)

    def module_show(self, module):
        raise ModulesError("no modules installation")


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


class TestPackageModulefileCreate(unittest.TestCase):
    """Key validation for the 'modulefile:' form (no filesystem access)."""

    def test_module_form_is_not_modulefile(self):
        """Regression guard: module: stays logical, modulefile_spec unset."""
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        self.assertFalse(pkg.is_modulefile)
        self.assertIsNone(pkg.modulefile_spec)
        self.assertEqual(pkg.load_spec, "gcc/15.2.0")

    def test_modulefile_form(self):
        pkg = PackageModule.create("mytool", {"modulefile": "etc/mf/tool"}, None)
        self.assertTrue(pkg.is_modulefile)
        self.assertEqual(pkg.modulefile_spec, "etc/mf/tool")
        self.assertIsNone(pkg.module)
        self.assertEqual(pkg.src_type, "module")

    def test_module_and_modulefile_conflict(self):
        with self.assertRaises(Exception) as ctx:
            PackageModule.create(
                "mytool", {"module": "mytool/1.0", "modulefile": "etc/mf/tool"}, None)
        self.assertIn("not both", str(ctx.exception))

    def test_version_and_modulefile_conflict(self):
        with self.assertRaises(Exception) as ctx:
            PackageModule.create(
                "mytool", {"version": "1.0", "modulefile": "etc/mf/tool"}, None)
        self.assertIn("not both", str(ctx.exception))

    def test_none_of_the_three(self):
        with self.assertRaises(Exception) as ctx:
            PackageModule.create("mytool", {}, None)
        self.assertIn("modulefile", str(ctx.exception))

    def test_modulefile_accepted_as_dep_key(self):
        self.assertIn("modulefile", PackageModule.dep_keys())


class TestPackageModulefileUpdate(TestBase):
    """Resolution of the 'modulefile:' form during update()."""

    def _make_update_info(self, mi=None):
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        class FakeArgs:
            anonymous_git = None
        ui = ProjectUpdateInfo(args=FakeArgs(), deps_dir=deps_dir)
        # Default to an interface that fails if touched: the modulefile: form
        # must resolve without any modules installation.
        ui.modules_interface = mi if mi is not None else ExplodingModulesInterface()
        return ui

    def _mkpkg(self, opts, decl_dir=None):
        """Create a PackageModule declared by an ivpm.yaml in *decl_dir*."""
        si = None
        if decl_dir is not None:
            si = SrcInfo(filename=os.path.join(decl_dir, "ivpm.yaml"),
                         lineno=1, linepos=1)
        return PackageModule.create("mytool", opts, si)

    @property
    def _mf_dir(self):
        return os.path.join(self.data_dir, "modulefiles")

    def test_relative_resolves_against_declaring_yaml(self):
        """A relative modulefile: is relative to the ivpm.yaml, not to cwd."""
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0"},
                          decl_dir=self.data_dir)
        cwd = os.getcwd()
        os.chdir(self.testdir)   # deliberately not the declaring directory
        try:
            pkg.update(self._make_update_info())
        finally:
            os.chdir(cwd)

        self.assertEqual(pkg.modulefile_path,
                         os.path.join(self._mf_dir, "tool1", "1.0"))

    def test_absolute_path(self):
        mf = os.path.join(self._mf_dir, "tool2")
        pkg = self._mkpkg({"modulefile": mf})
        pkg.update(self._make_update_info())
        self.assertEqual(pkg.modulefile_path, mf)

    def test_env_var_expansion(self):
        os.environ["MF_ROOT"] = self._mf_dir
        try:
            pkg = self._mkpkg({"modulefile": "$MF_ROOT/tool1/1.0"})
            pkg.update(self._make_update_info())
        finally:
            del os.environ["MF_ROOT"]
        self.assertEqual(pkg.modulefile_path,
                         os.path.join(self._mf_dir, "tool1", "1.0"))

    def test_tilde_expansion(self):
        """~ expands to the home directory (path need not exist to check it)."""
        pkg = self._mkpkg({"modulefile": "~/no-such-modulefile"})
        with self.assertRaises(Exception) as ctx:
            pkg.update(self._make_update_info())
        self.assertIn(os.path.expanduser("~"), str(ctx.exception))

    def test_missing_file_is_fatal(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/nope/1.0"},
                          decl_dir=self.data_dir)
        with self.assertRaises(Exception) as ctx:
            pkg.update(self._make_update_info())
        msg = str(ctx.exception)
        self.assertIn("does not exist", msg)
        self.assertIn("modulefiles/nope/1.0", msg)

    def test_directory_is_fatal_with_distinct_message(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1"},
                          decl_dir=self.data_dir)
        with self.assertRaises(Exception) as ctx:
            pkg.update(self._make_update_info())
        msg = str(ctx.exception)
        self.assertIn("directory", msg)
        self.assertIn("module:", msg)

    def test_modulefile_path_is_absolute(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0"},
                          decl_dir=self.data_dir)
        pkg.update(self._make_update_info())
        self.assertTrue(os.path.isabs(pkg.modulefile_path))

    def test_default_root_is_modulefile_dir(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0"},
                          decl_dir=self.data_dir)
        pkg.update(self._make_update_info())
        self.assertEqual(pkg.path, os.path.join(self._mf_dir, "tool1"))
        self.assertEqual(pkg.module_root, pkg.path)

    def test_root_override_wins(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0",
                           "root": self.data_dir},
                          decl_dir=self.data_dir)
        pkg.update(self._make_update_info())
        self.assertEqual(pkg.path, self.data_dir)

    def test_resolve_root_shows_the_absolute_path(self):
        """resolve-root: true runs 'module show <abs path>', not <name>."""
        leaf_dir = os.path.join(self.data_dir, "module_leaf1")
        fake_mi = FakeModulesInterface(show_output="setenv TOOL1_HOME %s" % leaf_dir)
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0",
                           "resolve-root": True},
                          decl_dir=self.data_dir)
        pkg.update(self._make_update_info(fake_mi))

        self.assertEqual(pkg.path, leaf_dir)
        self.assertEqual(fake_mi.show_calls,
                         [os.path.join(self._mf_dir, "tool1", "1.0")])

    def test_resolve_root_falls_back_when_no_modules_installed(self):
        """ModulesError from module show -> fall back to the modulefile dir."""
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0",
                           "resolve-root": True},
                          decl_dir=self.data_dir)
        # ExplodingModulesInterface.module_show raises ModulesError
        pkg.update(self._make_update_info())
        self.assertEqual(pkg.path, os.path.join(self._mf_dir, "tool1"))

    def test_resolves_without_a_modules_system(self):
        """The capability claim: no modules installation is consulted at all."""
        pkg = self._mkpkg({"modulefile": "modulefiles/tool2"},
                          decl_dir=self.data_dir)
        # ExplodingModulesInterface raises AssertionError on module_path()
        pkg.update(self._make_update_info())
        self.assertEqual(pkg.path, self._mf_dir)

    def test_type_data_carries_modulefile(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0"},
                          decl_dir=self.data_dir)
        pkg.update(self._make_update_info())
        td = [t for t in pkg.type_data if isinstance(t, ModuleTypeData)][0]
        self.assertEqual(td.modulefile,
                         os.path.join(self._mf_dir, "tool1", "1.0"))
        self.assertIsNone(td.module)

    def test_logical_form_leaves_type_data_modulefile_unset(self):
        """Regression guard: the logical form sets module, never modulefile."""
        mf_path = os.path.join(self.data_dir, "module_leaf1", "ivpm.yaml")
        ui = self._make_update_info(FakeModulesInterface(paths={"gcc/15.2.0": mf_path}))
        pkg = PackageModule.create("gcc", {"module": "gcc/15.2.0"}, None)
        pkg.update(ui)
        td = [t for t in pkg.type_data if isinstance(t, ModuleTypeData)][0]
        self.assertEqual(td.module, "gcc/15.2.0")
        self.assertIsNone(td.modulefile)

    def test_load_spec_is_the_resolved_path(self):
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0"},
                          decl_dir=self.data_dir)
        pkg.update(self._make_update_info())
        self.assertEqual(pkg.load_spec, pkg.modulefile_path)

    def test_sync_returns_skipped(self):
        from ivpm.pkg_sync import SyncOutcome
        pkg = self._mkpkg({"modulefile": "modulefiles/tool1/1.0"},
                          decl_dir=self.data_dir)
        result = pkg.sync(None)
        self.assertEqual(result.outcome, SyncOutcome.SKIPPED)
