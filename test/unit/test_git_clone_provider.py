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

    def _req(self, src, target, branch=None, tag=None, revision=None):
        return CloneRequest(
            src=src, target_dir=target, branch=branch, provider_args=None,
            event_dispatcher=UpdateEventDispatcher(), suppress_output=False,
            args=_Args(), tag=tag, revision=revision)

    def _tag(self, path, name, message=None):
        cmd = ["git", "tag"]
        if message is not None:
            cmd += ["-a", "-m", message]
        subprocess.check_call(cmd + [name], cwd=path, env=ENV)

    def _commit(self, path, fname, content):
        with open(os.path.join(path, fname), 'w') as f:
            f.write(content)
        subprocess.check_call(["git", "add", "-A"], cwd=path, env=ENV)
        subprocess.check_call(["git", "commit", "-m", content], cwd=path, env=ENV)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=path, env=ENV).decode().strip()

    @staticmethod
    def _head_sha(path):
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=path).decode().strip()

    @staticmethod
    def _is_detached(path):
        return subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=path).decode().strip() == ""

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

    def test_clone_relative_local_src(self):
        # A relative local source is resolved against the caller's cwd, not the
        # scratch cwd the clone runs from.
        src = os.path.join(self.testdir, 'gcp_src_rel')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws_rel')
        cwd = os.getcwd()
        os.chdir(self.testdir)
        try:
            res = GitCloneProvider().clone(self._req('gcp_src_rel', target))
        finally:
            os.chdir(cwd)
        self.assertTrue(res.ok)
        self.assertTrue(os.path.isfile(os.path.join(target, 'ivpm.yaml')))

    def test_clone_scp_style_does_not_self_clone(self):
        # 'git clone host:repo <cwd>/host:repo' used to make git resolve the
        # source against the destination it had just created, cloning the empty
        # repo into itself and reporting success.  It must fail instead.
        target = os.path.join(self.testdir, 'nosuchhost:nosuchrepo')
        cwd = os.getcwd()
        # Keep the test off the network: ssh transport fails immediately.  A
        # self-clone would take the *local* transport and still "succeed", so
        # this doesn't mask the regression.
        prev_ssh = os.environ.get("GIT_SSH_COMMAND")
        os.environ["GIT_SSH_COMMAND"] = "false"
        os.chdir(self.testdir)
        try:
            res = GitCloneProvider().clone(
                self._req('nosuchhost:nosuchrepo', target))
        finally:
            os.chdir(cwd)
            if prev_ssh is None:
                del os.environ["GIT_SSH_COMMAND"]
            else:
                os.environ["GIT_SSH_COMMAND"] = prev_ssh
        self.assertFalse(res.ok)
        self.assertFalse(os.path.isdir(os.path.join(target, '.git')))

    def test_failed_clone_explains_itself(self):
        # A failure must say what was run, how the locator was read, how the
        # failure was detected, and what git actually reported.
        target = os.path.join(self.testdir, 'gcp_missing_ws')
        src = os.path.join(self.testdir, 'gcp_no_such_repo')
        res = GitCloneProvider().clone(self._req(src, target))
        self.assertFalse(res.ok)
        msg = res.message
        for expect in ("git exit", "source", "interpreted as", "command",
                       "detected by", "git reported:", "does not exist"):
            self.assertIn(expect, msg)

    def test_scp_style_failure_explains_locator_reading(self):
        # The confusing case: 'abc:def' is an SSH host:path to git, not a path.
        # The message must say so, since git's own error never does.
        target = os.path.join(self.testdir, 'abc:def')
        prev_ssh = os.environ.get("GIT_SSH_COMMAND")
        os.environ["GIT_SSH_COMMAND"] = (
            "sh -c 'echo ssh: Could not resolve hostname abc >&2; exit 255'")
        cwd = os.getcwd()
        os.chdir(self.testdir)
        try:
            res = GitCloneProvider().clone(self._req('abc:def', target))
        finally:
            os.chdir(cwd)
            if prev_ssh is None:
                del os.environ["GIT_SSH_COMMAND"]
            else:
                os.environ["GIT_SSH_COMMAND"] = prev_ssh
        self.assertFalse(res.ok)
        self.assertIn("scp-style SSH locator -- host 'abc', path 'def'",
                      res.message)
        self.assertIn("hint: ", res.message)
        self.assertIn("./abc:def", res.message)

    def test_transport_and_locator_descriptions(self):
        p = GitCloneProvider()
        self.assertEqual(p._transport("abc:def"), "ssh")
        self.assertEqual(p._transport("./abc:def"), "local")
        self.assertEqual(p._transport("/a/b"), "local")
        self.assertEqual(p._transport("https://h/a/b.git"), "https")
        self.assertIn("host 'h'", p._describe_locator("https://h/a/b.git"))
        self.assertIn("local path", p._describe_locator("/a/b"))

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

    def test_clone_branch_checks_out_existing_remote_branch(self):
        src = os.path.join(self.testdir, 'gcp_src_rb')
        self._init_git_repo(src)
        subprocess.check_call(["git", "checkout", "-q", "-b", "dev"], cwd=src, env=ENV)
        dev_sha = self._commit(src, "dev.txt", "on dev")
        subprocess.check_call(["git", "checkout", "-q", "main"], cwd=src, env=ENV)

        target = os.path.join(self.testdir, 'gcp_ws_rb')
        res = GitCloneProvider().clone(self._req(src, target, branch="dev"))
        self.assertTrue(res.ok, res.message)
        cur = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=target).decode().strip()
        self.assertEqual(cur, "dev")
        self.assertEqual(self._head_sha(target), dev_sha)

    def test_clone_tag(self):
        src = os.path.join(self.testdir, 'gcp_src_tag')
        self._init_git_repo(src)
        v1_sha = self._commit(src, "a.txt", "v1 content")
        self._tag(src, "v1.0")
        self._commit(src, "a.txt", "later content")

        target = os.path.join(self.testdir, 'gcp_ws_tag')
        res = GitCloneProvider().clone(self._req(src, target, tag="v1.0"))
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self._head_sha(target), v1_sha)
        self.assertTrue(self._is_detached(target))
        self.assertEqual(res.resolved_revision, v1_sha)

    def test_clone_annotated_tag(self):
        src = os.path.join(self.testdir, 'gcp_src_atag')
        self._init_git_repo(src)
        v2_sha = self._commit(src, "a.txt", "v2 content")
        self._tag(src, "v2.0", message="release 2.0")
        self._commit(src, "a.txt", "later content")

        target = os.path.join(self.testdir, 'gcp_ws_atag')
        res = GitCloneProvider().clone(self._req(src, target, tag="v2.0"))
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self._head_sha(target), v2_sha)
        self.assertTrue(self._is_detached(target))

    def test_clone_unknown_tag_fails(self):
        src = os.path.join(self.testdir, 'gcp_src_notag')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws_notag')
        res = GitCloneProvider().clone(self._req(src, target, tag="v9.9"))
        self.assertFalse(res.ok)
        self.assertIn("v9.9", res.message)
        self.assertIn("tag", res.message)

    def test_clone_revision(self):
        src = os.path.join(self.testdir, 'gcp_src_rev')
        self._init_git_repo(src)
        first = self._commit(src, "a.txt", "first")
        self._commit(src, "a.txt", "second")

        target = os.path.join(self.testdir, 'gcp_ws_rev')
        res = GitCloneProvider().clone(self._req(src, target, revision=first))
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self._head_sha(target), first)
        self.assertTrue(self._is_detached(target))

    def test_clone_unknown_revision_fails(self):
        src = os.path.join(self.testdir, 'gcp_src_norev')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws_norev')
        res = GitCloneProvider().clone(
            self._req(src, target, revision="0" * 40))
        self.assertFalse(res.ok)
        self.assertIn("revision", res.message)

    def test_clone_branch_falls_back_to_tag(self):
        """-b naming a tag (not a branch) checks the tag out, detached."""
        src = os.path.join(self.testdir, 'gcp_src_btag')
        self._init_git_repo(src)
        v1_sha = self._commit(src, "a.txt", "v1 content")
        self._tag(src, "v1.0")
        self._commit(src, "a.txt", "later content")

        target = os.path.join(self.testdir, 'gcp_ws_btag')
        res = GitCloneProvider().clone(self._req(src, target, branch="v1.0"))
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self._head_sha(target), v1_sha)
        self.assertTrue(self._is_detached(target))

    def test_clone_branch_prefers_branch_over_same_named_tag(self):
        src = os.path.join(self.testdir, 'gcp_src_bt')
        self._init_git_repo(src)
        self._tag(src, "dup")                      # tag on the initial commit
        subprocess.check_call(["git", "checkout", "-q", "-b", "dup"], cwd=src, env=ENV)
        branch_sha = self._commit(src, "b.txt", "branch content")
        subprocess.check_call(["git", "checkout", "-q", "main"], cwd=src, env=ENV)

        target = os.path.join(self.testdir, 'gcp_ws_bt')
        res = GitCloneProvider().clone(self._req(src, target, branch="dup"))
        self.assertTrue(res.ok, res.message)
        cur = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=target).decode().strip()
        self.assertEqual(cur, "dup")
        self.assertEqual(self._head_sha(target), branch_sha)

    def test_clone_branch_reuses_existing_local_branch(self):
        """Re-cloning into an existing workspace whose branch is already local."""
        src = os.path.join(self.testdir, 'gcp_src_lb')
        self._init_git_repo(src)
        target = os.path.join(self.testdir, 'gcp_ws_lb')
        res = GitCloneProvider().clone(self._req(src, target, branch="local/only"))
        self.assertTrue(res.ok, res.message)
        subprocess.check_call(["git", "checkout", "-q", "main"], cwd=target, env=ENV)

        res = GitCloneProvider().clone(self._req(src, target, branch="local/only"))
        self.assertTrue(res.ok, res.message)
        cur = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=target).decode().strip()
        self.assertEqual(cur, "local/only")


if __name__ == "__main__":
    unittest.main()
