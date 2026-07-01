import os
import shutil
import subprocess
import tempfile
import unittest

from .test_base import TestBase

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
ENV = os.environ.copy()
ENV['PYTHONPATH'] = SRCDIR + (':' + ENV['PYTHONPATH'] if 'PYTHONPATH' in ENV else '')


class TestClone(TestBase):

    def _init_git_repo(self, path, files=None, branch='main'):
        os.makedirs(path, exist_ok=True)
        subprocess.check_call(["git", "init", "-b", branch], cwd=path, env=ENV)
        # Configure user for committing in this repo
        subprocess.check_call(["git", "config", "user.email", "you@example.com"], cwd=path, env=ENV)
        subprocess.check_call(["git", "config", "user.name", "Your Name"], cwd=path, env=ENV)
        if files:
            for rel, content in files.items():
                full = os.path.join(path, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write(content)
        # Minimal ivpm project to allow update
        if not os.path.exists(os.path.join(path, 'ivpm.yaml')):
            with open(os.path.join(path, 'ivpm.yaml'), 'w') as f:
                f.write('''\npackage:\n  name: sample\n\n  dep-sets:\n    - name: default-dev\n      deps: []\n''')
        subprocess.check_call(["git", "add", "-A"], cwd=path, env=ENV)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path, env=ENV)

    def _cmd(self):
        from ivpm.cmds.cmd_clone import CmdClone
        from ivpm.update_event import UpdateEventDispatcher
        return CmdClone(), UpdateEventDispatcher()

    def test_url_identity_ssh_https_equal(self):
        from ivpm.cmds.cmd_clone import CmdClone
        c = CmdClone()
        self.assertEqual(
            c._git_url_identity("git@github.com:fvutils/ivpm.git"),
            c._git_url_identity("https://github.com/fvutils/ivpm"))

    def test_populate_empty_target_clones(self):
        src_repo = os.path.join(self.testdir, 'idr_src')
        self._init_git_repo(src_repo)
        target = os.path.join(self.testdir, 'idr_empty')
        cmd, disp = self._cmd()
        rc = cmd._populate_repo(src_repo, src_repo, target, disp, False)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(target, 'ivpm.yaml')))

    def test_populate_reuses_existing_same_src(self):
        src_repo = os.path.join(self.testdir, 'reuse_src')
        self._init_git_repo(src_repo)
        target = os.path.join(self.testdir, 'reuse_ws')
        subprocess.check_call(["git", "clone", src_repo, target], env=ENV)
        cmd, disp = self._cmd()
        rc = cmd._populate_repo(src_repo, src_repo, target, disp, False)
        self.assertEqual(rc, 0)

    def test_populate_rejects_existing_different_src(self):
        from ivpm.diagnostics import SrcLoaderError
        src_a = os.path.join(self.testdir, 'diff_a')
        src_b = os.path.join(self.testdir, 'diff_b')
        self._init_git_repo(src_a)
        self._init_git_repo(src_b)
        target = os.path.join(self.testdir, 'diff_ws')
        subprocess.check_call(["git", "clone", src_a, target], env=ENV)
        cmd, disp = self._cmd()
        with self.assertRaises(SrcLoaderError):
            cmd._populate_repo(src_b, src_b, target, disp, False)

    def test_populate_clones_into_non_empty_dir(self):
        src_repo = os.path.join(self.testdir, 'inplace_src')
        self._init_git_repo(src_repo)
        target = os.path.join(self.testdir, 'inplace_ws')
        os.makedirs(target)
        with open(os.path.join(target, 'preexisting.txt'), 'w') as f:
            f.write("keep me")
        cmd, disp = self._cmd()
        rc = cmd._populate_repo(src_repo, src_repo, target, disp, False)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isdir(os.path.join(target, '.git')))
        self.assertTrue(os.path.isfile(os.path.join(target, 'ivpm.yaml')))
        self.assertTrue(os.path.isfile(os.path.join(target, 'preexisting.txt')))

    @unittest.skip("CI path issues")
    def test_clone_local_git_default(self):
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo)

        # Specify a workspace directory different from source to avoid collision
        wsdir = os.path.join(self.testdir, 'workspace_default')
        subprocess.check_call(["python", "-m", "ivpm", "clone", src_repo, wsdir], cwd=self.testdir, env=ENV)
        self.assertTrue(os.path.isdir(wsdir))
        self.assertTrue(os.path.isfile(os.path.join(wsdir, 'packages', 'ivpm.json')))

    @unittest.skip("CI path issues")
    def test_clone_local_git_branch_create(self):
        src_repo = os.path.join(self.testdir, 'src_repo2')
        self._init_git_repo(src_repo)

        wsdir = os.path.join(self.testdir, 'workspace2')
        subprocess.check_call(["python", "-m", "ivpm", "clone", src_repo, wsdir, "-b", "feature/x"], cwd=self.testdir, env=ENV)
        # Verify branch created
        out = subprocess.check_output(["git", "branch", "--show-current"], cwd=wsdir).decode().strip()
        self.assertEqual(out, "feature/x")

    @unittest.skip("CI path issues")
    def test_clone_local_git_branch_checkout_existing(self):
        src_repo = os.path.join(self.testdir, 'src_repo3')
        self._init_git_repo(src_repo)
        # Create a branch and push-like refs locally by creating the branch in src
        subprocess.check_call(["git", "checkout", "-b", "dev"], cwd=src_repo, env=ENV)
        with open(os.path.join(src_repo, 'README.md'), 'w') as f:
            f.write("dev branch")
        subprocess.check_call(["git", "add", "README.md"], cwd=src_repo, env=ENV)
        subprocess.check_call(["git", "commit", "-m", "dev"], cwd=src_repo, env=ENV)
        # Clone and specify branch dev; since there's no remote, origin/dev won't exist. Our logic checks remote via ls-remote which will fail; fallback will create new local branch.
        wsdir = os.path.join(self.testdir, 'workspace3')
        subprocess.check_call(["python", "-m", "ivpm", "clone", src_repo, wsdir, "-b", "dev"], cwd=self.testdir, env=ENV)
        out = subprocess.check_output(["git", "branch", "--show-current"], cwd=wsdir).decode().strip()
        self.assertEqual(out, "dev")
