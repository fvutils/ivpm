#****************************************************************************
#* test_update_from.py
#*
#* Tests for 'ivpm update --from <manifest>' (install from an external manifest)
#* and the --deps-dir override. Uses a pypi dep with --py-skip-install so no
#* network access is required.
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
        - name: gui-tools
          description: Adds the GUI debugger
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


class TestUpdateFrom(unittest.TestCase):

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

    def _lock(self, deps_dir="packages"):
        with open(os.path.join(self.work, deps_dir, "package-lock.json")) as f:
            return json.load(f)

    def test_install_default_into_cwd(self):
        _run("update", "--from", self.catalog, "--py-skip-install", cwd=self.work)
        # deps land in ./packages; no ivpm.yaml is copied into the workspace
        self.assertTrue(os.path.isdir(os.path.join(self.work, "packages")))
        self.assertFalse(os.path.isfile(os.path.join(self.work, "ivpm.yaml")))

    def test_lock_records_source_manifest(self):
        _run("update", "--from", self.catalog, "--py-skip-install", cwd=self.work)
        sm = self._lock().get("source_manifest")
        self.assertIsNotNone(sm)
        self.assertEqual(sm["from"], self.catalog)
        self.assertEqual(sm["dep_set"], "default")

    def test_dep_set_selection(self):
        _run("update", "--from", self.catalog, "-d", "gui-tools",
             "--py-skip-install", cwd=self.work)
        self.assertEqual(self._lock()["source_manifest"]["dep_set"], "gui-tools")

    def test_multiple_dep_sets_repeated(self):
        _run("update", "--from", self.catalog, "-d", "default", "-d", "gui-tools",
             "--py-skip-install", cwd=self.work)
        sm = self._lock()["source_manifest"]
        # Multiple sets are recorded as a list; the singular key is omitted.
        self.assertEqual(sm["dep_sets"], ["default", "gui-tools"])
        self.assertNotIn("dep_set", sm)

    def test_multiple_dep_sets_comma_separated(self):
        _run("update", "--from", self.catalog, "-d", "default,gui-tools",
             "--py-skip-install", cwd=self.work)
        self.assertEqual(
            self._lock()["source_manifest"]["dep_sets"], ["default", "gui-tools"])

    def test_unknown_dep_set_errors(self):
        _, rc, err = _run("update", "--from", self.catalog, "-d", "nope",
                          "--py-skip-install", cwd=self.work, check=False)
        self.assertNotEqual(rc, 0)

    def test_deps_dir_override(self):
        _run("update", "--from", self.catalog, "--deps-dir", "vendor",
             "--py-skip-install", cwd=self.work)
        self.assertTrue(os.path.isfile(
            os.path.join(self.work, "vendor", "package-lock.json")))
        self.assertFalse(os.path.isdir(os.path.join(self.work, "packages")))

    def test_local_ivpm_yaml_blocks_from(self):
        with open(os.path.join(self.work, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: local\n  dep-sets:\n  - name: default\n    deps: []\n")
        _, rc, err = _run("update", "--from", self.catalog, "--py-skip-install",
                          cwd=self.work, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("already has an ivpm.yaml", err)

    #-----------------------------------------------------------------------
    # Replay: a --from workspace has no ivpm.yaml, so a bare re-run must
    # recover the driving manifest from the lock.
    #-----------------------------------------------------------------------

    def test_bare_rerun_replays_source(self):
        _run("update", "--from", self.catalog, "--py-skip-install", cwd=self.work)
        _run("update", "--py-skip-install", cwd=self.work)
        self.assertEqual(self._lock()["source_manifest"]["from"], self.catalog)

    def test_bare_rerun_preserves_dep_sets(self):
        _run("update", "--from", self.catalog, "-d", "default", "-d", "gui-tools",
             "--py-skip-install", cwd=self.work)
        _run("update", "--py-skip-install", cwd=self.work)
        self.assertEqual(
            self._lock()["source_manifest"]["dep_sets"], ["default", "gui-tools"])

    def test_explicit_from_overrides_recorded(self):
        other = os.path.join(self._cat.name, "other.yaml")
        with open(other, "w") as f:
            f.write(_CATALOG.replace("acme-tools", "other-tools"))
        _run("update", "--from", self.catalog, "--py-skip-install", cwd=self.work)
        _run("update", "--from", other, "--py-skip-install", cwd=self.work)
        self.assertEqual(self._lock()["source_manifest"]["from"], other)

    def test_rerun_with_deps_dir_override(self):
        """A tool-directory install (deps-dir IS the root) replays in place."""
        tools = os.path.join(self.work, "tools")
        os.makedirs(tools)
        _run("update", "--from", self.catalog, "--deps-dir", ".",
             "--py-skip-install", cwd=tools)
        # Bare re-run: the recorded deps-dir is recovered, so the layout is
        # reproduced without the user repeating --deps-dir.
        _run("update", "--py-skip-install", cwd=tools)
        with open(os.path.join(tools, "package-lock.json")) as f:
            self.assertEqual(json.load(f)["source_manifest"]["from"], self.catalog)
        self.assertFalse(os.path.isdir(os.path.join(tools, "packages")))
        self.assertFalse(os.path.isfile(
            os.path.join(self.work, "package-lock.json")))

    def test_from_and_lock_file_mutually_exclusive(self):
        _, rc, err = _run("update", "--from", self.catalog, "--lock-file",
                          "foo.json", "--py-skip-install", cwd=self.work, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("mutually exclusive", err)


if __name__ == "__main__":
    unittest.main()
