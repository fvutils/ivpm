"""Tests for root-project status via clone providers (root-status-design.md)."""
import os
import sys
import subprocess
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.clone.clone_provider import CloneProvider, ClaimStrength, CloneResult
from ivpm.clone.clone_provider_rgy import CloneProviderRgy
from ivpm.show.info_types import CloneProviderInfo
from ivpm.pkg_status import PkgVcsStatus, git_working_tree_status
from ivpm import package_lock

from .test_base import TestBase
from .test_status import _make_git_repo, _write_lock


# ---------------------------------------------------------------------------
# Fake provider helper
# ---------------------------------------------------------------------------

def _mk_provider(name, probe_strength=ClaimStrength.NONE, root_status=None,
                 root_status_raises=False):
    class _Fake(CloneProvider):
        @classmethod
        def provider_info(cls):
            return CloneProviderInfo(name=cls._name, description="fake")

        def probe(self, root_dir):
            return self._probe

        def root_status(self, root_dir):
            if self._raises:
                raise RuntimeError("boom")
            return self._root_status

        def clone(self, req):
            return CloneResult(ok=True)

    _Fake._name = name
    _Fake._probe = probe_strength
    _Fake._root_status = root_status
    _Fake._raises = root_status_raises
    return _Fake()


# ---------------------------------------------------------------------------
# Lock schema: stamp_root_record + write_lock carry-forward
# ---------------------------------------------------------------------------

class TestRootRecordLock(TestBase):

    def _deps_dir(self):
        d = os.path.join(self.testdir, "packages")
        os.makedirs(d, exist_ok=True)
        return d

    def test_stamp_inserts_root(self):
        deps = self._deps_dir()
        _write_lock(deps, {})
        package_lock.stamp_root_record(
            deps, provider="git", src="https://x/y", resolved_revision="abc123")
        lock = package_lock.read_lock(os.path.join(deps, "package-lock.json"))
        self.assertEqual(lock["root"]["provider"], "git")
        self.assertEqual(lock["root"]["src"], "https://x/y")
        self.assertEqual(lock["root"]["resolved_revision"], "abc123")

    def test_stamp_refreshes_checksum(self):
        deps = self._deps_dir()
        _write_lock(deps, {})
        package_lock.stamp_root_record(deps, provider="git")
        # read_lock warns (not raises) on mismatch; assert it round-trips cleanly
        # by recomputing — the stored sha256 must match the body.
        import hashlib, json
        with open(os.path.join(deps, "package-lock.json")) as f:
            data = json.load(f)
        recorded = data.pop("sha256")
        body = json.dumps(data, indent=2, sort_keys=True)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), recorded)

    def test_write_lock_carries_forward_root(self):
        deps = self._deps_dir()
        _write_lock(deps, {})
        package_lock.stamp_root_record(deps, provider="git", src="u")
        # A subsequent ordinary write_lock (as `ivpm update` does) must preserve it.
        package_lock.write_lock(deps, {})
        lock = package_lock.read_lock(os.path.join(deps, "package-lock.json"))
        self.assertEqual(lock.get("root", {}).get("provider"), "git")

    def test_stamp_no_lock_is_noop(self):
        deps = self._deps_dir()  # no package-lock.json written
        package_lock.stamp_root_record(deps, provider="git")
        self.assertFalse(
            os.path.exists(os.path.join(deps, "package-lock.json")))


# ---------------------------------------------------------------------------
# resolve_root precedence
# ---------------------------------------------------------------------------

class TestResolveRoot(unittest.TestCase):

    def test_recorded_provider_wins(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("demo", probe_strength=ClaimStrength.NONE))
        rgy.register(_mk_provider("git", probe_strength=ClaimStrength.STRONG))
        # recorded 'demo' wins even though it does not probe
        self.assertEqual(
            rgy.resolve_root("/x", recorded_provider="demo").provider_info().name,
            "demo")

    def test_unknown_recorded_falls_through_to_probe(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("git", probe_strength=ClaimStrength.STRONG))
        self.assertEqual(
            rgy.resolve_root("/x", recorded_provider="gone").provider_info().name,
            "git")

    def test_single_strong_probe_wins(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("git", probe_strength=ClaimStrength.STRONG))
        rgy.register(_mk_provider("other", probe_strength=ClaimStrength.NONE))
        self.assertEqual(rgy.resolve_root("/x").provider_info().name, "git")

    def test_strong_beats_weak(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("weak", probe_strength=ClaimStrength.WEAK))
        rgy.register(_mk_provider("strong", probe_strength=ClaimStrength.STRONG))
        self.assertEqual(rgy.resolve_root("/x").provider_info().name, "strong")

    def test_tie_omits(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("a", probe_strength=ClaimStrength.STRONG))
        rgy.register(_mk_provider("b", probe_strength=ClaimStrength.STRONG))
        self.assertIsNone(rgy.resolve_root("/x"))

    def test_no_match_omits(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("a", probe_strength=ClaimStrength.NONE))
        self.assertIsNone(rgy.resolve_root("/x"))


# ---------------------------------------------------------------------------
# Git provider probe + root_status
# ---------------------------------------------------------------------------

class TestGitProviderRoot(TestBase):

    def _provider(self):
        from ivpm.clone.git_clone_provider import GitCloneProvider
        return GitCloneProvider()

    def test_probe_strong_on_git(self):
        root = os.path.join(self.testdir, "wt")
        _make_git_repo(root)
        self.assertEqual(self._provider().probe(root), ClaimStrength.STRONG)

    def test_probe_none_on_plain_dir(self):
        root = os.path.join(self.testdir, "plain")
        os.makedirs(root)
        self.assertEqual(self._provider().probe(root), ClaimStrength.NONE)

    def test_root_status_clean(self):
        root = os.path.join(self.testdir, "clean")
        _make_git_repo(root, branch="main")
        st = self._provider().root_status(root)
        self.assertEqual(st.vcs, "git")
        self.assertEqual(st.branch, "main")
        self.assertFalse(st.is_dirty)

    def test_root_status_dirty(self):
        root = os.path.join(self.testdir, "dirty")
        _make_git_repo(root)
        with open(os.path.join(root, "README.md"), "a") as f:
            f.write("change\n")
        st = self._provider().root_status(root)
        self.assertTrue(st.is_dirty)

    def test_root_status_parity_with_helper(self):
        """root_status() is the same helper per-package status uses."""
        root = os.path.join(self.testdir, "parity")
        _make_git_repo(root)
        a = self._provider().root_status(root)
        b = git_working_tree_status(root, name="(root)")
        self.assertEqual((a.branch, a.commit, a.is_dirty),
                         (b.branch, b.commit, b.is_dirty))


# ---------------------------------------------------------------------------
# End-to-end status integration
# ---------------------------------------------------------------------------

class TestRootStatusIntegration(TestBase):

    def setUp(self):
        super().setUp()
        self._saved_inst = CloneProviderRgy._inst

    def tearDown(self):
        CloneProviderRgy._inst = self._saved_inst
        super().tearDown()

    def _make_project(self, root_block=None):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_root_status
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        deps = os.path.join(self.testdir, "packages")
        _write_lock(deps, {})
        if root_block is not None:
            package_lock.stamp_root_record(deps, **root_block)
        return deps

    def test_git_root_probed(self):
        """A git checkout at the root shows a root header via the probe even
        when the lock records no root block."""
        self._make_project()
        _make_git_repo(self.testdir, branch="main")
        from ivpm.project_ops import ProjectOps
        root_status, _results = ProjectOps(self.testdir).status()
        self.assertIsNotNone(root_status)
        self.assertTrue(root_status.is_root)
        self.assertEqual(root_status.provider, "git")
        self.assertEqual(root_status.vcs, "git")

    def test_non_git_root_omitted(self):
        """A root that no provider recognizes yields no root header."""
        self._make_project()  # testdir is not a git repo
        from ivpm.project_ops import ProjectOps
        root_status, _results = ProjectOps(self.testdir).status()
        self.assertIsNone(root_status)

    def test_recorded_provider_selected(self):
        """A recorded root.provider selects a (non-git) provider for status."""
        fake_status = PkgVcsStatus(name="", src_type="git", path="/x",
                                   vcs="git", branch="rel", commit="deadbee")
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("demo", root_status=fake_status))
        CloneProviderRgy._inst = rgy

        self._make_project(root_block={"provider": "demo", "src": "demo://x"})
        from ivpm.project_ops import ProjectOps
        root_status, _results = ProjectOps(self.testdir).status()
        self.assertIsNotNone(root_status)
        self.assertEqual(root_status.provider, "demo")
        self.assertEqual(root_status.branch, "rel")
        self.assertEqual(root_status.name, "(root)")  # filled in by ProjectOps

    def test_root_status_exception_omitted(self):
        """A provider whose root_status() raises omits the header, not crash."""
        rgy = CloneProviderRgy()
        rgy.register(_mk_provider("demo", root_status_raises=True))
        CloneProviderRgy._inst = rgy

        self._make_project(root_block={"provider": "demo"})
        from ivpm.project_ops import ProjectOps
        root_status, results = ProjectOps(self.testdir).status()
        self.assertIsNone(root_status)
        self.assertEqual(results, [])  # package table still renders


class TestRootStatusRender(unittest.TestCase):

    def test_transcript_renders_root_header(self):
        import io
        from contextlib import redirect_stdout
        from ivpm.status_tui import TranscriptStatusTUI

        root = PkgVcsStatus(name="(root)", src_type="git", path="/x", vcs="git",
                            branch="main", commit="abc1234", is_root=True,
                            provider="git")
        buf = io.StringIO()
        with redirect_stdout(buf):
            TranscriptStatusTUI().render([], verbose=0, root_status=root)
        out = buf.getvalue()
        self.assertIn("Root", out)
        self.assertIn("main", out)

    def test_transcript_root_details_under_verbose(self):
        import io
        from contextlib import redirect_stdout
        from ivpm.status_tui import TranscriptStatusTUI

        root = PkgVcsStatus(
            name="(root)", src_type="git", path="/x", vcs="git", branch="main",
            commit="abc1234", is_dirty=True, is_root=True, provider="git",
            modified=["M tracked.txt"], untracked=["?? newfile.txt"])
        # Without -v: no per-file lines.
        buf0 = io.StringIO()
        with redirect_stdout(buf0):
            TranscriptStatusTUI().render([], verbose=0, root_status=root)
        self.assertNotIn("tracked.txt", buf0.getvalue())
        # With -v: modified + untracked file lines appear.
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            TranscriptStatusTUI().render([], verbose=1, root_status=root)
        out = buf1.getvalue()
        self.assertIn("M tracked.txt", out)
        self.assertIn("?? newfile.txt", out)

    def test_transcript_no_root_header_when_none(self):
        import io
        from contextlib import redirect_stdout
        from ivpm.status_tui import TranscriptStatusTUI

        buf = io.StringIO()
        with redirect_stdout(buf):
            TranscriptStatusTUI().render([], verbose=0, root_status=None)
        self.assertNotIn("Root", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
