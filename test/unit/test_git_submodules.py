"""Submodules of a git dependency, end to end through ``ivpm update``/``sync``.

A dependency whose tree has a ``.gitmodules`` gets
``git submodule update --init --recursive`` after its clone, on every clone
path: full clone, ``cache: false``, cache miss into the shared cache, and the
pristine clone a patched dependency is built from.  ``ivpm sync`` does the
same after a merge.

The fixture is three local repos, nested two deep so ``--recursive`` is
exercised: ``top`` has submodule ``ext/mid``, which has submodule ``leaf``.
Submodule URLs are ``file://``, which git >= 2.38.1 refuses for submodules
unless ``protocol.file.allow=always``; the tests set that through the
environment so it reaches the git processes ivpm spawns.
"""
import os
import subprocess
from unittest.mock import patch

from .test_base import TestBase
from .test_sync_ops import _SyncArgs

from ivpm.pkg_sync import SyncOutcome
from ivpm.pkg_types.package_git import PackageGit
from ivpm.project_ops import ProjectOps

# Commit hashes depend on timestamps; pin them so the fixture is deterministic.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
}

# Allow file:// submodule transport for every git process, ivpm's included.
_ALLOW_FILE = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
}


class _Base(TestBase):

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, dict(_GIT_ENV, **_ALLOW_FILE))
        env.start()
        self.addCleanup(env.stop)
        # Only the cache test opts into a shared cache.
        os.environ.pop("IVPM_CACHE", None)

        self.repos = os.path.join(self.testdir, "repos")
        self.leaf = self._repo("leaf", {"leaf.txt": "leaf\n"})
        self.mid = self._repo("mid", {"mid.txt": "mid\n"},
                              submodules={"leaf": self.leaf})
        self.top = self._repo("top", {"ivpm.yaml": "package:\n  name: top\n",
                                      "top.txt": "top\n"},
                              submodules={"ext/mid": self.mid})
        self.ws = os.path.join(self.testdir, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        # Cache entries are sealed unwritable.
        for root, dirs, _ in os.walk(self.testdir):
            for d in dirs:
                p = os.path.join(root, d)
                if not os.path.islink(p):
                    try:
                        os.chmod(p, 0o700)
                    except OSError:
                        pass
        return super().tearDown()

    @staticmethod
    def git(cwd, *args):
        subprocess.check_call(["git"] + list(args), cwd=cwd,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)

    def _repo(self, name, files, submodules=None):
        path = os.path.join(self.repos, name)
        os.makedirs(path)
        self.git(path, "init", "-b", "main")
        for rel, content in files.items():
            with open(os.path.join(path, rel), "w") as fp:
                fp.write(content)
        for sm_path, url in (submodules or {}).items():
            self.git(path, "submodule", "add", "file://%s" % url, sm_path)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", "init")
        return path

    def write_ws(self, attrs="", repo=None):
        """Write a workspace ivpm.yaml depending on *repo* (default ``top``)
        with extra dep *attrs* (pre-indented yaml lines)."""
        with open(os.path.join(self.ws, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n"
                     "  name: ws\n"
                     "  dep-sets:\n"
                     "    - name: default-dev\n"
                     "      deps:\n"
                     "        - name: top\n"
                     "          src: git\n"
                     "          url: file://%s\n"
                     "%s" % (repo or self.top, attrs))

    def update(self):
        class Args(object):
            anonymous_git = None
        ProjectOps(self.ws, Args()).update(dep_set="default-dev",
                                           skip_venv=True, args=Args())
        return os.path.join(self.ws, "packages", "top")

    def assertSubmodulesPopulated(self, pkg):
        self.assertTrue(os.path.isfile(os.path.join(pkg, "top.txt")))
        self.assertTrue(os.path.isfile(os.path.join(pkg, "ext", "mid", "mid.txt")),
                        "first-level submodule was not checked out")
        self.assertTrue(
            os.path.isfile(os.path.join(pkg, "ext", "mid", "leaf", "leaf.txt")),
            "nested submodule was not checked out (--recursive)")


class TestClonePaths(_Base):

    def test_full_clone(self):
        self.write_ws()
        self.assertSubmodulesPopulated(self.update())

    def test_cache_false(self):
        self.write_ws("          cache: false\n")
        self.assertSubmodulesPopulated(self.update())

    def test_cache_miss(self):
        cache = os.path.join(self.testdir, "cache")
        os.makedirs(cache)
        os.environ["IVPM_CACHE"] = cache
        self.write_ws("          cache: true\n")
        pkg = self.update()
        self.assertTrue(os.path.islink(pkg), "expected a link into the cache")
        self.assertSubmodulesPopulated(pkg)

    def test_patched_dependency(self):
        patch_path = os.path.join(self.ws, "fix.patch")
        with open(patch_path, "w") as fp:
            fp.write("--- a/top.txt\n"
                     "+++ b/top.txt\n"
                     "@@ -1 +1 @@\n"
                     "-top\n"
                     "+patched\n")
        self.write_ws("          patches:\n"
                      "            - fix.patch\n")
        pkg = self.update()
        with open(os.path.join(pkg, "top.txt")) as fp:
            self.assertEqual(fp.read(), "patched\n")
        self.assertSubmodulesPopulated(pkg)


class TestNoGitmodules(_Base):

    def test_no_submodule_command_without_gitmodules(self):
        seen = []
        orig = PackageGit._run_git

        def spy(s, cmd, *a, **kw):
            seen.append(list(cmd))
            return orig(s, cmd, *a, **kw)

        self.write_ws(repo=self.leaf)
        with patch.object(PackageGit, "_run_git", spy):
            pkg = self.update()
        self.assertTrue(os.path.isfile(os.path.join(pkg, "leaf.txt")))
        self.assertTrue(seen, "spy saw no git commands")
        self.assertFalse([c for c in seen if "submodule" in c])


class TestSync(_Base):

    def test_sync_checks_out_a_newly_added_submodule(self):
        plain = self._repo("plain", {"ivpm.yaml": "package:\n  name: top\n",
                                     "top.txt": "top\n"})
        self.write_ws("          branch: main\n", repo=plain)
        pkg = self.update()
        self.assertFalse(os.path.exists(os.path.join(pkg, "ext")))

        # Upstream gains a submodule after the workspace was populated.
        self.git(plain, "submodule", "add", "file://%s" % self.mid, "ext/mid")
        self.git(plain, "commit", "-m", "add submodule")

        results = ProjectOps(self.ws).sync(args=_SyncArgs())
        r = [r for r in results if r.name == "top"]
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].outcome, SyncOutcome.SYNCED)
        self.assertSubmodulesPopulated(pkg)
