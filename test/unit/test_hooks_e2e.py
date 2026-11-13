"""
End-to-end tests for project hooks: a real 'ivpm update' (venv creation and
package installation) followed by the post-update hooks.

Parsing and the runner in isolation are covered by test_hooks.py.
"""
import os
import shutil
from .test_base import TestBase


_PYPROJECT = """\
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "demo"
version = "0.1"

[tool.setuptools.packages.find]
where = ["src"]
"""

_EGG_INFO_YAML = """\
package:
  name: demo
  dep-sets:
    - name: default-dev
      deps:
        - name: setuptools
          src: pypi
  hooks:
    - name: egg-info
      event: post-update
      python: |
        from setuptools import setup
        setup(script_args=["egg_info", "-e", "src"])
"""


class _Args:
    py_uv = False
    py_pip = False
    py_system_site_packages = False
    anonymous_git = None
    log_level = "NONE"
    no_hooks = False


class TestHooksE2E(TestBase):

    def _marker_hook(self, marker, extra=""):
        return (
            "package:\n"
            "  name: demo\n"
            "  dep-sets:\n"
            "    - name: default-dev\n"
            "      deps: []\n"
            "  hooks:\n"
            "    - name: marker\n"
            "      event: post-update\n"
            "      python: open(%r, 'w').write('ran')\n" % marker
            + extra)

    def test_egg_info(self):
        """The motivating case: the root project's .egg-info after update,
        and 'ivpm sync' does not run post-update hooks."""
        self.mkFile("ivpm.yaml", _EGG_INFO_YAML)
        self.mkFile("pyproject.toml", _PYPROJECT)
        self.mkFile(os.path.join("src", "demo", "__init__.py"), "")

        self.ivpm_update(skip_venv=False, args=_Args())

        egg_info = os.path.join(self.testdir, "src", "demo.egg-info")
        self.assertTrue(os.path.isfile(os.path.join(egg_info, "PKG-INFO")),
                        "post-update hook did not create %s" % egg_info)

        shutil.rmtree(egg_info)
        self.ivpm_sync(args=_Args())
        self.assertFalse(os.path.exists(egg_info),
                         "ivpm sync ran a post-update hook")

    def test_hook_only_project_gets_venv(self):
        """No Python dependencies: the hook alone causes the venv to be made."""
        marker = os.path.join(self.testdir, "marker.txt")
        self.mkFile("ivpm.yaml", self._marker_hook(marker))

        self.ivpm_update(skip_venv=False, args=_Args())

        self.assertTrue(os.path.isdir(
            os.path.join(self.testdir, "packages", "python")))
        with open(marker) as fp:
            self.assertEqual(fp.read(), "ran")

    def test_failing_hook_fails_update(self):
        marker = os.path.join(self.testdir, "marker.txt")
        self.mkFile("ivpm.yaml", self._marker_hook(
            marker,
            "    - name: boom\n"
            "      event: post-update\n"
            "      python: raise SystemExit(3)\n"))

        with self.assertRaises(Exception) as cm:
            self.ivpm_update(skip_venv=False, args=_Args())
        self.assertIn("hook 'boom'", str(cm.exception))
        # The hook before it ran
        self.assertTrue(os.path.isfile(marker))

    def test_no_hooks(self):
        """--no-hooks: nothing runs, so no venv is needed either."""
        marker = os.path.join(self.testdir, "marker.txt")
        self.mkFile("ivpm.yaml", self._marker_hook(marker))

        args = _Args()
        args.no_hooks = True
        self.ivpm_update(skip_venv=True, args=args)
        self.assertFalse(os.path.exists(marker))

    def test_no_venv_is_an_error(self):
        """Hooks declared, venv skipped and none from an earlier run."""
        marker = os.path.join(self.testdir, "marker.txt")
        self.mkFile("ivpm.yaml", self._marker_hook(marker))

        with self.assertRaises(Exception) as cm:
            self.ivpm_update(skip_venv=True, args=_Args())
        self.assertIn("hook 'marker'", str(cm.exception))
        self.assertFalse(os.path.exists(marker))
