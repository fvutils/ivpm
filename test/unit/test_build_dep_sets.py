"""
`ivpm build` works on the dep-set(s) the last `ivpm update` installed -- all of
them -- and never selects its own. `build -d` is deprecated: accepted (with a
warning) only when it names the installed dep-sets.
"""
import json
import os
import unittest
from unittest import mock

from .test_base import TestBase
from ivpm.cmds.cmd_build import CmdBuild
from ivpm.project_ops import ProjectOps
from ivpm.yamlsrc import SrcLoaderError


_MANIFEST = """
package:
  name: t

  dep-sets:
  - name: a
    deps:
    - name: pkg-a
      src: pypi

  - name: b
    deps:
    - name: pkg-b
      src: pypi

  - name: c
    deps:
    - name: pkg-c
      src: pypi
"""


class TestBuildDepSets(TestBase):

    def _mkws(self, ivpm_json=None, lock=None):
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        with open(os.path.join(self.testdir, "ivpm.yaml"), "w") as fp:
            fp.write(_MANIFEST)
        if ivpm_json is not None:
            with open(os.path.join(deps_dir, "ivpm.json"), "w") as fp:
                json.dump(ivpm_json, fp)
        if lock is not None:
            with open(os.path.join(deps_dir, "package-lock.json"), "w") as fp:
                json.dump(lock, fp)
        return deps_dir

    def _build(self, dep_set=None):
        """Run build with the updater and handlers stubbed out.

        Returns (package names handed to the updater, warnings), or the
        updater mock's call list is empty when build refuses first.
        """
        warnings = []
        with mock.patch("ivpm.project_ops.PackageUpdater") as upd_cls, \
                mock.patch("ivpm.project_ops.PackageHandlerRgy"), \
                mock.patch("ivpm.project_ops._dispatch_root"), \
                mock.patch("ivpm.project_ops.warning",
                           side_effect=warnings.append):
            self._updater = upd_cls.return_value
            ProjectOps(self.testdir).build(dep_set=dep_set)
        ds = self._updater.update.call_args.args[0]
        return sorted(ds.packages.keys()), warnings

    def test_builds_every_recorded_dep_set(self):
        self._mkws(ivpm_json={"dep-set": "a", "dep-sets": ["a", "b"]})
        pkgs, warnings = self._build()
        self.assertEqual(["pkg-a", "pkg-b"], pkgs)
        self.assertEqual([], warnings)

    def test_dep_sets_recovered_from_lock(self):
        self._mkws(lock={"ivpm_lock_version": 2, "packages": {},
                         "dep_sets": ["b", "c"]})
        pkgs, _ = self._build()
        self.assertEqual(["pkg-b", "pkg-c"], pkgs)

    def test_single_recorded_dep_set(self):
        self._mkws(ivpm_json={"dep-set": "c"})
        pkgs, _ = self._build()
        self.assertEqual(["pkg-c"], pkgs)

    def test_nothing_recorded_is_fatal(self):
        self._mkws()
        with mock.patch("ivpm.project_ops.PackageUpdater") as upd_cls:
            with self.assertRaises(SrcLoaderError) as ctx:
                ProjectOps(self.testdir).build()
        self.assertIn("ivpm update", str(ctx.exception))
        upd_cls.return_value.update.assert_not_called()

    def test_matching_dash_d_warns_and_builds(self):
        self._mkws(ivpm_json={"dep-set": "a", "dep-sets": ["a", "b"]})
        # Order does not matter: it is the same installed selection.
        pkgs, warnings = self._build(dep_set=["b", "a"])
        self.assertEqual(["pkg-a", "pkg-b"], pkgs)
        self.assertEqual(1, len(warnings))
        self.assertIn("deprecated", warnings[0])

    def test_mismatched_dash_d_is_fatal(self):
        self._mkws(ivpm_json={"dep-set": "a", "dep-sets": ["a", "b"]})
        with mock.patch("ivpm.project_ops.PackageUpdater") as upd_cls:
            with self.assertRaises(SrcLoaderError) as ctx:
                ProjectOps(self.testdir).build(dep_set=["c"])
        msg = str(ctx.exception)
        self.assertIn("a,b", msg)
        self.assertIn("--force", msg)
        upd_cls.return_value.update.assert_not_called()

    def test_recorded_dep_set_is_not_announced(self):
        """update's 'select a different one with -d --force' note does not
        apply to build, which has no selection of its own."""
        self._mkws(ivpm_json={"dep-set": "a"})
        notes = []
        with mock.patch("ivpm.project_ops.note", side_effect=notes.append):
            self._build()
        self.assertFalse(any("--force" in n for n in notes), notes)

    def test_cmd_build_flattens_dash_d(self):
        class _A:
            pass
        args = _A()
        args.project_dir = self.testdir
        args.dep_set = ["a,b", "a"]
        args.debug = False
        with mock.patch.object(ProjectOps, "build") as build:
            CmdBuild()(args)
        self.assertEqual(["a", "b"], build.call_args.kwargs["dep_set"])


if __name__ == "__main__":
    unittest.main()
