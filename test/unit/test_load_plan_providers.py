"""Source providers consult the load planner instead of stat-ing.

The behaviour these pin down is the one the old ``os.path.exists(pkg_dir)``
checks got wrong: an **empty** package directory is not a loaded package. Every
other residency state must decide exactly as it did before, which is what makes
this refactor behaviour-preserving.

Uses only offline sources -- ``src: dir`` and a local ``file://`` git repo -- so
nothing here depends on the network.

See pkg-prepare-impl-plan.md P2.
"""
import os
import shutil
import subprocess

from .test_base import TestBase

from ivpm.load_plan import LoadAction, LoadPlanner, LoadState, clear_prepared_dir


# Pinned so a commit is a pure function of tree+message+parent: a commit hash
# embeds the timestamps, and two fixtures built either side of a second
# boundary would otherwise diverge under load.
_GENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t.com",
    GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@t.com",
    GIT_AUTHOR_DATE="2020-01-01T00:00:00+0000",
    GIT_COMMITTER_DATE="2020-01-01T00:00:00+0000",
)


def _git(cwd, *args):
    return subprocess.run(("git",) + args, cwd=cwd, env=_GENV,
                          capture_output=True, text=True, check=True)


class _ProviderTestBase(TestBase):

    def deps_dir(self):
        return os.path.join(self.testdir, "packages")

    def pkg_dir(self, name):
        return os.path.join(self.deps_dir(), name)

    def assertPopulated(self, name):
        path = self.pkg_dir(name)
        self.assertTrue(os.path.isdir(path) or os.path.islink(path),
                        "%s is missing" % path)
        self.assertTrue(os.listdir(path), "%s is empty" % path)

    def emptyDirFor(self, name):
        """What a pre-populate step leaves behind: an empty package dir."""
        path = self.pkg_dir(name)
        if os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        os.makedirs(path)
        return path


class TestSrcDirProvider(_ProviderTestBase):
    """`src: dir` -- package_dir.py, which both decides and creates."""

    def _write_manifest(self, link):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: dir_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: leaf1\n"
                    "                  url: file://${DATA_DIR}/leaf_proj1\n"
                    "                  src: dir\n"
                    "                  link: %s\n" % ("true" if link else "false"))

    def test_empty_directory_is_repopulated(self):
        """Broken before: an empty dir read as 'already loaded' and was skipped."""
        self._write_manifest(link=False)
        self.ivpm_update(skip_venv=True)
        self.assertPopulated("leaf1")

        self.emptyDirFor("leaf1")
        self.ivpm_update(skip_venv=True)
        self.assertPopulated("leaf1")

    def test_populated_directory_is_left_alone(self):
        self._write_manifest(link=False)
        self.ivpm_update(skip_venv=True)

        marker = os.path.join(self.pkg_dir("leaf1"), "MARKER")
        with open(marker, "w") as fp:
            fp.write("do not clobber me")

        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(marker),
                        "a resident package was re-fetched over")

    def test_symlinked_package_is_reused(self):
        self._write_manifest(link=True)
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.islink(self.pkg_dir("leaf1")))

        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.islink(self.pkg_dir("leaf1")),
                        "the symlink was replaced")


class TestGitProvider(_ProviderTestBase):
    """`src: git` against a local file:// repo -- package_git.py."""

    def setUp(self):
        super().setUp()
        self.origin = os.path.join(self.testdir, "origin")
        os.makedirs(self.origin)
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "config", "commit.gpgsign", "false")
        with open(os.path.join(self.origin, "f.txt"), "w") as fp:
            fp.write("content")
        _git(self.origin, "add", "f.txt")
        _git(self.origin, "commit", "-q", "-m", "init")

        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: git_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: gitdep\n"
                    "                  url: file://%s\n"
                    "                  src: git\n" % self.origin)

    def test_clone_then_reuse(self):
        self.ivpm_update(skip_venv=True)
        self.assertPopulated("gitdep")
        self.assertTrue(os.path.isdir(os.path.join(self.pkg_dir("gitdep"), ".git")))

        marker = os.path.join(self.pkg_dir("gitdep"), "MARKER")
        with open(marker, "w") as fp:
            fp.write("x")
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(marker), "a resident git dep was re-cloned")

    def test_empty_directory_is_recloned(self):
        """git clone accepts an existing empty directory, so this just works
        once the decision stops calling an empty dir 'loaded'."""
        self.ivpm_update(skip_venv=True)
        self.assertPopulated("gitdep")

        self.emptyDirFor("gitdep")
        self.ivpm_update(skip_venv=True)
        self.assertPopulated("gitdep")
        self.assertTrue(os.path.isdir(os.path.join(self.pkg_dir("gitdep"), ".git")))

    def test_deleted_directory_is_recloned(self):
        self.ivpm_update(skip_venv=True)
        shutil.rmtree(self.pkg_dir("gitdep"))
        self.ivpm_update(skip_venv=True)
        self.assertPopulated("gitdep")


class TestDriftReporting(_ProviderTestBase):
    """Drift is now classified per package, in every scope."""

    def test_transitive_drift_is_detected(self):
        """The old pre-resolution pass only saw the root dep-set, so drift in a
        transitive dependency was invisible to it."""
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: drift_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: nested_toolB\n"
                    "                  url: file://${DATA_DIR}/nested_toolB\n"
                    "                  src: dir\n"
                    "                  link: false\n")
        self.ivpm_update(skip_venv=True)

        lock_path = os.path.join(self.deps_dir(), "package-lock.json")
        self.assertTrue(os.path.isfile(lock_path))

        import json
        with open(lock_path) as fp:
            lock = json.load(fp)

        # Every package that was actually materialized, root and transitive.
        keys = [k for k, e in lock["packages"].items()
                if os.path.isdir(os.path.join(self.deps_dir(), k))]
        self.assertTrue(len(keys) > 1,
                        "expected transitive deps in the lock; got %r"
                        % list(lock["packages"].keys()))

        # Rewrite one transitive entry's recorded path so it no longer matches.
        transitive = sorted(k for k in keys if k != "nested_toolB")
        self.assertTrue(transitive, "no transitive dependency to drift")
        target = transitive[0]

        planner = LoadPlanner(self.deps_dir(), lock)

        from ivpm.pkg_types.package_dir import PackageDir
        pkg = PackageDir(target.rsplit("/", 1)[-1])
        pkg.src_type = "dir"
        pkg.url = "file:///somewhere/else/entirely"
        pkg.scope_key = target
        pkg.path = os.path.join(self.deps_dir(), target)

        decision = planner.decide(pkg)
        self.assertIs(decision.state, LoadState.RESIDENT_DRIFTED)
        self.assertIs(decision.action, LoadAction.REFRESH,
                      "a drifted spec must be applied, not skipped")
        self.assertIn(target, planner.drifted())


class TestClearPreparedDir(TestBase):
    """The helper the materializers use to write over a prepared directory."""

    def test_removes_empty_directory(self):
        path = os.path.join(self.testdir, "empty")
        os.makedirs(path)
        self.assertTrue(clear_prepared_dir(path))
        self.assertFalse(os.path.exists(path))

    def test_leaves_populated_directory(self):
        path = os.path.join(self.testdir, "full")
        os.makedirs(path)
        with open(os.path.join(path, "f"), "w") as fp:
            fp.write("x")
        self.assertFalse(clear_prepared_dir(path))
        self.assertTrue(os.path.isdir(path))

    def test_leaves_symlink(self):
        target = os.path.join(self.testdir, "target")
        os.makedirs(target)
        link = os.path.join(self.testdir, "link")
        os.symlink(target, link)
        self.assertFalse(clear_prepared_dir(link))
        self.assertTrue(os.path.islink(link))

    def test_missing_path_is_a_noop(self):
        self.assertFalse(
            clear_prepared_dir(os.path.join(self.testdir, "nope")))

    def test_leaves_plain_file(self):
        path = os.path.join(self.testdir, "afile")
        with open(path, "w") as fp:
            fp.write("x")
        self.assertFalse(clear_prepared_dir(path))
        self.assertTrue(os.path.isfile(path))


class TestPreserveDirMode(TestBase):
    """copytree(dirs_exist_ok=True) copies the *source* dir's mode onto the
    destination root, silently clearing what a prepare step configured."""

    def test_mode_survives_copytree(self):
        import shutil
        import stat
        from ivpm.load_plan import preserve_dir_mode

        src = os.path.join(self.testdir, "src")
        os.makedirs(src)
        with open(os.path.join(src, "f"), "w") as fp:
            fp.write("x")

        dst = os.path.join(self.testdir, "dst")
        os.makedirs(dst)
        os.chmod(dst, os.stat(dst).st_mode | stat.S_ISGID)

        with preserve_dir_mode(dst):
            shutil.copytree(src, dst, dirs_exist_ok=True)

        self.assertTrue(os.stat(dst).st_mode & stat.S_ISGID)
        self.assertTrue(os.path.isfile(os.path.join(dst, "f")))

    def test_without_the_guard_the_bit_is_lost(self):
        """Documents why the guard exists, so nobody removes it as redundant."""
        import shutil
        import stat

        src = os.path.join(self.testdir, "src2")
        os.makedirs(src)
        with open(os.path.join(src, "f"), "w") as fp:
            fp.write("x")

        dst = os.path.join(self.testdir, "dst2")
        os.makedirs(dst)
        os.chmod(dst, os.stat(dst).st_mode | stat.S_ISGID)

        shutil.copytree(src, dst, dirs_exist_ok=True)
        self.assertFalse(os.stat(dst).st_mode & stat.S_ISGID)

    def test_missing_path_is_a_noop(self):
        from ivpm.load_plan import preserve_dir_mode
        with preserve_dir_mode(os.path.join(self.testdir, "nope")):
            pass

    def test_restores_on_exception(self):
        import stat
        from ivpm.load_plan import preserve_dir_mode

        path = os.path.join(self.testdir, "d")
        os.makedirs(path)
        os.chmod(path, os.stat(path).st_mode | stat.S_ISGID)

        with self.assertRaises(RuntimeError):
            with preserve_dir_mode(path):
                os.chmod(path, 0o700)
                raise RuntimeError("boom")

        self.assertTrue(os.stat(path).st_mode & stat.S_ISGID)
