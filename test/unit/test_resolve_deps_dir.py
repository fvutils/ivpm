#****************************************************************************
#* test_resolve_deps_dir.py
#*
#* Unit tests for deps-dir resolution shared by the 'perf' and 'show'
#* sub-commands (ivpm.proj_info.resolve_deps_dir / find_lockfile_dir).
#*
#* Resolution contract:
#*   1. via ivpm.yaml, if present (its declared deps-dir, default 'packages')
#*   2. otherwise, by searching direct sub-directories for the lockfile
#*   3. otherwise, the conventional <project>/packages
#****************************************************************************
import os
import tempfile
import textwrap
import unittest

from ivpm.proj_info import resolve_deps_dir, find_lockfile_dir


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{}")


class TestResolveDepsDir(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_yaml(self, body):
        with open(os.path.join(self.tmp, "ivpm.yaml"), "w") as f:
            f.write(textwrap.dedent(body))

    # -- via ivpm.yaml --------------------------------------------------

    def test_yaml_explicit_deps_dir(self):
        """An explicit deps-dir in ivpm.yaml wins."""
        self._write_yaml("""
            package:
              name: p
              deps-dir: vendor
              dep-sets:
              - name: default
                deps: []
        """)
        # A stray lockfile elsewhere must NOT override the manifest.
        _touch(os.path.join(self.tmp, "packages", "package-lock.json"))
        self.assertEqual(resolve_deps_dir(self.tmp),
                         os.path.join(self.tmp, "vendor"))

    def test_yaml_default_deps_dir(self):
        """ivpm.yaml present without deps-dir → conventional 'packages'."""
        self._write_yaml("""
            package:
              name: p
              dep-sets:
              - name: default
                deps: []
        """)
        self.assertEqual(resolve_deps_dir(self.tmp),
                         os.path.join(self.tmp, "packages"))

    def test_malformed_yaml_falls_back_to_search(self):
        """A manifest that fails to parse must not crash resolution; the
        lockfile search takes over."""
        self._write_yaml("this: is not: valid: ivpm")
        _touch(os.path.join(self.tmp, "deps", "package-lock.json"))
        self.assertEqual(resolve_deps_dir(self.tmp),
                         os.path.join(self.tmp, "deps"))

    # -- lockfile search (no ivpm.yaml) ---------------------------------

    def test_search_nonconventional_dir(self):
        """No ivpm.yaml: lockfile in a non-'packages' dir is discovered."""
        _touch(os.path.join(self.tmp, "third_party", "package-lock.json"))
        self.assertEqual(resolve_deps_dir(self.tmp),
                         os.path.join(self.tmp, "third_party"))

    def test_search_prefers_conventional_packages(self):
        """When multiple dirs hold a lockfile, 'packages' is preferred."""
        _touch(os.path.join(self.tmp, "aaa", "package-lock.json"))
        _touch(os.path.join(self.tmp, "packages", "package-lock.json"))
        self.assertEqual(resolve_deps_dir(self.tmp),
                         os.path.join(self.tmp, "packages"))

    def test_no_yaml_no_lockfile_falls_back(self):
        """No ivpm.yaml and no lockfile anywhere → conventional 'packages'."""
        self.assertEqual(resolve_deps_dir(self.tmp),
                         os.path.join(self.tmp, "packages"))

    # -- find_lockfile_dir directly -------------------------------------

    def test_find_lockfile_dir_none(self):
        self.assertIsNone(find_lockfile_dir(self.tmp))

    def test_find_lockfile_dir_missing_project(self):
        """A non-existent project dir returns None rather than raising."""
        self.assertIsNone(find_lockfile_dir(os.path.join(self.tmp, "nope")))


if __name__ == "__main__":
    unittest.main()
