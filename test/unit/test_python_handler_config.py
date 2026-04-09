"""
Tests for the lazy venv creation and python handler configuration.

Covers:
- TestPythonConfigYaml   : parsing of ``package.with.python:`` from ivpm.yaml
- TestVenvModeResolution : _resolve_venv_mode() priority logic
- TestLazyVenvCreation   : integration — venv only created when Python pkgs are present
- TestIvpmAutoInjection  : ivpm is always injected when handler fires
"""
import os
import sys
from unittest.mock import MagicMock, patch
from .test_base import TestBase


# ---------------------------------------------------------------------------
# YAML parsing tests
# ---------------------------------------------------------------------------

class TestPythonConfigYaml(TestBase):
    """Verify package.with.python is correctly parsed into PythonConfig."""

    def _load_proj(self, yaml_text):
        from ivpm.ivpm_yaml_reader import IvpmYamlReader
        import io
        fp = io.StringIO(yaml_text)
        return IvpmYamlReader().read(fp, "test_yaml")

    def test_default_python_config(self):
        """A project without with.python leaves python_config as None."""
        proj = self._load_proj("""
package:
    name: test_pkg
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertIsNone(proj.python_config)

    def test_venv_false(self):
        from ivpm.proj_info import VenvMode
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
            venv: false
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertEqual(proj.python_config.venv, VenvMode.SKIP)

    def test_venv_true(self):
        from ivpm.proj_info import VenvMode
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
            venv: true
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertEqual(proj.python_config.venv, VenvMode.AUTO)

    def test_venv_uv(self):
        from ivpm.proj_info import VenvMode
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
            venv: uv
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertEqual(proj.python_config.venv, VenvMode.UV)

    def test_venv_pip(self):
        from ivpm.proj_info import VenvMode
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
            venv: pip
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertEqual(proj.python_config.venv, VenvMode.PIP)

    def test_system_site_packages(self):
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
            system-site-packages: true
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertTrue(proj.python_config.system_site_packages)

    def test_pre_release(self):
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
            pre-release: true
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertTrue(proj.python_config.pre_release)

    def test_unknown_with_key_is_fatal(self):
        """Unknown keys under package.with must cause a fatal error."""
        with self.assertRaises(Exception):
            self._load_proj("""
package:
    name: test_pkg
    with:
        unknown_key: {}
    dep-sets:
        - name: default-dev
          deps: []
""")

    def test_empty_python_section(self):
        """An empty python: section uses all defaults."""
        from ivpm.proj_info import VenvMode
        proj = self._load_proj("""
package:
    name: test_pkg
    with:
        python:
    dep-sets:
        - name: default-dev
          deps: []
""")
        self.assertEqual(proj.python_config.venv, VenvMode.AUTO)


# ---------------------------------------------------------------------------
# VenvMode resolution tests
# ---------------------------------------------------------------------------

class TestVenvModeResolution(TestBase):
    """Verify _resolve_venv_mode() priority logic."""

    def _make_handler(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        h = PackageHandlerPython()
        return h

    def _make_update_info(self, args=None, skip_venv=False, python_config=None):
        from ivpm.project_ops_info import ProjectUpdateInfo
        ui = ProjectUpdateInfo(args, "/tmp/deps",
                               skip_venv=skip_venv,
                               python_config=python_config)
        return ui

    def test_skip_py_install_wins(self):
        from ivpm.proj_info import VenvMode
        args = MagicMock()
        args.py_skip_install = True
        args.py_uv = False
        args.py_pip = False
        ui = self._make_update_info(args=args)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.SKIP)

    def test_skip_venv_flag(self):
        from ivpm.proj_info import VenvMode
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = False
        args.py_pip = False
        ui = self._make_update_info(args=args, skip_venv=True)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.SKIP)

    def test_cli_uv_wins_over_yaml_pip(self):
        """CLI --py-uv overrides yaml venv: pip."""
        from ivpm.proj_info import VenvMode, PythonConfig
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = True
        args.py_pip = False
        cfg = PythonConfig(venv=VenvMode.PIP)
        ui = self._make_update_info(args=args, python_config=cfg)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.UV)

    def test_cli_pip_wins_over_yaml_uv(self):
        """CLI --py-pip overrides yaml venv: uv."""
        from ivpm.proj_info import VenvMode, PythonConfig
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = False
        args.py_pip = True
        cfg = PythonConfig(venv=VenvMode.UV)
        ui = self._make_update_info(args=args, python_config=cfg)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.PIP)

    def test_yaml_skip_blocks_cli_pip(self):
        """yaml venv: false cannot be overridden by --py-pip."""
        from ivpm.proj_info import VenvMode, PythonConfig
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = False
        args.py_pip = True
        cfg = PythonConfig(venv=VenvMode.SKIP)
        ui = self._make_update_info(args=args, python_config=cfg)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.SKIP)

    def test_yaml_venv_used_when_no_cli_override(self):
        from ivpm.proj_info import VenvMode, PythonConfig
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = False
        args.py_pip = False
        cfg = PythonConfig(venv=VenvMode.UV)
        ui = self._make_update_info(args=args, python_config=cfg)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.UV)

    def test_default_is_auto(self):
        from ivpm.proj_info import VenvMode
        args = MagicMock()
        args.py_skip_install = False
        args.py_uv = False
        args.py_pip = False
        ui = self._make_update_info(args=args)
        h = self._make_handler()
        self.assertEqual(h._resolve_venv_mode(ui), VenvMode.AUTO)


# ---------------------------------------------------------------------------
# Lazy venv creation integration tests
# ---------------------------------------------------------------------------

_YAML_NO_PYTHON = """
package:
    name: no_python_project
    dep-sets:
        - name: default-dev
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
"""

_YAML_WITH_PYPI = """
package:
    name: pypi_project
    dep-sets:
        - name: default-dev
          deps:
            - name: pyyaml
              pypi: true
"""

_YAML_VENV_SKIP = """
package:
    name: skip_project
    with:
        python:
            venv: false
    dep-sets:
        - name: default-dev
          deps:
            - name: pyyaml
              pypi: true
"""


class TestLazyVenvCreation(TestBase):
    """Integration tests: venv is only created when Python packages are present."""

    def test_no_venv_when_no_python_packages(self):
        """A project with only dir-type deps must NOT create a venv."""
        self.mkFile("ivpm.yaml", _YAML_NO_PYTHON)

        class Args:
            py_uv = False
            py_pip = True
            py_system_site_packages = False
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        python_dir = os.path.join(self.testdir, "packages", "python")
        self.assertFalse(os.path.isdir(python_dir),
                         "No venv should be created for a project with no Python packages")

    def test_venv_created_for_pypi_package(self):
        """A project with a pypi dep MUST create a venv."""
        self.mkFile("ivpm.yaml", _YAML_WITH_PYPI)

        class Args:
            py_uv = False
            py_pip = True
            py_system_site_packages = False
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        python_dir = os.path.join(self.testdir, "packages", "python")
        self.assertTrue(os.path.isdir(python_dir),
                        "A venv must be created when a pypi package is present")

    def test_venv_skip_via_yaml(self):
        """venv: false in with.python suppresses venv creation even with pypi deps."""
        self.mkFile("ivpm.yaml", _YAML_VENV_SKIP)

        class Args:
            py_uv = False
            py_pip = True
            py_system_site_packages = False
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        python_dir = os.path.join(self.testdir, "packages", "python")
        self.assertFalse(os.path.isdir(python_dir),
                         "venv: false in yaml must suppress venv creation")


# ---------------------------------------------------------------------------
# ivpm auto-injection tests
# ---------------------------------------------------------------------------

class TestIvpmAutoInjection(TestBase):
    """Verify ivpm is auto-injected when the python handler fires."""

    _YAML_NO_IVPM = """
package:
    name: no_ivpm_project
    dep-sets:
        - name: default-dev
          deps:
            - name: pyyaml
              pypi: true
"""

    _YAML_WITH_IVPM = """
package:
    name: with_ivpm_project
    dep-sets:
        - name: default-dev
          deps:
            - name: pyyaml
              pypi: true
            - name: ivpm
              pypi: true
              version: ">=2"
"""

    def test_ivpm_injected_when_absent(self):
        """ivpm is added to requirements when not explicitly specified."""
        self.mkFile("ivpm.yaml", self._YAML_NO_IVPM)

        class Args:
            py_uv = False
            py_pip = True
            py_system_site_packages = False
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        python_dir = os.path.join(self.testdir, "packages", "python")
        # Verify ivpm ended up in the venv
        self.assertTrue(os.path.isdir(python_dir))
        # Check at least one requirements file mentions ivpm
        reqs = [f for f in os.listdir(self.testdir + "/packages")
                if f.startswith("python_pkgs_")]
        found_ivpm = False
        for r in reqs:
            with open(os.path.join(self.testdir, "packages", r)) as fp:
                if "ivpm" in fp.read():
                    found_ivpm = True
                    break
        self.assertTrue(found_ivpm, "ivpm must appear in a requirements file")

    def test_ivpm_not_duplicated_when_explicit(self):
        """When ivpm is explicitly listed, it must not appear twice."""
        self.mkFile("ivpm.yaml", self._YAML_WITH_IVPM)

        class Args:
            py_uv = False
            py_pip = True
            py_system_site_packages = False
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        # Collect all ivpm mentions across all requirements files
        pkg_dir = os.path.join(self.testdir, "packages")
        ivpm_count = 0
        for fname in os.listdir(pkg_dir):
            if fname.startswith("python_pkgs_"):
                with open(os.path.join(pkg_dir, fname)) as fp:
                    for line in fp:
                        if line.strip().startswith("ivpm"):
                            ivpm_count += 1
        self.assertEqual(ivpm_count, 1, "ivpm must appear exactly once across requirements files")
