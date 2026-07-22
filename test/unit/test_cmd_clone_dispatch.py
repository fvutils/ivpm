import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.__main__ import (
    get_parser, _partition_clone_argv, _resolve_clone_provider_safe,
    _maybe_clone_provider_help,
)
from unittest import mock

from ivpm.clone.clone_provider import (
    CloneProvider, CloneOption, CloneResult, CloneRootConfig, ClaimStrength,
)
from ivpm.clone.clone_provider_rgy import CloneProviderRgy
from ivpm.show.info_types import CloneProviderInfo
from ivpm.cmds.cmd_clone import CmdClone
from ivpm.project_ops import ProjectOps


class FakeVcsProvider(CloneProvider):
    """A fake provider owning myvcs:// and accepting -branch/-node."""
    last_request = None

    @classmethod
    def provider_info(cls):
        return CloneProviderInfo(name="myvcs", description="fake myvcs",
                                 schemes=["myvcs"])

    def schemes(self):
        return ["myvcs"]

    def options(self):
        return [
            CloneOption(flags=["-branch"], dest="branch", help="branch", metavar="V"),
            CloneOption(flags=["-node"], dest="node", help="node", metavar="V"),
        ]

    def default_workspace_name(self, src):
        return "myvcs_ws"

    def clone(self, req):
        FakeVcsProvider.last_request = req
        # Materialize a minimal tree so post-clone update is a no-op-safe.
        os.makedirs(req.target_dir, exist_ok=True)
        return CloneResult(ok=True, resolved_revision="deadbeef")


def _install_fake_registry():
    """Replace the CloneProviderRgy singleton with git + a fake myvcs provider."""
    from ivpm.clone.git_clone_provider import GitCloneProvider
    rgy = CloneProviderRgy()
    rgy.register(GitCloneProvider())
    rgy.register(FakeVcsProvider())
    CloneProviderRgy._inst = rgy
    return rgy


def _reset_registry():
    CloneProviderRgy._inst = None


def _parse(argv):
    """Reproduce the clone parse pipeline from __main__.main()."""
    parser = get_parser([], [])
    raw = argv
    args, extras = parser.parse_known_args(raw)
    prov_tokens = []
    if getattr(args, 'command', None) == 'clone':
        provider = _resolve_clone_provider_safe(args)
        if provider is not None:
            common_argv, prov_tokens = _partition_clone_argv(raw, provider)
            if prov_tokens:
                args, extras = parser.parse_known_args(common_argv)
        if getattr(args, 'workspace_dir', None) is None and len(extras) == 1 \
                and not extras[0].startswith('-'):
            args.workspace_dir = extras[0]
            extras = []
        args._clone_extras = prov_tokens
    args.project_dir = None
    return args, extras


class TestCloneDispatch(TestBase):

    def setUp(self):
        super().setUp()
        _install_fake_registry()
        FakeVcsProvider.last_request = None

    def tearDown(self):
        _reset_registry()
        super().tearDown()

    def test_provider_args_routed(self):
        target = os.path.join(self.testdir, "myvcs_target")
        args, extras = _parse(
            ["clone", "myvcs://repo", target, "-branch", "abc", "-node", "xyz"])
        self.assertEqual(extras, [])
        args.func(args)
        req = FakeVcsProvider.last_request
        self.assertIsNotNone(req)
        self.assertEqual(req.provider_args.branch, "abc")
        self.assertEqual(req.provider_args.node, "xyz")
        self.assertEqual(req.src, "myvcs://repo")

    def test_provider_flag_missing_value_errors(self):
        # '-branch' requires a value; the provider parser rejects it (scoped).
        args, extras = _parse(["clone", "myvcs://repo", "-branch"])
        with self.assertRaises(SystemExit):
            args.func(args)

    def test_forced_provider_overrides_claim(self):
        # A git-shaped URL, but --provider myvcs forces myvcs.
        target = os.path.join(self.testdir, "forced_target")
        args, extras = _parse(
            ["clone", "--provider", "myvcs", "https://github.com/a/b", target])
        args.func(args)
        self.assertIsNotNone(FakeVcsProvider.last_request)

    def test_provider_help_passthrough(self):
        # Non-default provider help is handled before argparse.
        self.assertTrue(_maybe_clone_provider_help(["clone", "myvcs://x", "--help"]))
        # `--provider myvcs --help` form too.
        self.assertTrue(_maybe_clone_provider_help(
            ["clone", "--provider", "myvcs", "--help"]))
        # git (default) falls through to the generic clone help.
        self.assertFalse(_maybe_clone_provider_help(
            ["clone", "https://github.com/a/b", "--help"]))

    def test_default_workspace_name_from_provider(self):
        args, extras = _parse(["clone", "myvcs://repo"])
        args.func(args)
        req = FakeVcsProvider.last_request
        self.assertEqual(os.path.basename(req.target_dir), "myvcs_ws")

    def test_git_only_flag_ignored_for_non_git(self):
        # --ssh with a non-git provider should warn+ignore, not fail.
        args, extras = _parse(["clone", "--ssh", "myvcs://repo"])
        args.func(args)
        self.assertIsNotNone(FakeVcsProvider.last_request)


class TestPostCloneUpdate(TestBase):
    """The provider's CloneResult.root_config must reach ProjectOps.update."""

    def _args(self):
        class _A:
            pass
        a = _A()
        a.dep_set = None
        a.definitions = []
        return a

    def test_overlay_threaded_when_yaml_present(self):
        target = os.path.join(self.testdir, "ws_yaml")
        os.makedirs(target)
        with open(os.path.join(target, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: x\n")
        rc = CloneRootConfig(handler_overlay={
            "example-handler": {"items": ["a"]}})
        result = CloneResult(ok=True, root_config=rc)
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(self._args(), target, result)
        self.assertTrue(upd.called)
        kwargs = upd.call_args.kwargs
        self.assertEqual(kwargs.get("handler_overlay"),
                         {"example-handler": {"items": ["a"]}})
        self.assertIsNone(kwargs.get("default_config"))

    def test_default_config_used_when_no_yaml(self):
        target = os.path.join(self.testdir, "ws_bare")
        os.makedirs(target)
        dp = {"name": "x", "deps-dir": "import"}
        rc = CloneRootConfig(default_package=dp,
                             handler_overlay={"myhandler": {"markers": True}})
        result = CloneResult(ok=True, root_config=rc)
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(self._args(), target, result)
        self.assertTrue(upd.called)
        kwargs = upd.call_args.kwargs
        self.assertEqual(kwargs.get("default_config"), dp)
        self.assertEqual(kwargs.get("handler_overlay"), {"myhandler": {"markers": True}})

    def test_no_config_no_yaml_skips_update(self):
        target = os.path.join(self.testdir, "ws_none")
        os.makedirs(target)
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(self._args(), target,
                                          CloneResult(ok=True))
        upd.assert_not_called()


class TestStampRootRecordBare(TestBase):
    """_stamp_root_record must work for a bare workspace (no ivpm.yaml) and
    persist the provider's root_config so a later update can reproduce it."""

    def _fake_provider(self, name="myvcs"):
        class _P:
            def provider_info(self_p):
                return CloneProviderInfo(name=name, description="", schemes=[name])
        return _P()

    def _write_bare_lock(self, deps_dir_name="import"):
        import json, hashlib
        d = os.path.join(self.testdir, "ws", deps_dir_name)
        os.makedirs(d)
        lock = {"ivpm_lock_version": 1, "packages": {}}
        body = json.dumps(lock, indent=2, sort_keys=True)
        lock["sha256"] = hashlib.sha256(body.encode()).hexdigest()
        with open(os.path.join(d, "package-lock.json"), "w") as f:
            json.dump(lock, f, indent=2, sort_keys=True)
        return os.path.join(self.testdir, "ws")

    def test_stamps_root_config_for_bare_workspace(self):
        from ivpm.package_lock import read_lock
        ws = self._write_bare_lock("import")
        rc = CloneRootConfig(
            default_package={"name": "myrepo", "deps-dir": "import",
                             "dep-sets": [{"name": "default", "deps": []}]},
            handler_overlay={"example-handler": {"items": ["a"]}})
        result = CloneResult(ok=True, resolved_revision="rev123", root_config=rc)

        CmdClone()._stamp_root_record(ws, "myvcs://myrepo",
                                      self._fake_provider("myvcs"), result)

        root = read_lock(os.path.join(ws, "import", "package-lock.json"))["root"]
        self.assertEqual(root["provider"], "myvcs")
        self.assertEqual(root["src"], "myvcs://myrepo")
        self.assertEqual(root["config"]["default_package"]["name"], "myrepo")
        self.assertEqual(root["config"]["handler_overlay"],
                         {"example-handler": {"items": ["a"]}})


if __name__ == "__main__":
    unittest.main()
