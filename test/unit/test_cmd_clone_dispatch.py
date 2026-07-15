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
from ivpm.clone.clone_provider import (
    CloneProvider, CloneOption, CloneResult, ClaimStrength,
)
from ivpm.clone.clone_provider_rgy import CloneProviderRgy
from ivpm.show.info_types import CloneProviderInfo


class FakeCdbProvider(CloneProvider):
    """A fake provider owning cdb:// and accepting -branch/-node."""
    last_request = None

    @classmethod
    def provider_info(cls):
        return CloneProviderInfo(name="cdb", description="fake cdb",
                                 schemes=["cdb"])

    def schemes(self):
        return ["cdb"]

    def options(self):
        return [
            CloneOption(flags=["-branch"], dest="branch", help="branch", metavar="V"),
            CloneOption(flags=["-node"], dest="node", help="node", metavar="V"),
        ]

    def default_workspace_name(self, src):
        return "cdb_ws"

    def clone(self, req):
        FakeCdbProvider.last_request = req
        # Materialize a minimal tree so post-clone update is a no-op-safe.
        os.makedirs(req.target_dir, exist_ok=True)
        return CloneResult(ok=True, resolved_revision="deadbeef")


def _install_fake_registry():
    """Replace the CloneProviderRgy singleton with git + a fake cdb provider."""
    from ivpm.clone.git_clone_provider import GitCloneProvider
    rgy = CloneProviderRgy()
    rgy.register(GitCloneProvider())
    rgy.register(FakeCdbProvider())
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
        FakeCdbProvider.last_request = None

    def tearDown(self):
        _reset_registry()
        super().tearDown()

    def test_provider_args_routed(self):
        target = os.path.join(self.testdir, "cdb_target")
        args, extras = _parse(
            ["clone", "cdb://codeline", target, "-branch", "abc", "-node", "xyz"])
        self.assertEqual(extras, [])
        args.func(args)
        req = FakeCdbProvider.last_request
        self.assertIsNotNone(req)
        self.assertEqual(req.provider_args.branch, "abc")
        self.assertEqual(req.provider_args.node, "xyz")
        self.assertEqual(req.src, "cdb://codeline")

    def test_provider_flag_missing_value_errors(self):
        # '-branch' requires a value; the provider parser rejects it (scoped).
        args, extras = _parse(["clone", "cdb://codeline", "-branch"])
        with self.assertRaises(SystemExit):
            args.func(args)

    def test_forced_provider_overrides_claim(self):
        # A git-shaped URL, but --provider cdb forces cdb.
        target = os.path.join(self.testdir, "forced_target")
        args, extras = _parse(
            ["clone", "--provider", "cdb", "https://github.com/a/b", target])
        args.func(args)
        self.assertIsNotNone(FakeCdbProvider.last_request)

    def test_provider_help_passthrough(self):
        # Non-default provider help is handled before argparse.
        self.assertTrue(_maybe_clone_provider_help(["clone", "cdb://x", "--help"]))
        # `--provider cdb --help` form too.
        self.assertTrue(_maybe_clone_provider_help(
            ["clone", "--provider", "cdb", "--help"]))
        # git (default) falls through to the generic clone help.
        self.assertFalse(_maybe_clone_provider_help(
            ["clone", "https://github.com/a/b", "--help"]))

    def test_default_workspace_name_from_provider(self):
        args, extras = _parse(["clone", "cdb://codeline"])
        args.func(args)
        req = FakeCdbProvider.last_request
        self.assertEqual(os.path.basename(req.target_dir), "cdb_ws")

    def test_git_only_flag_ignored_for_non_git(self):
        # --ssh with a non-git provider should warn+ignore, not fail.
        args, extras = _parse(["clone", "--ssh", "cdb://codeline"])
        args.func(args)
        self.assertIsNotNone(FakeCdbProvider.last_request)


if __name__ == "__main__":
    unittest.main()
