"""`ivpm clone` with clone preparers installed: ordering, refusal, rollback,
and the request a preparer sees."""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase
# Shared clone-dispatch fixtures (functions/classes only, so no test case is
# collected twice).
from .test_cmd_clone_dispatch import (
    FakeVcsProvider, _install_fake_registry, _reset_registry, _parse)

from ivpm.clone.clone_preparer import ClonePreparer, ClonePrepareResult
from ivpm.clone.clone_preparer_rgy import ClonePreparerRgy
from ivpm.clone.clone_provider import CloneResult
from ivpm.yamlsrc import SrcLoaderError


def _install_preparers(*classes):
    rgy = ClonePreparerRgy()
    for c in classes:
        rgy.addPreparer(c)
    ClonePreparerRgy._inst = rgy
    return rgy


class _Recorder(ClonePreparer):
    """Records every request it sees; behavior set per test via class attrs."""
    name = "recorder"
    seen = []
    check_result = None
    make_dir = False
    dir_mode = None

    def check(self, req):
        type(self).seen.append(req)
        return type(self).check_result

    def prepare(self, req):
        if type(self).make_dir:
            os.makedirs(req.target_dir)
            if type(self).dir_mode is not None:
                os.chmod(req.target_dir, type(self).dir_mode)
        return None


def _git(*args, cwd=None):
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    })
    subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Base(TestBase):

    def setUp(self):
        super().setUp()
        _install_fake_registry()
        FakeVcsProvider.last_request = None
        _Recorder.seen = []
        _Recorder.check_result = None
        _Recorder.make_dir = False
        _Recorder.dir_mode = None

    def tearDown(self):
        _reset_registry()
        ClonePreparerRgy._inst = None
        super().tearDown()

    def _clone(self, argv):
        args, extras = _parse(argv)
        self.assertEqual([], extras)
        args.func(args)


class TestClonePreparersCmd(_Base):

    def test_no_preparers_unchanged(self):
        _install_preparers()
        target = os.path.join(self.testdir, "ws")
        self._clone(["clone", "myvcs://repo", target])
        self.assertIsNotNone(FakeVcsProvider.last_request)
        self.assertTrue(os.path.isdir(target))

    def test_check_deny_no_clone_no_dir(self):
        _Recorder.check_result = ClonePrepareResult.deny("not an ER area")
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "a", "ws")
        with self.assertRaises(SrcLoaderError) as ctx:
            self._clone(["clone", "myvcs://repo", target])
        self.assertIn("not an ER area", str(ctx.exception))
        self.assertIsNone(FakeVcsProvider.last_request)
        self.assertFalse(os.path.exists(os.path.join(self.testdir, "a")))

    def test_multi_deny_one_message(self):
        class A(ClonePreparer):
            name = "alpha"

            def check(self, req):
                return ClonePrepareResult.deny("wrong group")

        class B(ClonePreparer):
            name = "beta"

            def check(self, req):
                return ClonePrepareResult.deny("wrong partition")
        _install_preparers(A, B)
        with self.assertRaises(SrcLoaderError) as ctx:
            self._clone(["clone", "myvcs://repo",
                         os.path.join(self.testdir, "ws")])
        text = str(ctx.exception)
        for s in ("alpha", "wrong group", "beta", "wrong partition"):
            self.assertIn(s, text)
        self.assertIsNone(FakeVcsProvider.last_request)

    def test_warn_continues(self):
        _Recorder.check_result = ClonePrepareResult.warn("manifest unreadable")
        _install_preparers(_Recorder)
        with mock.patch("ivpm.msg.warning") as warn:
            self._clone(["clone", "myvcs://repo",
                         os.path.join(self.testdir, "ws")])
        self.assertIn("manifest unreadable", warn.call_args[0][0])
        self.assertIsNotNone(FakeVcsProvider.last_request)

    def test_check_raises_fails_closed(self):
        class Boom(ClonePreparer):
            name = "boom"

            def check(self, req):
                raise RuntimeError("no policy server")
        _install_preparers(Boom)
        with self.assertRaises(SrcLoaderError) as ctx:
            self._clone(["clone", "myvcs://repo",
                         os.path.join(self.testdir, "ws")])
        self.assertIn("no policy server", str(ctx.exception))
        self.assertIsNone(FakeVcsProvider.last_request)

    def test_prepared_dir_used_by_clone(self):
        _Recorder.make_dir = True
        _Recorder.dir_mode = 0o2750
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")
        self._clone(["clone", "myvcs://repo", target])
        self.assertIsNotNone(FakeVcsProvider.last_request)
        self.assertEqual(0o2750, os.stat(target).st_mode & 0o7777)

    def test_clone_failure_rolls_back_prepared_dir(self):
        _Recorder.make_dir = True
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "a", "b", "ws")
        with mock.patch.object(FakeVcsProvider, "clone",
                               return_value=CloneResult(ok=False,
                                                        message="no such node")):
            with self.assertRaises(SrcLoaderError) as ctx:
                self._clone(["clone", "myvcs://repo", target])
        self.assertIn("no such node", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.testdir, "a")))

    def test_clone_exception_rolls_back(self):
        _Recorder.make_dir = True
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")
        with mock.patch.object(FakeVcsProvider, "clone",
                               side_effect=RuntimeError("provider crashed")):
            with self.assertRaises(RuntimeError):
                self._clone(["clone", "myvcs://repo", target])
        self.assertFalse(os.path.exists(target))

    def test_clone_failure_keeps_preexisting_dir(self):
        target = os.path.join(self.testdir, "ws")
        os.makedirs(target)
        _install_preparers(_Recorder)
        with mock.patch.object(FakeVcsProvider, "clone",
                               return_value=CloneResult(ok=False, message="x")):
            with self.assertRaises(SrcLoaderError):
                self._clone(["clone", "myvcs://repo", target])
        self.assertTrue(os.path.isdir(target))

    def test_post_clone_update_failure_no_rollback(self):
        _Recorder.make_dir = True
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")

        def _clone_with_content(req):
            open(os.path.join(req.target_dir, "content"), "w").close()
            return CloneResult(ok=True)
        with mock.patch.object(FakeVcsProvider, "clone",
                               side_effect=_clone_with_content), \
                mock.patch("ivpm.cmds.cmd_clone.CmdClone._post_clone_update",
                           side_effect=RuntimeError("update failed")):
            with self.assertRaises(RuntimeError):
                self._clone(["clone", "myvcs://repo", target])
        self.assertTrue(os.path.isfile(os.path.join(target, "content")))

    def test_request_fields(self):
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")
        # The fake tree has no ivpm.yaml, so the update would refuse '-d'.
        with mock.patch("ivpm.cmds.cmd_clone.CmdClone._post_clone_update"):
            self._clone(["clone", "myvcs://repo", target, "-b", "feat",
                         "-d", "a,b", "-d", "c", "-D", "X=1"])
        req = _Recorder.seen[0]
        self.assertEqual("myvcs://repo", req.src)
        self.assertEqual("myvcs://repo", req.url)
        self.assertEqual("myvcs", req.provider)
        self.assertEqual(os.path.abspath(target), req.target_dir)
        self.assertFalse(req.target_nonempty)
        self.assertEqual(("feat", None, None),
                         (req.branch, req.tag, req.revision))
        self.assertEqual(["a", "b", "c"], req.dep_set)
        self.assertEqual({"X": "1"}, req.cli_overrides)
        self.assertIsNone(req.prompt_callback)
        # The provider has no remote read: read_file reports "unknown".
        self.assertIsNone(req.read_file("ivpm.yaml"))

    def test_tag_and_revision_fields(self):
        _install_preparers(_Recorder)
        self._clone(["clone", "myvcs://repo",
                     os.path.join(self.testdir, "w1"), "-t", "v1"])
        self._clone(["clone", "myvcs://repo",
                     os.path.join(self.testdir, "w2"), "-r", "abc123"])
        self.assertEqual((None, "v1", None),
                         (_Recorder.seen[0].branch, _Recorder.seen[0].tag,
                          _Recorder.seen[0].revision))
        self.assertEqual("abc123", _Recorder.seen[1].revision)

    def test_url_is_mapped(self):
        _install_preparers(_Recorder)
        with mock.patch.dict(os.environ, {
                "IVPM_GIT_URL_MAP": "myvcs://repo=myvcs://mirror/repo"}):
            self._clone(["clone", "myvcs://repo",
                         os.path.join(self.testdir, "ws")])
        req = _Recorder.seen[0]
        self.assertEqual("myvcs://repo", req.src)
        self.assertEqual("myvcs://mirror/repo", req.url)

    def test_here_sets_target_nonempty(self):
        _install_preparers(_Recorder)
        here = os.path.join(self.testdir, "here")
        os.makedirs(here)
        open(os.path.join(here, "notes.txt"), "w").close()
        cwd = os.getcwd()
        os.chdir(here)
        try:
            self._clone(["clone", "myvcs://repo", "--here"])
        finally:
            os.chdir(cwd)
        self.assertTrue(_Recorder.seen[0].target_nonempty)
        self.assertEqual(os.path.realpath(here),
                         os.path.realpath(_Recorder.seen[0].target_dir))

    def test_preparers_run_before_tui(self):
        order = []

        class Rec(ClonePreparer):
            name = "rec"

            def check(self, req):
                order.append("check")

        _install_preparers(Rec)
        from ivpm.cmds import cmd_clone
        real = cmd_clone.create_update_tui

        def _tui(*a, **kw):
            order.append("tui")
            return real(*a, **kw)
        with mock.patch.object(cmd_clone, "create_update_tui", side_effect=_tui):
            self._clone(["clone", "myvcs://repo",
                         os.path.join(self.testdir, "ws")])
        self.assertEqual(["check", "tui"], order)

    def test_existing_workspace_refused_before_preparers(self):
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")
        os.makedirs(os.path.join(target, "packages"))
        with open(os.path.join(target, "packages", "package-lock.json"), "w") as f:
            f.write("{}")
        with self.assertRaises(SrcLoaderError):
            self._clone(["clone", "myvcs://repo", target])
        self.assertEqual([], _Recorder.seen)

    def test_load_error_is_fatal(self):
        class _BadEP(object):
            name = "bad"
            value = "nope:X"
            dist = None

            def load(self):
                raise ImportError("no such module")

        ClonePreparerRgy._inst = None
        import ivpm._compat as compat
        saved = compat.entry_points
        compat.entry_points = lambda group=None: (
            [_BadEP()] if group == "ivpm.clone_preparers" else saved(group=group))
        try:
            with self.assertRaises(SrcLoaderError) as ctx:
                self._clone(["clone", "myvcs://repo",
                             os.path.join(self.testdir, "ws")])
        finally:
            compat.entry_points = saved
        self.assertIn("Failed to load clone preparer 'bad'", str(ctx.exception))
        self.assertIsNone(FakeVcsProvider.last_request)

    def test_end_source_reads_called_on_deny(self):
        _Recorder.check_result = ClonePrepareResult.deny("no")
        _install_preparers(_Recorder)
        with mock.patch.object(FakeVcsProvider, "end_source_reads",
                               autospec=True) as end:
            with self.assertRaises(SrcLoaderError):
                self._clone(["clone", "myvcs://repo",
                             os.path.join(self.testdir, "ws")])
        self.assertEqual(1, end.call_count)


class TestGitCloneIntoPreparedDir(_Base):
    """The built-in git provider, cloning into a directory a preparer made."""

    def setUp(self):
        super().setUp()
        work = os.path.join(self.testdir, "work")
        os.makedirs(work)
        _git("init", "-q", "-b", "main", work)
        os.makedirs(os.path.join(work, "src"))
        with open(os.path.join(work, "src", "a.txt"), "w") as f:
            f.write("a\n")
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", "one", cwd=work)
        self.bare = os.path.join(self.testdir, "repo.git")
        _git("clone", "-q", "--bare", work, self.bare)

    def test_git_clone_into_prepared_dir(self):
        _Recorder.make_dir = True
        _Recorder.dir_mode = 0o2770
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")
        self._clone(["clone", "file://" + self.bare, target])
        self.assertTrue(os.path.isfile(os.path.join(target, "src", "a.txt")))
        # A plain clone into the empty directory, not git's clone-in-place.
        self.assertEqual(0o2770, os.stat(target).st_mode & 0o7777)

    def test_group_inherited_through_clone(self):
        alt = [g for g in os.getgroups() if g != os.getgid()]
        if not alt:
            self.skipTest("user has no secondary group")
        gid = alt[0]

        class GroupPrep(ClonePreparer):
            name = "group"

            def prepare(self, req):
                os.makedirs(req.target_dir)
                os.chown(req.target_dir, -1, gid)
                os.chmod(req.target_dir, 0o2770)

        _install_preparers(GroupPrep)
        target = os.path.join(self.testdir, "ws")
        self._clone(["clone", "file://" + self.bare, target])
        wrong = []
        for root, dirs, files in os.walk(target):
            for n in dirs + files:
                p = os.path.join(root, n)
                if os.lstat(p).st_gid != gid:
                    wrong.append(p)
        self.assertEqual([], wrong)

    def test_failed_git_clone_rolls_back(self):
        _Recorder.make_dir = True
        _install_preparers(_Recorder)
        target = os.path.join(self.testdir, "ws")
        with mock.patch("ivpm.clone.git_clone_provider.GitCloneProvider."
                        "_probe_lines", return_value=[]):
            with self.assertRaises(SrcLoaderError):
                self._clone(["clone", "file://" + self.testdir + "/nope.git",
                             target])
        self.assertFalse(os.path.exists(target))


class TestReadSourceThroughClone(_Base):
    """A preparer reading the source through req.read_file() during a real
    `ivpm clone` of a local bare repo."""

    def setUp(self):
        super().setUp()
        work = os.path.join(self.testdir, "work")
        _git("init", "-q", "-b", "main", work)
        with open(os.path.join(work, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: p\n")
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", "one", cwd=work)
        _git("checkout", "-q", "-b", "feat", cwd=work)
        with open(os.path.join(work, "ivpm.yaml"), "w") as f:
            f.write("package:\n  name: p\n# export-restricted\n")
        _git("commit", "-q", "-am", "two", cwd=work)
        _git("checkout", "-q", "main", cwd=work)
        self.bare = os.path.join(self.testdir, "repo.git")
        _git("clone", "-q", "--bare", work, self.bare)
        self.tmp = os.path.join(self.testdir, "tmp")
        os.makedirs(self.tmp)

    def _clone_with_tmp(self, argv):
        import tempfile
        with mock.patch.object(tempfile, "tempdir", self.tmp):
            self._clone(argv)

    def test_check_refuses_from_manifest(self):
        seen = []

        class Export(ClonePreparer):
            name = "export"

            def check(self, req):
                text = req.read_file("ivpm.yaml")
                seen.append(text)
                if text and "export-restricted" in text:
                    return ClonePrepareResult.deny("export-restricted")

        _install_preparers(Export)
        target = os.path.join(self.testdir, "ws")
        with self.assertRaises(SrcLoaderError):
            self._clone_with_tmp(["clone", "-b", "feat", "file://" + self.bare,
                                  target])
        self.assertFalse(os.path.exists(target))
        self.assertEqual([], os.listdir(self.tmp))
        self.assertIn("export-restricted", seen[0])

    def test_check_passes_and_clone_proceeds(self):
        seen = []

        class Export(ClonePreparer):
            name = "export"

            def check(self, req):
                seen.append(req.read_file("ivpm.yaml"))

        _install_preparers(Export)
        target = os.path.join(self.testdir, "ws")
        with mock.patch("ivpm.cmds.cmd_clone.CmdClone._post_clone_update"):
            self._clone_with_tmp(["clone", "file://" + self.bare, target])
        self.assertTrue(os.path.isfile(os.path.join(target, "ivpm.yaml")))
        self.assertEqual("package:\n  name: p\n", seen[0])
        self.assertEqual([], [n for n in os.listdir(self.tmp)
                              if n.startswith("ivpm-clone-src-")])

if __name__ == "__main__":
    unittest.main()
