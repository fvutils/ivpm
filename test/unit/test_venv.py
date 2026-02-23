"""
Tests for virtual environment creation behavior.

Verifies that:
- Default venv creation produces an isolated environment (no system-site-packages).
- Opt-in via py_system_site_packages=True produces a venv that includes system site-packages.
"""
import os
import configparser
from .test_base import TestBase


_SIMPLE_IVPM_YAML = """
package:
    name: venv_test
    dep-sets:
        - name: default-dev
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
"""


def _read_pyvenv_cfg(python_dir):
    """Parse pyvenv.cfg from a virtualenv directory."""
    cfg_path = os.path.join(python_dir, "pyvenv.cfg")
    cfg = configparser.ConfigParser()
    # pyvenv.cfg has no section header; add a fake one
    with open(cfg_path) as fp:
        content = "[root]\n" + fp.read()
    cfg.read_string(content)
    return dict(cfg["root"])


class TestVenv(TestBase):

    def _make_project(self):
        self.mkFile("ivpm.yaml", _SIMPLE_IVPM_YAML)

    def test_venv_isolated_by_default(self):
        """Default update must create an isolated venv (no system site-packages)."""
        self._make_project()

        class Args:
            py_uv = False
            py_pip = True          # force pip so we don't need uv
            py_system_site_packages = False
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        python_dir = os.path.join(self.testdir, "packages", "python")
        self.assertTrue(os.path.isdir(python_dir),
                        "packages/python directory should exist after update")

        cfg = _read_pyvenv_cfg(python_dir)
        value = cfg.get("include-system-site-packages", "false").strip().lower()
        self.assertEqual(value, "false",
                         "Default venv must NOT include system site-packages; "
                         "got include-system-site-packages = %r" % value)

    def test_venv_system_site_packages_opt_in(self):
        """Update with py_system_site_packages=True must create a venv that includes system site-packages."""
        self._make_project()

        class Args:
            py_uv = False
            py_pip = True          # force pip so we don't need uv
            py_system_site_packages = True
            anonymous_git = None
            log_level = "NONE"

        self.ivpm_update(skip_venv=False, args=Args())

        python_dir = os.path.join(self.testdir, "packages", "python")
        self.assertTrue(os.path.isdir(python_dir),
                        "packages/python directory should exist after update")

        cfg = _read_pyvenv_cfg(python_dir)
        value = cfg.get("include-system-site-packages", "false").strip().lower()
        self.assertEqual(value, "true",
                         "Opt-in venv MUST include system site-packages; "
                         "got include-system-site-packages = %r" % value)
