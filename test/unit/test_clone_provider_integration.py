import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.clone.clone_provider import CloneProvider, CloneResult, ClaimStrength
from ivpm.clone.clone_provider_rgy import CloneProviderRgy
from ivpm.show.info_types import CloneProviderInfo


# --- a plugin-style provider discovered via an entry point ------------------ #
class DemoProvider(CloneProvider):
    @classmethod
    def provider_info(cls):
        return CloneProviderInfo(name="demo", description="demo provider",
                                 schemes=["demo"])

    def schemes(self):
        return ["demo"]

    def clone(self, req):
        os.makedirs(req.target_dir, exist_ok=True)
        return CloneResult(ok=True)


class _FakeEP:
    """Mimic importlib.metadata.EntryPoint enough for _load_plugins."""
    def __init__(self, name, loader, dist=None):
        self.name = name
        self.value = "test:%s" % name
        self.group = "ivpm.clone_providers"
        self.dist = dist
        self._loader = loader

    def load(self):
        return self._loader()


def _patch_entry_points(eps):
    """Patch the entry_points symbol _load_plugins imports."""
    def fake_entry_points(group=None):
        return eps if group == "ivpm.clone_providers" else []
    # _load_plugins imports from importlib.metadata (py>=3.10) at call time.
    return mock.patch("importlib.metadata.entry_points", fake_entry_points)


class TestEntryPointDiscovery(unittest.TestCase):

    def tearDown(self):
        CloneProviderRgy._inst = None

    def test_discovers_plugin_via_entry_point(self):
        with _patch_entry_points([_FakeEP("demo", lambda: DemoProvider)]):
            rgy = CloneProviderRgy()
            rgy._load()
        self.assertIn("demo", rgy.provider_names())
        self.assertIn("git", rgy.provider_names())     # built-in still present
        self.assertIs(rgy.resolve("demo://thing"), rgy.get("demo"))

    def test_broken_plugin_is_skipped(self):
        def _boom():
            raise RuntimeError("plugin import failed")
        with _patch_entry_points([_FakeEP("broken", _boom),
                                  _FakeEP("demo", lambda: DemoProvider)]):
            rgy = CloneProviderRgy()
            rgy._load()
        # The broken plugin is skipped; git and the good plugin still work.
        self.assertNotIn("broken", rgy.provider_names())
        self.assertIn("demo", rgy.provider_names())
        self.assertEqual(
            rgy.resolve("https://github.com/a/b").provider_info().name, "git")

    def test_duplicate_scheme_plugin_skipped_not_fatal(self):
        # Two plugins claiming the same scheme: the second load fails but is
        # logged+skipped, leaving the registry usable.
        class DemoDup(DemoProvider):
            @classmethod
            def provider_info(cls):
                return CloneProviderInfo(name="demo2", description="dup",
                                         schemes=["demo"])
        with _patch_entry_points([_FakeEP("demo", lambda: DemoProvider),
                                  _FakeEP("demo2", lambda: DemoDup)]):
            rgy = CloneProviderRgy()
            rgy._load()
        self.assertIn("demo", rgy.provider_names())
        self.assertNotIn("demo2", rgy.provider_names())


class _YamlWritingProvider(CloneProvider):
    """Materializes a minimal ivpm project so post-clone update should fire."""
    @classmethod
    def provider_info(cls):
        return CloneProviderInfo(name="yamlp", description="writes ivpm.yaml",
                                 schemes=["yamlp"])

    def schemes(self):
        return ["yamlp"]

    def clone(self, req):
        os.makedirs(req.target_dir, exist_ok=True)
        with open(os.path.join(req.target_dir, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: sample\n  dep-sets:\n"
                    "    - name: default-dev\n      deps: []\n")
        return CloneResult(ok=True)


class TestPostCloneUpdate(TestBase):

    def setUp(self):
        super().setUp()
        rgy = CloneProviderRgy()
        from ivpm.clone.git_clone_provider import GitCloneProvider
        rgy.register(GitCloneProvider())
        rgy.register(_YamlWritingProvider())
        CloneProviderRgy._inst = rgy

    def tearDown(self):
        CloneProviderRgy._inst = None
        super().tearDown()

    def test_post_clone_update_fires_for_provider_tree(self):
        from ivpm.cmds.cmd_clone import CmdClone

        class Args:
            pass
        args = Args()
        args.src = "yamlp://thing"
        args.workspace_dir = os.path.join(self.testdir, "yamlp_ws")
        args.provider = None
        args.branch = None
        args.here = False
        args.log_level = "NONE"
        args.dep_set = None
        args.definitions = []
        args._clone_extras = []

        called = {}
        real_update = None
        from ivpm.project_ops import ProjectOps

        def fake_update(self, *a, **kw):
            called["dir"] = self.project_dir if hasattr(self, "project_dir") else None
            called["fired"] = True

        with mock.patch.object(ProjectOps, "update", fake_update):
            CmdClone()(args)

        self.assertTrue(called.get("fired"),
                        "post-clone update did not fire for provider-produced ivpm.yaml")
        self.assertTrue(os.path.isfile(
            os.path.join(args.workspace_dir, "ivpm.yaml")))


if __name__ == "__main__":
    unittest.main()
