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

    def _args(self, dep_set=None):
        class _A:
            pass
        a = _A()
        a.dep_set = dep_set
        a.definitions = []
        return a

    def _yaml_target(self, name="ws_yaml"):
        target = os.path.join(self.testdir, name)
        os.makedirs(target)
        with open(os.path.join(target, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: x\n")
        return target

    def test_dep_sets_flattened_for_update(self):
        target = self._yaml_target()
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(
                self._args(["a,b", "c"]), target, CloneResult(ok=True))
        self.assertEqual(["a", "b", "c"], upd.call_args.kwargs["dep_set"])

    def test_dep_sets_deduped_and_stripped(self):
        target = self._yaml_target()
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(
                self._args([" b ", "a,b"]), target, CloneResult(ok=True))
        self.assertEqual(["b", "a"], upd.call_args.kwargs["dep_set"])

    def test_no_dep_set_selects_default(self):
        target = self._yaml_target()
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(
                self._args(), target, CloneResult(ok=True))
        self.assertIsNone(upd.call_args.kwargs["dep_set"])

    def test_dep_sets_flattened_for_bare_tree(self):
        target = os.path.join(self.testdir, "ws_bare_ds")
        os.makedirs(target)
        rc = CloneRootConfig(default_package={"name": "x"})
        with mock.patch.object(ProjectOps, "update") as upd:
            CmdClone()._post_clone_update(
                self._args(["a,b"]), target,
                CloneResult(ok=True, root_config=rc))
        self.assertEqual(["a", "b"], upd.call_args.kwargs["dep_set"])

    def test_dep_sets_without_manifest_is_fatal(self):
        target = os.path.join(self.testdir, "ws_none_ds")
        os.makedirs(target)
        from ivpm.yamlsrc import SrcLoaderError
        with mock.patch.object(ProjectOps, "update") as upd:
            with self.assertRaises(SrcLoaderError) as ctx:
                CmdClone()._post_clone_update(
                    self._args(["a,b"]), target, CloneResult(ok=True))
        self.assertIn("'a,b'", str(ctx.exception))
        upd.assert_not_called()

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


class TestCloneDepSetArgs(TestBase):
    """clone's -d is repeatable and comma-separated, like update's."""

    def setUp(self):
        super().setUp()
        _install_fake_registry()

    def tearDown(self):
        _reset_registry()
        super().tearDown()

    def test_repeats_accumulate(self):
        args, _ = _parse(["clone", "-d", "a,b", "-d", "c", "myvcs://repo"])
        self.assertEqual(["a,b", "c"], args.dep_set)
        self.assertEqual("myvcs://repo", args.src)

    def test_absent_is_none(self):
        args, _ = _parse(["clone", "myvcs://repo"])
        self.assertIsNone(args.dep_set)


class TestCloneFreshWorkspace(TestBase):
    """clone reuses a checkout, but never a directory holding an IVPM install:
    its recorded dep-set(s) would conflict with this clone's -d."""

    def setUp(self):
        super().setUp()
        _install_fake_registry()
        FakeVcsProvider.last_request = None
        # Let the fake provider accept non-empty targets, as git does, so the
        # IVPM-state guard (not the generic non-empty guard) is what decides.
        patcher = mock.patch.object(FakeVcsProvider, "allows_nonempty_target",
                                    True, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        _reset_registry()
        super().tearDown()

    def _target(self, files):
        import json
        target = os.path.join(self.testdir, "ws")
        for rel, body in files.items():
            path = os.path.join(target, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(body if isinstance(body, str) else json.dumps(body))
        return target

    def _clone(self, target):
        args, _ = _parse(["clone", "myvcs://repo", target])
        args.func(args)

    def _assert_refused(self, target, found):
        from ivpm.yamlsrc import SrcLoaderError
        with self.assertRaises(SrcLoaderError) as ctx:
            self._clone(target)
        msg = str(ctx.exception)
        self.assertIn("already an IVPM workspace", msg)
        self.assertIn(os.path.join(target, found), msg)
        self.assertIsNone(FakeVcsProvider.last_request,
                          "provider ran despite the refusal")

    _LOCK = {"ivpm_lock_version": 2, "packages": {}}

    def test_packages_lock_refused(self):
        target = self._target({"packages/package-lock.json": self._LOCK})
        self._assert_refused(target, "packages/package-lock.json")

    def test_bare_deps_dir_lock_refused(self):
        target = self._target({"import/package-lock.json": self._LOCK})
        self._assert_refused(target, "import/package-lock.json")

    def test_ivpm_json_only_refused(self):
        target = self._target({"packages/ivpm.json": {"dep-set": "a"}})
        self._assert_refused(target, "packages/ivpm.json")

    def test_target_is_deps_dir_refused(self):
        target = self._target({"package-lock.json": self._LOCK})
        self._assert_refused(target, "package-lock.json")

    def test_here_in_workspace_refused(self):
        from ivpm.yamlsrc import SrcLoaderError
        target = self._target({"packages/package-lock.json": self._LOCK})
        cwd = os.getcwd()
        os.chdir(target)
        try:
            args, _ = _parse(["clone", "--here", "myvcs://repo"])
            with self.assertRaises(SrcLoaderError):
                args.func(args)
        finally:
            os.chdir(cwd)
        self.assertIsNone(FakeVcsProvider.last_request)

    def test_checkout_without_install_allowed(self):
        target = self._target({".git/HEAD": "ref: refs/heads/main\n",
                               "README": "x"})
        self._clone(target)
        self.assertIsNotNone(FakeVcsProvider.last_request)

    def test_foreign_lock_allowed(self):
        """An npm package-lock.json is not IVPM state."""
        target = self._target({"web/package-lock.json": {"lockfileVersion": 3}})
        self._clone(target)
        self.assertIsNotNone(FakeVcsProvider.last_request)


class _TwoDepSetProvider(CloneProvider):
    """Materializes a project with dep-sets a and b, each with a dir dep."""
    deps_root = None

    @classmethod
    def provider_info(cls):
        return CloneProviderInfo(name="twods", description="two dep-sets",
                                 schemes=["twods"])

    def schemes(self):
        return ["twods"]

    def clone(self, req):
        os.makedirs(req.target_dir, exist_ok=True)
        with open(os.path.join(req.target_dir, "ivpm.yaml"), "w") as f:
            f.write(
                "package:\n"
                "  name: sample\n"
                "  dep-sets:\n"
                "    - name: a\n"
                "      deps:\n"
                "        - name: dep_a\n"
                "          url: file://%s/dep_a\n"
                "          src: dir\n"
                "    - name: b\n"
                "      deps:\n"
                "        - name: dep_b\n"
                "          url: file://%s/dep_b\n"
                "          src: dir\n" % (self.deps_root, self.deps_root))
        return CloneResult(ok=True)


class TestCloneMultiDepSetEndToEnd(TestBase):
    """clone -d a,b installs both sets through the real update, and records
    the selection so a later bare update keeps it."""

    def setUp(self):
        super().setUp()
        rgy = CloneProviderRgy()
        rgy.register(_TwoDepSetProvider())
        CloneProviderRgy._inst = rgy
        deps_root = os.path.join(self.testdir, "deps")
        for name in ("dep_a", "dep_b"):
            os.makedirs(os.path.join(deps_root, name))
            with open(os.path.join(deps_root, name, "README"), "w") as f:
                f.write(name)
        _TwoDepSetProvider.deps_root = deps_root

    def tearDown(self):
        CloneProviderRgy._inst = None
        super().tearDown()

    def test_clone_installs_and_records_both(self):
        import json
        from ivpm.package_lock import read_lock
        ws = os.path.join(self.testdir, "ws")
        args, _ = _parse(["clone", "-d", "a,b", "twods://x", ws])
        args.py_skip_install = True
        args.func(args)

        lock = read_lock(os.path.join(ws, "packages", "package-lock.json"))
        self.assertIn("dep_a", lock["packages"])
        self.assertIn("dep_b", lock["packages"])
        with open(os.path.join(ws, "packages", "ivpm.json")) as f:
            self.assertEqual(["a", "b"], json.load(f)["dep-sets"])

        # A bare update adopts the recorded pair, not the default ('a').
        _, _, dep_sets, _ = ProjectOps(ws)._init()
        self.assertEqual(["a", "b"], dep_sets)


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
