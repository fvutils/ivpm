#****************************************************************************
#* test_deps_dir_discovery.py
#*
#* Tests for deps-dir self-recognition: 'ivpm status' and 'ivpm sync' must work
#* when run *inside* a deps-dir (the shared tool-directory layout, where the
#* deps-dir is the root), and must keep working unchanged for a conventional
#* project workspace. Uses a pypi dep with --py-skip-install so no network
#* access is required.
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

_CATALOG = textwrap.dedent("""
    package:
      name: acme-tools
      description: Remote catalog of tool bundles
      dep-sets:
        - name: default
          description: Minimal set
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


class TestDepsDirDiscovery(unittest.TestCase):

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

    def _mk_tools_dir(self, name="tools"):
        """Install into <work>/<name> such that the directory *is* the deps-dir."""
        tools = os.path.join(self.work, name)
        os.makedirs(tools, exist_ok=True)
        _run("update", "--from", self.catalog, "--deps-dir", ".",
             "--py-skip-install", cwd=tools)
        self.assertTrue(os.path.isfile(
            os.path.join(tools, "package-lock.json")),
            "precondition: the outdir must itself be the deps-dir")
        return tools

    #-----------------------------------------------------------------------
    # Inside a deps-dir
    #-----------------------------------------------------------------------

    def test_status_inside_deps_dir(self):
        tools = self._mk_tools_dir()
        _run("status", cwd=tools)

    def test_sync_inside_deps_dir(self):
        tools = self._mk_tools_dir()
        _run("sync", "-n", cwd=tools)

    def test_status_explicit_deps_dir(self):
        tools = self._mk_tools_dir()
        _run("status", "-p", tools, cwd=self.work)

    def test_status_walks_up_from_package_subdir(self):
        tools = self._mk_tools_dir()
        sub = os.path.join(tools, "some-package", "src")
        os.makedirs(sub)
        _run("status", cwd=sub)

    #-----------------------------------------------------------------------
    # Conventional workspace behavior is unchanged
    #-----------------------------------------------------------------------

    def test_status_from_project_root_unchanged(self):
        _run("update", "--from", self.catalog, "--py-skip-install", cwd=self.work)
        self.assertTrue(os.path.isdir(os.path.join(self.work, "packages")))
        _run("status", cwd=self.work)

    def test_explicit_p_does_not_walk(self):
        """An explicit -p is an assertion; it must not resolve to an ancestor."""
        tools = self._mk_tools_dir()
        empty = os.path.join(tools, "empty-subdir")
        os.makedirs(empty)
        out, rc, err = _run("status", "-p", empty, cwd=self.work, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("Failed to locate IVPM meta-data", out + err)

    def test_self_match_precedes_child_scan(self):
        """A deps-dir whose nested scope carries its own lock resolves to the
        outer dir -- no ambiguity fatal, no descent into the nested scope."""
        tools = self._mk_tools_dir()
        nested = os.path.join(tools, "nested-pkg")
        os.makedirs(nested)
        with open(os.path.join(tools, "package-lock.json")) as f:
            lock = json.load(f)
        with open(os.path.join(nested, "package-lock.json"), "w") as f:
            json.dump(lock, f)
        out, rc, err = _run("status", cwd=tools, check=False)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Ambiguous IVPM workspace", err)

    def test_walk_stops_at_filesystem_root(self):
        """An isolated tmpdir with nothing above it fails cleanly."""
        with tempfile.TemporaryDirectory() as isolated:
            _, rc, err = _run("status", cwd=isolated, check=False)
            self.assertNotEqual(rc, 0)
            self.assertIn("Failed to locate IVPM meta-data", err)


if __name__ == "__main__":
    unittest.main()
