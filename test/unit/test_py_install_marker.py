"""
Tests for the Python handler's "install completed" marker.

The handler used to skip installing whenever python_pkgs_1.txt existed. That
file is written before the installer runs, so a failed install made every
later 'ivpm update' report success while installing nothing.

Coverage:
  IM01  a completed install writes the marker; an identical rerun skips
  IM02  a failed install leaves no marker, and the next run retries
  IM03  a marker from an earlier install is removed when a new one fails
  IM04  changed requirements are installed despite a marker
  IM05  requirements files left by a failed run do not count as installed
  IM06  url: null in a pyproject.toml / package.json entry is a located error
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from ivpm.handlers.package_handler_python import (
    PackageHandlerPython, _INSTALL_MARKER, _resolve_pyproject_url)
from ivpm.diagnostics import SrcLoaderError
from ivpm.package import Package
from ivpm.project_ops_info import ProjectUpdateInfo

from .test_base import TestBase


class _InstallFailed(Exception):
    pass


class TestInstallMarker(TestBase):

    def setUp(self):
        super().setUp()
        self.deps_dir = os.path.join(self.testdir, "packages")
        self.python_dir = os.path.join(self.deps_dir, "python")
        os.makedirs(self.python_dir, exist_ok=True)
        self.marker = os.path.join(self.python_dir, _INSTALL_MARKER)

    def _run(self, pkgs=("somepkg",), fail=False):
        """Run on_root_post_load with the installer stubbed; return the
        requirements files it was asked to install."""
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = True
        args.py_pip = False
        args.py_prerls_packages = False
        args.py_system_site_packages = False
        ui = ProjectUpdateInfo(args=args, deps_dir=self.deps_dir,
                               project_dir=self.testdir, suppress_output=True)
        ui.force_py_install = False

        handler = PackageHandlerPython()
        handler.reset()
        for name in pkgs:
            p = Package(name)
            p.src_type = "pypi"
            p.version = None
            handler.pypi_pkg_s.add(name)
            handler.pkgs_info[name] = p

        calls = []

        def fake_install(python_dir, reqfile, *a, **kw):
            calls.append(reqfile)
            if fail:
                raise _InstallFailed(reqfile)

        with patch("ivpm.handlers.package_handler_python.setup_venv"), \
             patch.object(handler, "_push_entrypoint_agent_dirs"), \
             patch.object(handler, "_verify_installed"), \
             patch.object(handler, "_install_requirements",
                          side_effect=fake_install):
            handler.on_root_post_load(ui)
        return calls

    def test_IM01_completed_install_is_not_repeated(self):
        self.assertTrue(self._run())
        self.assertTrue(os.path.isfile(self.marker))
        self.assertEqual([], self._run())

    def test_IM02_failed_install_is_retried(self):
        with self.assertRaises(_InstallFailed):
            self._run(fail=True)
        self.assertFalse(os.path.isfile(self.marker))
        self.assertTrue(self._run(), "the next run must retry the install")
        self.assertTrue(os.path.isfile(self.marker))

    def test_IM03_failure_removes_an_earlier_marker(self):
        self._run()
        self.assertTrue(os.path.isfile(self.marker))
        with self.assertRaises(_InstallFailed):
            self._run(pkgs=("somepkg", "otherpkg"), fail=True)
        self.assertFalse(os.path.isfile(self.marker))

    def test_IM04_changed_requirements_are_installed(self):
        self._run()
        self.assertTrue(self._run(pkgs=("somepkg", "otherpkg")))

    def test_IM05_requirements_files_alone_do_not_skip(self):
        """The old check: python_pkgs_1.txt present meant 'installed'."""
        with open(os.path.join(self.deps_dir, "python_pkgs_1.txt"), "w") as fp:
            fp.write("somepkg\n")
        self.assertTrue(self._run())


class TestNullUrl(TestBase):

    def test_IM06a_pyproject_null_url(self):
        from ivpm.pkg_types.package_pyproject_toml import PackagePyprojectToml
        with self.assertRaises(SrcLoaderError) as ctx:
            PackagePyprojectToml.create("p", {"url": None}, None)
        self.assertIn("'url' must be a string, not null", str(ctx.exception))

    def test_IM06b_packagejson_null_url(self):
        from ivpm.pkg_types.package_packagejson import PackagePackageJson
        with self.assertRaises(SrcLoaderError) as ctx:
            PackagePackageJson.create("p", {"url": None}, None)
        self.assertIn("'url' must be a string, not null", str(ctx.exception))

    def test_IM06c_resolve_rejects_none(self):
        with self.assertRaises(ValueError):
            _resolve_pyproject_url(None)


if __name__ == "__main__":
    unittest.main()
