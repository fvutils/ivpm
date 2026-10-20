"""A ``src: pypi`` dependency must not read a same-named directory in the deps
dir as if it were its own source tree.

PyPI (and npm) packages are installed by their handler into packages/python
(packages/node); nothing is fetched to packages/<name>. A directory there is
typically a checkout left by an earlier source-based spec of the same name.
Loading its ivpm.yaml validated that checkout's dep-sets against the PyPI
entry's requested set (fatal when absent) or silently traversed its deps.
"""
import argparse
import os
import shutil
import tempfile
import unittest
from unittest import mock

from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.package_updater import PackageUpdater
from ivpm.pkg_types.package_git import PackageGit
from ivpm.pkg_types.package_pypi import PackagePyPi
from ivpm.pkg_types.package_npm import PackageNpm


class _StubHandler:
    def on_leaf_pre_load(self, pkg, update_info):
        pass

    def on_leaf_post_load(self, pkg, update_info):
        pass


# The root dev set redirects inherited deps to 'default-dev', exactly as in
# the reported workspace. 'lib' is a local source dep whose own manifest
# declares a transitive Git 'example'.
ROOT = """\
package:
  name: root
  dep-sets:
    - name: dev
      default-dep-set: default-dev
      deps:
        - name: example
          src: pypi
          version: ">=1.2"
          extras: [fast]
%s
"""

LIB_DEP = """\
        - name: lib
          url: file://%s
          src: dir
          dep-set: use
"""

LIB = """\
package:
  name: lib
  dep-sets:
    - name: use
      deps:
        - name: example
          url: https://example.invalid/example.git
          dep-set: use
"""

# What an earlier git-based 'example' left behind: no 'default-dev'.
LEFTOVER_NO_SET = """\
package:
  name: example
  default-dep-set: dev
  dep-sets:
    - name: dev
      deps: []
    - name: use
      deps: []
"""

# A leftover that *does* declare the requested set, with a sentinel dep that
# must never enter the graph, and self-declared extras that must never reach
# the requirement line.
LEFTOVER_SENTINEL = """\
package:
  name: example
  provides:
    python:
      extras: [leftover]
  dep-sets:
    - name: default-dev
      deps:
        - name: leftover-sentinel
          src: pypi
"""


class TestPyPiIgnoresLeftoverCheckout(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ivpm-pypi-leftover-")
        self.deps_dir = os.path.join(self._dir, "packages")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self._dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fp:
            fp.write(content)
        return path

    def _leftover(self, content):
        path = self._write(os.path.join("packages", "example", "ivpm.yaml"),
                           content)
        return path, os.stat(path).st_mtime_ns

    def _resolve(self, with_lib=False, lock_data=None, refresh_all=False):
        extra = ""
        if with_lib:
            self._write(os.path.join("lib_src", "ivpm.yaml"), LIB)
            extra = LIB_DEP % os.path.join(self._dir, "lib_src")
        root = self._write("ivpm.yaml", ROOT % extra)
        with open(root) as fp:
            ds = IvpmYamlReader().read(fp, root).get_dep_set("dev")
        updater = PackageUpdater(self.deps_dir, _StubHandler(),
                                 args=argparse.Namespace(jobs=1))
        updater.update_info.lock_data = lock_data
        updater.update_info.refresh_all = refresh_all
        with mock.patch.object(
                PackageGit, "update",
                side_effect=AssertionError("git fetch queued")) as git_update:
            all_pkgs = updater.update(ds)
        self.assertFalse(git_update.called)
        return all_pkgs

    def _graph(self, all_pkgs):
        """The resolved identity of every package, for comparing runs."""
        return sorted(
            (k, type(p).__name__, getattr(p, "version", None),
             tuple(getattr(p, "extras", None) or ()))
            for k, p in ((k, all_pkgs[k]) for k in all_pkgs.keys())
            if not getattr(p, "virtual", False))

    def _assert_pypi_example(self, all_pkgs):
        ex = all_pkgs["example"]
        self.assertIsInstance(ex, PackagePyPi)
        self.assertEqual("default-dev", ex.dep_set)
        self.assertEqual(">=1.2", ex.version)
        self.assertEqual(["fast"], ex.extras)
        self.assertIsNone(ex.proj_info)
        # Nothing from the leftover manifest was merged into the entry.
        self.assertFalse(ex.type_data)

    def test_missing_dep_set_in_leftover_is_not_fatal(self):
        path, mtime = self._leftover(LEFTOVER_NO_SET)
        all_pkgs = self._resolve()
        self._assert_pypi_example(all_pkgs)
        self.assertEqual(mtime, os.stat(path).st_mtime_ns)

    def test_leftover_deps_are_not_traversed(self):
        self._leftover(LEFTOVER_SENTINEL)
        all_pkgs = self._resolve()
        self._assert_pypi_example(all_pkgs)
        self.assertNotIn("leftover-sentinel", all_pkgs.keys())

    def test_root_pypi_beats_transitive_git_and_checkout_survives(self):
        path, mtime = self._leftover(LEFTOVER_NO_SET)
        all_pkgs = self._resolve(with_lib=True)
        self._assert_pypi_example(all_pkgs)
        self.assertIn("lib", all_pkgs.keys())
        with open(path) as fp:
            self.assertEqual(LEFTOVER_NO_SET, fp.read())
        self.assertEqual(mtime, os.stat(path).st_mtime_ns)

    def test_same_graph_with_and_without_leftover(self):
        without = self._graph(self._resolve(with_lib=True))
        self._leftover(LEFTOVER_SENTINEL)
        with_leftover = self._graph(self._resolve(with_lib=True))
        self.assertEqual(without, with_leftover)

    def test_lock_drift_does_not_refresh_the_checkout(self):
        """The lock still records the earlier git 'example'. Classifying the
        leftover as a drifted resident would try to clear it -- and PyPI's
        remove() rightly refuses, which used to fail the update."""
        path, mtime = self._leftover(LEFTOVER_NO_SET)
        lock = {"packages": {"example": {
            "src": "git", "url": "https://example.invalid/example.git",
            "dep_set": "use", "resolved_by": "root"}}}
        self._assert_pypi_example(self._resolve(lock_data=lock))
        self._assert_pypi_example(self._resolve(refresh_all=True))
        self.assertEqual(mtime, os.stat(path).st_mtime_ns)

    def test_requirement_keeps_version_and_extras(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        self._leftover(LEFTOVER_SENTINEL)
        all_pkgs = self._resolve()
        h = PackageHandlerPython()
        h.reset()
        reqs = os.path.join(self._dir, "reqs.txt")
        h._write_requirements_txt(self.deps_dir, [all_pkgs["example"]], reqs)
        with open(reqs) as fp:
            self.assertEqual(["example[fast]>=1.2"],
                             [l.strip() for l in fp if l.strip()])


class TestRegistrySourcesOccupyNoDir(unittest.TestCase):

    def test_update_returns_no_project(self):
        for cls in (PackagePyPi, PackageNpm):
            self.assertFalse(cls.occupies_dir)
            info = mock.Mock()
            self.assertIsNone(cls("x").update(info))
            info.report_package.assert_called_once_with(cacheable=False)


if __name__ == "__main__":
    unittest.main()
