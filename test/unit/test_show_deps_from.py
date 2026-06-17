#****************************************************************************
#* test_show_deps_from.py
#*
#* Tests for 'ivpm show deps --from <manifest>' — the catalog browse view.
#* No dependencies are fetched; purely parses the manifest.
#****************************************************************************
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_UNIT_DIR))
_SRC_DIR = os.path.join(_ROOT_DIR, "src")
_PYTHON = os.path.join(_ROOT_DIR, "packages", "python", "bin", "python3")
if not os.path.exists(_PYTHON):
    _PYTHON = sys.executable
_ENV = {**os.environ, "PYTHONPATH": _SRC_DIR}
sys.path.insert(0, _SRC_DIR)

_CATALOG = textwrap.dedent("""
    package:
      name: acme-tools
      version: 2.3.0
      description: Curated EDA toolchain bundles
      default-dep-set: default
      dep-sets:
        - name: default
          description: Minimal set
          deps:
            - name: pyyaml
              src: pypi
        - name: gui-tools
          description: Adds the GUI debugger
          deps:
            - name: pyyaml
              src: pypi
""")

_CATALOG_NO_DESC = textwrap.dedent("""
    package:
      name: plain
      dep-sets:
        - name: default
          deps:
            - name: pyyaml
              src: pypi
""")


def _run(*args, cwd, check=True):
    cmd = [_PYTHON, "-m", "ivpm"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, env=_ENV, cwd=cwd)
    if check and r.returncode != 0:
        raise AssertionError(
            f"{cmd} failed (rc={r.returncode})\nstdout:{r.stdout}\nstderr:{r.stderr}")
    return r.stdout, r.returncode, r.stderr


class TestShowDepsFrom(unittest.TestCase):

    def setUp(self):
        self._cat = tempfile.TemporaryDirectory()
        self.catalog = os.path.join(self._cat.name, "catalog.yaml")
        with open(self.catalog, "w") as f:
            f.write(_CATALOG)
        self._work = tempfile.TemporaryDirectory()
        self.work = self._work.name

    def tearDown(self):
        self._cat.cleanup()
        self._work.cleanup()

    def test_text_lists_catalog(self):
        out, rc, _ = _run("show", "deps", "--from", self.catalog, "--no-rich",
                          cwd=self.work)
        self.assertEqual(rc, 0)
        self.assertIn("acme-tools", out)
        self.assertIn("v2.3.0", out)
        self.assertIn("Curated EDA toolchain bundles", out)
        self.assertIn("default", out)
        self.assertIn("gui-tools", out)
        self.assertIn("Adds the GUI debugger", out)
        self.assertIn("(default)", out)

    def test_json_shape(self):
        out, rc, _ = _run("show", "deps", "--from", self.catalog, "--json",
                          cwd=self.work)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["name"], "acme-tools")
        self.assertEqual(data["version"], "2.3.0")
        self.assertEqual(data["default_dep_set"], "default")
        sets = {d["name"]: d for d in data["dep_sets"]}
        self.assertTrue(sets["default"]["default"])
        self.assertFalse(sets["gui-tools"]["default"])
        self.assertEqual(sets["gui-tools"]["description"], "Adds the GUI debugger")

    def test_no_descriptions_still_lists(self):
        path = os.path.join(self._cat.name, "plain.yaml")
        with open(path, "w") as f:
            f.write(_CATALOG_NO_DESC)
        out, rc, _ = _run("show", "deps", "--from", path, "--no-rich", cwd=self.work)
        self.assertEqual(rc, 0)
        self.assertIn("plain", out)
        self.assertIn("default", out)

    def test_does_not_touch_cwd(self):
        _run("show", "deps", "--from", self.catalog, "--no-rich", cwd=self.work)
        # Browsing must not create a workspace / lock / manifest in the cwd
        self.assertEqual(os.listdir(self.work), [])


if __name__ == "__main__":
    unittest.main()
