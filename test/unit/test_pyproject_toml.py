"""
Unit tests for src: pyproject.toml support in IVPM.

Covers:
  PT01  PackagePyprojectToml source type creation with correct defaults
  PT02  include: [optional-dependencies.dev] imports only that extra
  PT03  include: all imports runtime + all extras + all dep-groups
  PT04  Explicit src: pypi entry wins over same-name entry from file
  PT05  PEP 508 name extraction handles bare name, versioned, and extras forms
  PT06  Full PEP 508 specifier preserved verbatim in requirements output
  PT07  Missing/absent section is silent (empty list, no exception)
  PT08  Harvested entries appear in pypi_pkg_s after on_root_post_load
  PT09  PEP 735 dependency-groups entries are harvested
  PT10  PEP 735 include-group is expanded recursively
  PT11  Environment variable in url is expanded before file open
  PT12  Non-existent path triggers fatal()
  PT13  pyproject.toml is registered in PkgTypeRgy
"""

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.pkg_types.package_pyproject_toml import PackagePyprojectToml
from ivpm.pkg_types.package_pypi import PackagePyPi
from ivpm.handlers.package_handler_python import (
    PackageHandlerPython,
    _pep508_split,
    _extract_toml_section,
    _expand_dep_group,
    _resolve_pyproject_url,
)
from ivpm.project_ops_info import ProjectUpdateInfo

from .test_base import TestBase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _leaf1_path(data_dir):
    return os.path.join(data_dir, "pyproject_leaf1", "pyproject.toml")


def _leaf2_path(data_dir):
    return os.path.join(data_dir, "pyproject_leaf2", "pyproject.toml")


def _make_pkg(path_or_url, include=None, raw_url=False):
    """Build a PackagePyprojectToml for use in tests."""
    url = path_or_url if raw_url else "file://%s" % path_or_url
    opts = {"url": url}
    if include is not None:
        opts["include"] = include
    return PackagePyprojectToml.create("test_deps", opts, None)


def _make_handler():
    h = PackageHandlerPython()
    h.reset()
    return h


def _make_update_info(testdir):
    deps_dir = os.path.join(testdir, "packages")
    os.makedirs(deps_dir, exist_ok=True)
    args = MagicMock()
    args.suppress_output = True
    args.py_skip_install = True
    args.py_uv = False
    args.py_pip = False
    args.force_py_install = False
    return ProjectUpdateInfo(
        args=args,
        deps_dir=deps_dir,
        project_dir=testdir,
        suppress_output=True,
        skip_venv=True,
    )


# ---------------------------------------------------------------------------
# PT01 — Source type creation
# ---------------------------------------------------------------------------

class TestPT01SourceTypeCreation(TestBase):

    def test_PT01_source_type_created(self):
        """PT01: PackagePyprojectToml created with correct src_type and defaults."""
        opts = {"url": "file:///some/path/pyproject.toml"}
        pkg = PackagePyprojectToml.create("proj_deps", opts, None)
        self.assertIsInstance(pkg, PackagePyprojectToml)
        self.assertEqual(pkg.name, "proj_deps")
        self.assertEqual(pkg.src_type, "pyproject.toml")
        self.assertEqual(pkg.url, "file:///some/path/pyproject.toml")
        self.assertEqual(pkg.include, ["dependencies"])

    def test_PT01b_include_list_stored(self):
        """include: list is stored as-is."""
        opts = {"url": "file:///p/pyproject.toml",
                "include": ["dependencies", "optional-dependencies.dev"]}
        pkg = PackagePyprojectToml.create("deps", opts, None)
        self.assertEqual(pkg.include, ["dependencies", "optional-dependencies.dev"])

    def test_PT01c_include_string_wrapped(self):
        """include: single string is wrapped in a list."""
        opts = {"url": "file:///p/pyproject.toml", "include": "dependencies"}
        pkg = PackagePyprojectToml.create("deps", opts, None)
        self.assertEqual(pkg.include, ["dependencies"])


# ---------------------------------------------------------------------------
# PT02 — Single extra
# ---------------------------------------------------------------------------

class TestPT02SingleExtra(TestBase):

    def test_PT02_single_extra(self):
        """PT02: include: [optional-dependencies.dev] imports only dev extra."""
        pkg = _make_pkg(_leaf2_path(self.data_dir),
                        include=["optional-dependencies.dev"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        names = {p.name for p in result}
        self.assertIn("pytest", names)
        self.assertIn("pytest-cov", names)
        self.assertNotIn("httpx", names)       # runtime — not included
        self.assertNotIn("responses", names)   # test extra — not included
        self.assertNotIn("ruff", names)        # lint group — not included


# ---------------------------------------------------------------------------
# PT03 — include: all
# ---------------------------------------------------------------------------

class TestPT03IncludeAll(TestBase):

    def test_PT03_include_all(self):
        """PT03: include: all yields runtime + every extra + every dep-group."""
        pkg = _make_pkg(_leaf2_path(self.data_dir), include=["all"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        names = {p.name for p in result}
        self.assertIn("httpx", names)       # runtime
        self.assertIn("pytest", names)      # dev extra (also test extra, deduped)
        self.assertIn("pytest-cov", names)  # dev extra
        self.assertIn("responses", names)   # test extra
        self.assertIn("ruff", names)        # lint dep-group


# ---------------------------------------------------------------------------
# PT04 — Explicit src: pypi wins collision
# ---------------------------------------------------------------------------

class TestPT04ExplicitWins(TestBase):

    def test_PT04_explicit_pypi_wins_collision(self):
        """PT04: explicit src: pypi entry overrides same-name dep from pyproject.toml."""
        pkg = _make_pkg(_leaf1_path(self.data_dir), include=["dependencies"])
        handler = _make_handler()

        # Pre-load explicit 'requests' entry
        explicit = PackagePyPi("requests")
        explicit.src_type = "pypi"
        explicit.version = "==2.28.2"
        handler.pypi_pkg_s.add("requests")
        handler.pkgs_info["requests"] = explicit

        result = handler._harvest_pyproject_toml(pkg)
        harvested_names = [p.name for p in result]
        self.assertNotIn("requests", harvested_names)

        # Original explicit entry must be unchanged
        self.assertEqual(handler.pkgs_info["requests"].version, "==2.28.2")


# ---------------------------------------------------------------------------
# PT05 — _pep508_split
# ---------------------------------------------------------------------------

class TestPT05Pep508Split(unittest.TestCase):

    def test_PT05a_bare_name(self):
        self.assertEqual(_pep508_split("requests"), ("requests", ""))

    def test_PT05b_with_version(self):
        name, rem = _pep508_split("requests>=2.31")
        self.assertEqual(name, "requests")
        self.assertEqual(rem, ">=2.31")

    def test_PT05c_with_extras_and_version(self):
        name, rem = _pep508_split("mypackage[extra1,extra2]>=1.0")
        self.assertEqual(name, "mypackage")
        self.assertTrue(rem.startswith("[extra1,extra2]"))

    def test_PT05d_normalisation(self):
        """Hyphens, underscores, and dots fold to '-' (PEP 503)."""
        name, _ = _pep508_split("My_Package.lib>=1.0")
        self.assertEqual(name, "my-package-lib")

    def test_PT05e_with_marker(self):
        name, rem = _pep508_split("tomli>=2.0; python_version < '3.11'")
        self.assertEqual(name, "tomli")
        self.assertIn("python_version", rem)


# ---------------------------------------------------------------------------
# PT06 — Raw spec preserved verbatim
# ---------------------------------------------------------------------------

class TestPT06RawSpec(TestBase):

    def test_PT06_raw_spec_preserved(self):
        """PT06: _raw_spec attribute holds the full original PEP 508 string."""
        pkg = _make_pkg(_leaf1_path(self.data_dir), include=["dependencies"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        req_pkg = next(p for p in result if p.name == "requests")
        self.assertEqual(req_pkg._raw_spec, "requests>=2.31")

    def test_PT06b_raw_spec_written_to_requirements(self):
        """PT06b: _write_requirements_txt emits _raw_spec verbatim."""
        handler = _make_handler()
        p = PackagePyPi("mypackage")
        p.src_type = "pypi"
        p._raw_spec = "mypackage[extra]>=1.0; python_version>='3.8'"

        req_file = os.path.join(self.testdir, "req.txt")
        handler._write_requirements_txt(self.testdir, [p], req_file)
        content = open(req_file).read()
        self.assertIn("mypackage[extra]>=1.0; python_version>='3.8'", content)


# ---------------------------------------------------------------------------
# PT07 — Missing section is silent
# ---------------------------------------------------------------------------

class TestPT07MissingSection(TestBase):

    def test_PT07_missing_section_is_silent(self):
        """PT07: absent optional-dependencies section returns [] without error."""
        pkg = _make_pkg(_leaf1_path(self.data_dir),
                        include=["optional-dependencies.nonexistent"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        self.assertEqual(result, [])

    def test_PT07b_missing_dep_group_is_silent(self):
        """PT07b: absent dependency-groups section returns [] without error."""
        pkg = _make_pkg(_leaf1_path(self.data_dir),
                        include=["dependency-groups.nonexistent"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# PT08 — Harvested entries injected into pypi_pkg_s after on_root_post_load
# ---------------------------------------------------------------------------

class TestPT08InjectionOnRootPostLoad(TestBase):

    def test_PT08_harvested_entries_injected(self):
        """PT08: after on_root_post_load the harvested packages appear in pypi_pkg_s."""
        pkg = _make_pkg(_leaf1_path(self.data_dir), include=["dependencies"])
        handler = _make_handler()
        handler._pyproject_toml_pkgs.append(pkg)

        ui = _make_update_info(self.testdir)

        with patch.object(handler, "_install_requirements"):
            with patch("ivpm.handlers.package_handler_python.setup_venv"):
                handler.on_root_post_load(ui)

        self.assertIn("requests", handler.pypi_pkg_s)
        self.assertIn("click", handler.pypi_pkg_s)

    def test_PT08b_only_pyproject_entry_triggers_python(self):
        """PT08b: a project with only src: pyproject.toml still triggers Python install."""
        pkg = _make_pkg(_leaf1_path(self.data_dir), include=["dependencies"])
        handler = _make_handler()
        # No explicit pypi or src packages — only the virtual entry
        handler._pyproject_toml_pkgs.append(pkg)

        ui = _make_update_info(self.testdir)

        install_called = []

        def fake_install(*a, **kw):
            install_called.append(True)

        with patch("ivpm.handlers.package_handler_python.setup_venv"):
            with patch.object(handler, "_install_requirements", side_effect=fake_install):
                handler.on_root_post_load(ui)

        # The requirements file was written and install was attempted
        self.assertTrue(install_called or handler.pypi_pkg_s,
                        "Expected either install to be called or pypi_pkg_s to be populated")


# ---------------------------------------------------------------------------
# PT09 — PEP 735 dependency-groups
# ---------------------------------------------------------------------------

class TestPT09DepGroups(TestBase):

    def test_PT09_dep_group_harvested(self):
        """PT09: [dependency-groups.lint] entries are harvested."""
        pkg = _make_pkg(_leaf2_path(self.data_dir),
                        include=["dependency-groups.lint"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        names = {p.name for p in result}
        self.assertIn("ruff", names)


# ---------------------------------------------------------------------------
# PT10 — PEP 735 include-group expansion
# ---------------------------------------------------------------------------

class TestPT10IncludeGroupExpansion(TestBase):

    def test_PT10_include_group_expanded_recursively(self):
        """PT10: lint group pulls in dev group transitively via include-group."""
        pkg = _make_pkg(_leaf2_path(self.data_dir),
                        include=["dependency-groups.lint"])
        handler = _make_handler()
        result = handler._harvest_pyproject_toml(pkg)
        names = {p.name for p in result}
        # ruff is direct in lint; pytest comes via include-group = "dev"
        self.assertIn("ruff", names)
        self.assertIn("pytest", names)
        self.assertIn("pytest-cov", names)

    def test_PT10b_circular_include_group_safe(self):
        """PT10b: circular include-group references do not cause infinite recursion."""
        groups = {
            "a": ["pkg-a", {"include-group": "b"}],
            "b": ["pkg-b", {"include-group": "a"}],
        }
        result = _expand_dep_group(groups, "a", frozenset())
        names = set(result)
        self.assertIn("pkg-a", names)
        self.assertIn("pkg-b", names)


# ---------------------------------------------------------------------------
# PT11 — Environment variable in URL
# ---------------------------------------------------------------------------

class TestPT11EnvVarUrl(TestBase):

    def test_PT11_env_var_in_url_expanded(self):
        """PT11: ${MY_ROOT} in url is expanded before opening the file."""
        data_dir = os.path.join(self.data_dir, "pyproject_leaf1")
        os.environ["IVPM_TEST_ROOT"] = data_dir
        try:
            pkg = _make_pkg(
                "file://${IVPM_TEST_ROOT}/pyproject.toml",
                include=["dependencies"],
                raw_url=True,
            )
            handler = _make_handler()
            result = handler._harvest_pyproject_toml(pkg)
            self.assertTrue(len(result) > 0)
            names = {p.name for p in result}
            self.assertIn("requests", names)
        finally:
            del os.environ["IVPM_TEST_ROOT"]


# ---------------------------------------------------------------------------
# PT12 — Missing file triggers fatal
# ---------------------------------------------------------------------------

class TestPT12MissingFile(TestBase):

    def test_PT12_missing_file_calls_fatal(self):
        """PT12: non-existent path triggers fatal() with a descriptive message."""
        pkg = _make_pkg("file:///nonexistent/does_not_exist/pyproject.toml")
        handler = _make_handler()

        with patch("ivpm.handlers.package_handler_python.fatal") as mock_fatal:
            mock_fatal.side_effect = SystemExit(1)
            with self.assertRaises(SystemExit):
                handler._harvest_pyproject_toml(pkg)
        mock_fatal.assert_called_once()
        msg = mock_fatal.call_args[0][0]
        self.assertIn("not found", msg)


# ---------------------------------------------------------------------------
# PT13 — Registered in PkgTypeRgy
# ---------------------------------------------------------------------------

class TestPT13Registered(unittest.TestCase):

    def test_PT13_pyproject_toml_registered(self):
        """PT13: 'pyproject.toml' source type is registered in PkgTypeRgy."""
        # Reset the singleton so the registration is re-run cleanly
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        PkgTypeRgy._inst = None
        rgy = PkgTypeRgy.inst()
        self.assertTrue(rgy.hasPkgType("pyproject.toml"))
        # Clean up so other tests aren't affected
        PkgTypeRgy._inst = None
