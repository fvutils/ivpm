"""GitCloneProvider.read_source_file(): read a file at the requested
branch/tag/revision before the clone, without writing into the target.

See "Supporting clone preparers" in docs/source/clone_providers.rst.
"""
import argparse
import glob
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.clone.clone_provider import CloneRequest
from ivpm.clone.git_clone_provider import GitCloneProvider, _LIVE_READ_DIRS
from ivpm.site_config import reset_site_config


def _git(*args, cwd=None):
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    })
    r = subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                       capture_output=True, text=True)
    return r.stdout.strip()


def _write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


class _ReadBase(TestBase):
    """Bare repo: main = c1 (ivpm.yaml v1, inc.yaml, tag v1) -> c2 (ivpm.yaml
    v2); feat = c2 -> c3 (ivpm.yaml feat, f.txt)."""

    def setUp(self):
        super().setUp()
        # Isolate from the user's git-url-map / auth configuration.
        self._env = mock.patch.dict(os.environ, {
            "IVPM_GIT_URL_MAP": "",
            "IVPM_USER_CONFIG": os.path.join(self.testdir, "nope.yaml"),
            "IVPM_SITE_CONFIG": os.path.join(self.testdir, "nope.yaml"),
        })
        self._env.start()
        reset_site_config()

        work = os.path.join(self.testdir, "work")
        _git("init", "-q", "-b", "main", work)
        _write(work, "ivpm.yaml", "package: {name: p, version: '1'}\n")
        _write(work, "inc.yaml", "inc: 1\n")
        _write(work, "sub/x.txt", "x\n")
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", "c1", cwd=work)
        self.c1 = _git("rev-parse", "HEAD", cwd=work)
        _git("tag", "-a", "-m", "v1", "v1", cwd=work)
        _write(work, "ivpm.yaml", "package: {name: p, version: '2'}\n")
        _git("commit", "-q", "-am", "c2", cwd=work)
        self.c2 = _git("rev-parse", "HEAD", cwd=work)
        _git("checkout", "-q", "-b", "feat", cwd=work)
        _write(work, "ivpm.yaml", "package: {name: p, version: feat}\n")
        _write(work, "f.txt", "f\n")
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", "c3", cwd=work)
        _git("checkout", "-q", "main", cwd=work)

        self.bare = os.path.join(self.testdir, "repo.git")
        _git("clone", "-q", "--bare", work, self.bare)
        _git("config", "uploadpack.allowFilter", "true", cwd=self.bare)
        self.url = "file://" + self.bare
        self.target = os.path.join(self.testdir, "ws")
        self.provider = GitCloneProvider()

    def tearDown(self):
        self.provider.end_source_reads(None)
        self._env.stop()
        reset_site_config()
        super().tearDown()

    def req(self, src=None, branch=None, tag=None, revision=None):
        return CloneRequest(
            src=src or self.url, target_dir=self.target, branch=branch,
            tag=tag, revision=revision, provider_args=None,
            event_dispatcher=None, suppress_output=True,
            args=argparse.Namespace())

    def read(self, path="ivpm.yaml", **kw):
        return self.provider.read_source_file(self.req(**kw), path)


class TestRefs(_ReadBase):

    def test_default_head(self):
        self.assertIn("version: '2'", self.read())

    def test_branch(self):
        self.assertIn("version: feat", self.read(branch="feat"))
        self.assertEqual("f\n", self.read("f.txt", branch="feat"))

    def test_tag(self):
        self.assertIn("version: '1'", self.read(tag="v1"))

    def test_tag_missing(self):
        self.assertIsNone(self.read(tag="nosuch"))

    def test_branch_is_tag(self):
        """-b v1 checks out the tag when no branch has that name."""
        self.assertIn("version: '1'", self.read(branch="v1"))

    def test_branch_new(self):
        """-b nosuch starts a new branch from the default branch."""
        self.assertIn("version: '2'", self.read(branch="nosuch"))

    def test_revision_full_sha_non_tip(self):
        self.assertIn("version: '1'", self.read(revision=self.c1))

    def test_revision_abbrev_sha(self):
        calls = []
        real = GitCloneProvider._run_read

        def spy(cmd, cwd, binary=False):
            calls.append(cmd)
            return real(cmd, cwd, binary=binary)

        with mock.patch.object(GitCloneProvider, "_run_read",
                               staticmethod(spy)):
            text = self.read(revision=self.c1[:8])
        self.assertIn("version: '1'", text)
        self.assertTrue(any("--filter=tree:0" in c for c in calls))

    def test_revision_missing(self):
        self.assertIsNone(self.read(revision="0" * 40))


class TestPaths(_ReadBase):

    def test_nested_path(self):
        self.assertEqual("x\n", self.read("sub/x.txt"))
        self.assertEqual("x\n", self.read("./sub/../sub/x.txt"))

    def test_missing_path(self):
        self.assertIsNone(self.read("nope.yaml"))

    def test_directory_path(self):
        self.assertIsNone(self.read("sub"))

    def test_symlink_path(self):
        work = os.path.join(self.testdir, "work")
        os.symlink("ivpm.yaml", os.path.join(work, "link.yaml"))
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", "link", cwd=work)
        _git("push", "-q", self.bare, "main", cwd=work)
        self.assertIsNone(self.read("link.yaml"))

    def test_escape_path(self):
        with mock.patch.object(GitCloneProvider, "_run_read") as run:
            for p in ("/etc/passwd", "../x", "a/../../x", ".", "", "..",
                      None):
                self.assertIsNone(self.read(p), p)
        run.assert_not_called()


class TestSideEffects(_ReadBase):

    def test_no_writes_to_target(self):
        self.assertIsNotNone(self.read())
        self.assertFalse(os.path.exists(self.target))
        os.makedirs(self.target)
        self.provider.end_source_reads(None)
        self.assertIsNotNone(self.read(branch="feat"))
        self.assertEqual([], os.listdir(self.target))

    def test_blobs_fetched_lazily(self):
        """Only the blob read is fetched; the rest of the tree's blobs are not."""
        self.assertIsNotNone(self.read("inc.yaml"))
        (state,) = self.provider._src_reads.values()
        out = _git("cat-file", "--batch-check", "--batch-all-objects",
                   cwd=state.tmpdir)
        blobs = [ln for ln in out.splitlines() if " blob " in ln]
        self.assertEqual(1, len(blobs))

    def test_cached_single_fetch(self):
        fetches = []
        real = GitCloneProvider._run_read

        def spy(cmd, cwd, binary=False):
            if "fetch" in cmd:
                fetches.append(cmd)
            return real(cmd, cwd, binary=binary)

        with mock.patch.object(GitCloneProvider, "_run_read",
                               staticmethod(spy)):
            self.assertIsNotNone(self.read("ivpm.yaml"))
            self.assertIsNotNone(self.read("inc.yaml"))
        self.assertEqual(1, len(fetches))

    def test_end_source_reads_cleans_tmp(self):
        tmp = os.path.join(self.testdir, "tmp")
        os.makedirs(tmp)
        with mock.patch.object(tempfile, "tempdir", tmp):
            self.assertIsNotNone(self.read())
            self.assertIsNotNone(self.read(branch="feat"))
        dirs = glob.glob(os.path.join(tmp, "ivpm-clone-src-*"))
        self.assertEqual(2, len(dirs))
        self.provider.end_source_reads(None)
        self.assertEqual([], glob.glob(os.path.join(tmp, "ivpm-clone-src-*")))
        self.assertFalse(set(dirs) & _LIVE_READ_DIRS)

    def test_unreachable_source(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(self.read(
                src="file://" + os.path.join(self.testdir, "nope.git")))
        self.assertEqual("", err.getvalue())

    def test_timeout_returns_none(self):
        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        with mock.patch.dict(os.environ, {"IVPM_CLONE_READ_TIMEOUT": "1"}), \
                mock.patch("ivpm.clone.git_clone_provider.subprocess.run",
                           side_effect=hang):
            self.assertIsNone(self.read())


class TestSources(_ReadBase):

    def test_git_url_map_honored(self):
        os.environ["IVPM_GIT_URL_MAP"] = (
            "https://example.invalid/o/=file://%s/" % self.testdir)
        reset_site_config()
        self.assertIn("version: '2'",
                      self.read(src="https://example.invalid/o/repo.git"))

    def test_local_path_source(self):
        cwd = os.getcwd()
        os.chdir(self.testdir)
        try:
            self.assertIn("version: '2'", self.read(src="repo.git"))
            self.assertIn("version: feat",
                          self.read(src=self.bare, branch="feat"))
        finally:
            os.chdir(cwd)

    def test_server_without_filter(self):
        _git("config", "uploadpack.allowFilter", "false", cwd=self.bare)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIn("version: '1'", self.read(tag="v1"))
            self.provider.end_source_reads(None)
            self.assertIn("version: '1'", self.read(revision=self.c1[:8]))
        self.assertEqual("", err.getvalue())


if __name__ == "__main__":
    unittest.main()
