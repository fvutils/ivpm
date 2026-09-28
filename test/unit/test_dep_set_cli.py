"""
Every command that selects dep-sets reads -d/--dep-set the same way: repeatable,
each value optionally comma-separated, duplicates dropped with the first
occurrence winning. A command that grows its own -d belongs in this table.
"""
import os
import unittest
from unittest import mock

from .test_base import TestBase
from ivpm.__main__ import get_parser
from ivpm.cmds.cmd_clone import CmdClone
from ivpm.cmds.cmd_update import CmdUpdate
from ivpm.clone.clone_provider import CloneResult
from ivpm.install_spec import parse_source_groups, split_source_groups
from ivpm.project_ops import ProjectOps


_DASH_D = ["-d", "a,b", "-d", "c", "-d", "a"]
_EXPECTED = ["a", "b", "c"]


class TestDepSetCliSymmetry(TestBase):

    def _parse(self, argv):
        args, _ = get_parser([], []).parse_known_args(argv)
        return args

    def test_update(self):
        args = self._parse(["update"] + _DASH_D)
        args.project_dir = self.testdir
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdUpdate()(args)
        self.assertEqual(_EXPECTED, upd.call_args.kwargs["dep_set"])

    def test_clone(self):
        args = self._parse(["clone"] + _DASH_D + ["https://example.com/r.git"])
        args.definitions = []
        target = os.path.join(self.testdir, "ws")
        os.makedirs(target)
        with open(os.path.join(target, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: x\n")
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(args, target, CloneResult(ok=True))
        self.assertEqual(_EXPECTED, upd.call_args.kwargs["dep_set"])

    def test_install(self):
        _, groups = split_source_groups(["--from", "cat.yaml"] + _DASH_D)
        specs = parse_source_groups(groups)
        self.assertEqual(_EXPECTED, specs[0].dep_sets)

    def test_build(self):
        # Deprecated, but read the same way so its match check is consistent.
        args = self._parse(["build"] + _DASH_D)
        args.project_dir = self.testdir
        with mock.patch.object(ProjectOps, "build") as build:
            args.func(args)
        self.assertEqual(_EXPECTED, build.call_args.kwargs["dep_set"])


if __name__ == "__main__":
    unittest.main()
