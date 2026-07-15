import os
import sys
import subprocess
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.clone.git_clone_provider import GitCloneProvider
from ivpm.clone.clone_provider import ClaimStrength, CloneRequest
from ivpm.update_event import UpdateEventDispatcher

ENV = os.environ.copy()


class _Args:
    def __init__(self, **kw):
        self.ssh = kw.get("ssh", False)
        self.anonymous = kw.get("anonymous", False)
        self.git_auth_order = kw.get("git_auth_order", None)


class TestGitClaim(unittest.TestCase):
    def setUp(self):
        self.p = GitCloneProvider()

    def test_strong_locators(self):
        for s in ("git@github.com:fvutils/ivpm.git",
                  "ssh://git@host/x",
                  "git://host/x",
                  "https://github.com/fvutils/ivpm.git"):
            self.assertEqual(self.p.claim(s), ClaimStrength.STRONG, s)

    def test_weak_locators(self):
        for s in ("https://github.com/fvutils/ivpm",
                  "http://host/x",
                  "file:///tmp/x",
                  "/some/local/path"):
            self.assertEqual(self.p.claim(s), ClaimStrength.WEAK, s)

    def test_existing_local_repo_is_strong(self):
        # A directory containing .git claims STRONG even without .git suffix.
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".git"))
        try:
            self.assertEqual(self.p.claim(d), ClaimStrength.STRONG)
        finally:
            import shutil
            shutil.rmtree(d)

    def test_default_workspace_name(self):
        self.assertEqual(self.p.default_workspace_name("https://h/a/b.git"), "b")
        self.assertEqual(self.p.default_workspace_name("https://h/a/b"), "b")
        self.assertEqual(self.p.default_workspace_name("git@h:a/b.git"), "b")


class TestGitOptionsParity(unittest.TestCase):
    def test_options_render_as_params(self):
        p = GitCloneProvider()
        info = p.provider_info()
        param_names = [pi.name for pi in info.params]
        self.assertIn("--ssh", param_names)
        self.assertIn("-a, --anonymous", param_names)
        self.assertIn("--git-auth-order", param_names)
        self.assertTrue(info.is_default)
        self.assertEqual(info.name, "git")

    def test_parser_accepts_declared_flags(self):
        p = GitCloneProvider()
        parser = p.build_arg_parser()
        ns = parser.parse_args(["--ssh", "--git-auth-order", "gh,ssh"])
        self.assertTrue(ns.ssh)
        self.assertEqual(ns.git_auth_order, "gh,ssh")


class TestGitClone(TestBase):
    def _init_git_repo(self, path, branch='main'):
        os.makedirs(path, exist_ok=True)
        subprocess.check_call(["git", "init", "-b", branch], cwd=path, env=ENV)
        subprocess.check_call(["git", "config", "user.email", "you@example.com"], cwd=path, env=ENV)
        subprocess.check_call(["git", "config", "user.name", "Your Name"], cwd=path, env=ENV)
        with open(os.path.join(path, 'ivpm.yaml'), 'w') as f:
            f.write('package:\n  name: sample\n  dep-sets:\n    - name: default-dev\n      deps: []\n')
        subprocess.check_call(["git", "add", "-A"], cwd=path, env=ENV)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path, env=ENV)

    def _req(self, src, target, branch=None):
        return CloneRequest(
            src=src, target_dir=target, branch=branch, provider_args=None,
            event_dispatcher=UpdateEventDispatcher(), suppress_output=False,
            args=_Args())

    def test_clone_empty_target(self):
        src = os.path.join(self.testdir, 'gcp_src')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws')
        res = GitCloneProvider().clone(self._req(src, target))
        self.assertTrue(res.ok)
        self.assertTrue(os.path.isfile(os.path.join(target, 'ivpm.yaml')))
        self.assertIsNotNone(res.resolved_revision)

    def test_clone_reuse_existing(self):
        src = os.path.join(self.testdir, 'gcp_src2')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws2')
        subprocess.check_call(["git", "clone", src, target], env=ENV)
        res = GitCloneProvider().clone(self._req(src, target))
        self.assertTrue(res.ok)

    def test_clone_into_non_empty(self):
        src = os.path.join(self.testdir, 'gcp_src3')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws3')
        os.makedirs(target)
        with open(os.path.join(target, 'keep.txt'), 'w') as f:
            f.write("keep")
        res = GitCloneProvider().clone(self._req(src, target))
        self.assertTrue(res.ok)
        self.assertTrue(os.path.isdir(os.path.join(target, '.git')))
        self.assertTrue(os.path.isfile(os.path.join(target, 'keep.txt')))
        self.assertTrue(os.path.isfile(os.path.join(target, 'ivpm.yaml')))

    def test_clone_rejects_existing_different_src(self):
        from ivpm.diagnostics import SrcLoaderError
        src_a = os.path.join(self.testdir, 'gcp_diff_a')
        src_b = os.path.join(self.testdir, 'gcp_diff_b')
        self._init_git_repo(src_a)
        self._init_git_repo(src_b)
        target = os.path.join(self.testdir, 'gcp_diff_ws')
        subprocess.check_call(["git", "clone", src_a, target], env=ENV)
        with self.assertRaises(SrcLoaderError):
            GitCloneProvider().clone(self._req(src_b, target))

    def test_url_identity_ssh_https_equal(self):
        p = GitCloneProvider()
        self.assertEqual(
            p._git_url_identity("git@github.com:fvutils/ivpm.git"),
            p._git_url_identity("https://github.com/fvutils/ivpm"))

    def test_clone_branch_create(self):
        src = os.path.join(self.testdir, 'gcp_src4')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws4')
        res = GitCloneProvider().clone(self._req(src, target, branch="feature/x"))
        self.assertTrue(res.ok)
        cur = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=target).decode().strip()
        self.assertEqual(cur, "feature/x")


if __name__ == "__main__":
    unittest.main()
