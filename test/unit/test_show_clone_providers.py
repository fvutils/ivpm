import io
import os
import sys
import json
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.clone.clone_provider import CloneProvider, CloneOption, CloneResult
from ivpm.clone.clone_provider_rgy import CloneProviderRgy
from ivpm.clone.git_clone_provider import GitCloneProvider
from ivpm.show.info_types import CloneProviderInfo
from ivpm.show.show_clone_providers import ShowCloneProviders


class FakePlugin(CloneProvider):
    @classmethod
    def provider_info(cls):
        return CloneProviderInfo(name="myvcs", description="fake myvcs repo",
                                 schemes=["myvcs"])

    def schemes(self):
        return ["myvcs"]

    def options(self):
        return [
            CloneOption(flags=["-branch"], dest="branch", help="branch",
                        required=True, metavar="V"),
            CloneOption(flags=["-node"], dest="node", help="node",
                        default="head", metavar="V"),
        ]

    def clone(self, req):
        return CloneResult(ok=True)


class _Args:
    def __init__(self, name=None, as_json=False):
        self.name = name
        self.json = as_json
        self.no_rich = True   # force plain output for deterministic assertions


def _install():
    rgy = CloneProviderRgy()
    rgy.register(GitCloneProvider())
    rgy.register(FakePlugin(), provenance={"origin": "ivpm-myvcs",
                                           "provider": "ivpm-myvcs",
                                           "version": "1.2.0"})
    CloneProviderRgy._inst = rgy


class TestShowCloneProviders(unittest.TestCase):
    def setUp(self):
        _install()

    def tearDown(self):
        CloneProviderRgy._inst = None

    def _run(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ShowCloneProviders()(_Args(**kw))
        return buf.getvalue()

    def test_list_shows_git_and_plugin(self):
        out = self._run()
        self.assertIn("git", out)
        self.assertIn("myvcs", out)
        self.assertIn("[default]", out)      # git flagged default
        self.assertIn("myvcs://", out)        # scheme shown
        self.assertIn("ivpm-myvcs", out)      # plugin provenance

    def test_detail_renders_option_table(self):
        out = self._run(name="myvcs")
        self.assertIn("-branch", out)
        self.assertIn("(required)", out)
        self.assertIn("-node", out)
        self.assertIn("head", out)            # default surfaced

    def test_detail_parity_with_parser(self):
        # Every declared option must be accepted by the built parser.
        provider = CloneProviderRgy.inst().get("myvcs")
        info = CloneProviderRgy.inst().info_for("myvcs")
        parser = provider.build_arg_parser()
        ns = parser.parse_args(["-branch", "b", "-node", "n"])
        self.assertEqual(ns.branch, "b")
        self.assertEqual(ns.node, "n")
        # Option names shown in `show` correspond to real parser flags.
        shown = [p.name for p in info.params]
        self.assertIn("-branch", shown)
        self.assertIn("-node", shown)

    def test_json_shape(self):
        out = self._run(as_json=True)
        data = json.loads(out)
        names = [d["name"] for d in data]
        self.assertEqual(set(names), {"git", "myvcs"})
        myvcs = next(d for d in data if d["name"] == "myvcs")
        self.assertEqual(myvcs["schemes"], ["myvcs"])
        self.assertEqual(myvcs["version"], "1.2.0")
        self.assertFalse(myvcs["is_default"])

    def test_unknown_provider_exits(self):
        with self.assertRaises(SystemExit):
            self._run(name="nope")


if __name__ == "__main__":
    unittest.main()
