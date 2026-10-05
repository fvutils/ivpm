"""Group-partitioned caching, end to end through ``ivpm update``.

Two users who resolve to different groups must each get their own copy of a
package in a shared cache, each copy owned by that user's group, and each
workspace linked to its own copy.  A preparer returning a ProtectionPolicy is
the whole interface; these tests drive it with an environment variable standing
in for "which user is this".

Also covers re-linking: a reused cache link whose partition no longer matches
the package's policy is moved to the right partition on the next update.
"""
import os
import stat
import subprocess
import unittest
from unittest.mock import patch

from .test_base import TestBase

from ivpm.prepare import PackagePreparer, PrepareResult
from ivpm.prepare.pkg_preparer_rgy import PackagePreparerRgy
from ivpm.project_ops import ProjectOps
from ivpm.protection import PARTITION_PREFIX, ProtectionPolicy

_GIDS = sorted(set(os.getgroups()))
needs_two_groups = unittest.skipIf(
    len(_GIDS) < 2, "needs membership of two groups to simulate two users")

_GROUP_ENV = "IVPM_TEST_PROTECT_GID"


class _EnvGroupPreparer(PackagePreparer):
    """Protects every package with the gid in $IVPM_TEST_PROTECT_GID."""
    name = "test-env-group"
    always = True

    def prepare(self, req):
        gid = os.environ.get(_GROUP_ENV)
        if not gid:
            return PrepareResult.ok()
        return PrepareResult.ok(policy=ProtectionPolicy.for_group(int(gid)))


class _FetchOnlyEnvGroupPreparer(_EnvGroupPreparer):
    """The same policy, but not consulted for reused packages."""
    name = "test-env-group-fetch-only"
    always = False


class _Base(TestBase):

    def setUp(self):
        super().setUp()
        rgy = PackagePreparerRgy.inst()
        self._saved = list(rgy.preparers)
        self._saved_meta = dict(rgy._meta)
        self.cache_dir = os.path.join(self.testdir, "cache")
        os.makedirs(self.cache_dir)
        os.environ["IVPM_CACHE"] = self.cache_dir
        os.environ.pop(_GROUP_ENV, None)
        self.repo = self._git_repo()

    def tearDown(self):
        rgy = PackagePreparerRgy.inst()
        rgy.preparers = self._saved
        rgy._meta = self._saved_meta
        os.environ.pop("IVPM_CACHE", None)
        os.environ.pop(_GROUP_ENV, None)
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

    def register(self, cls):
        PackagePreparerRgy.inst().addPreparer(cls, origin="test")

    def _git_repo(self):
        path = os.path.join(self.testdir, "repos", "tool_pkg")
        os.makedirs(os.path.join(path, "bin"))
        env = dict(os.environ, GIT_AUTHOR_DATE="2020-01-01T00:00:00Z",
                   GIT_COMMITTER_DATE="2020-01-01T00:00:00Z")
        run = lambda *a: subprocess.check_call(
            list(a), cwd=path, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run("git", "init", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "Test")
        with open(os.path.join(path, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n  name: tool_pkg\n")
        with open(os.path.join(path, "bin", "tool"), "w") as fp:
            fp.write("#!/bin/sh\necho hi\n")
        os.chmod(os.path.join(path, "bin", "tool"), 0o755)
        with open(os.path.join(path, "data.txt"), "w") as fp:
            fp.write("data\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        return path

    def workspace(self, name):
        ws = os.path.join(self.testdir, name)
        os.makedirs(ws, exist_ok=True)
        with open(os.path.join(ws, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n"
                     "    name: %s\n"
                     "    dep-sets:\n"
                     "        - name: default-dev\n"
                     "          deps:\n"
                     "            - name: tool_pkg\n"
                     "              url: file://%s\n"
                     "              src: git\n"
                     "              cache: true\n" % (name, self.repo))
        return ws

    def update(self, ws, gid=None):
        # Fetched content lands on the cache filesystem and is published by
        # rename; a tree copy here means data went somewhere it should not.
        with patch("ivpm.fscopy.copy_tree", side_effect=AssertionError(
                "a fetch was copied across filesystems")):
            return self._update(ws, gid)

    def _update(self, ws, gid):
        if gid is None:
            os.environ.pop(_GROUP_ENV, None)
        else:
            os.environ[_GROUP_ENV] = str(gid)

        class Args(object):
            anonymous_git = None
        ProjectOps(ws, Args()).update(dep_set="default-dev", skip_venv=True,
                                      args=Args())
        return os.path.join(ws, "packages", "tool_pkg")

    def partitions(self):
        d = os.path.join(self.cache_dir, "tool_pkg")
        return sorted(n for n in os.listdir(d) if n.startswith(PARTITION_PREFIX))

    def partition_of(self, link):
        """The partition component the link resolves into, or None."""
        rel = os.path.relpath(os.path.realpath(link),
                              os.path.realpath(self.cache_dir))
        parts = rel.split(os.sep)
        self.assertEqual(parts[0], "tool_pkg", "%s is not a cache link" % link)
        return parts[1] if parts[1].startswith(PARTITION_PREFIX) else None

    @staticmethod
    def key(gid):
        return ProtectionPolicy(gid=gid).partition_key()


class TestTwoUsersTwoCopies(_Base):

    @needs_two_groups
    def test_each_group_gets_its_own_protected_copy(self):
        self.register(_EnvGroupPreparer)
        g1, g2 = _GIDS[0], _GIDS[1]
        link1 = self.update(self.workspace("ws1"), g1)
        link2 = self.update(self.workspace("ws2"), g2)

        self.assertEqual(self.partitions(), sorted([self.key(g1), self.key(g2)]))
        for link, gid in ((link1, g1), (link2, g2)):
            self.assertTrue(os.path.islink(link))
            self.assertEqual(self.partition_of(link), self.key(gid))
            entry = os.path.realpath(link)
            self.assertEqual(os.stat(entry).st_gid, gid)
            tool = os.path.join(entry, "bin", "tool")
            self.assertEqual(os.stat(tool).st_gid, gid)
            self.assertTrue(os.access(tool, os.X_OK),
                            "the executable lost its execute bit")
            self.assertEqual(stat.S_IMODE(os.stat(tool).st_mode), 0o550)
            self.assertEqual(stat.S_IMODE(
                os.stat(os.path.join(entry, "data.txt")).st_mode), 0o440)
            self.assertEqual(
                subprocess.check_output([tool]).decode().strip(), "hi")


class TestRelinkOnPolicyChange(_Base):

    @needs_two_groups
    def test_a_changed_group_moves_the_link_to_the_new_partition(self):
        self.register(_EnvGroupPreparer)
        g1, g2 = _GIDS[0], _GIDS[1]
        ws = self.workspace("ws")
        link = self.update(ws, g1)
        old_entry = os.path.realpath(link)
        self.assertEqual(self.partition_of(link), self.key(g1))

        self.update(ws, g2)

        self.assertEqual(self.partition_of(link), self.key(g2))
        # The old partition's entry is another user's; it is left alone.
        self.assertTrue(os.path.isfile(os.path.join(old_entry, "bin", "tool")))
        self.assertEqual(os.stat(old_entry).st_gid, g1)

    @needs_two_groups
    def test_an_unprotected_link_moves_into_a_partition(self):
        ws = self.workspace("ws")
        link = self.update(ws)
        self.assertIsNone(self.partition_of(link))

        self.register(_EnvGroupPreparer)
        self.update(ws, _GIDS[1])
        self.assertEqual(self.partition_of(link), self.key(_GIDS[1]))

    @needs_two_groups
    def test_a_matching_link_is_reused_untouched(self):
        self.register(_EnvGroupPreparer)
        ws = self.workspace("ws")
        link = self.update(ws, _GIDS[1])
        before = (os.readlink(link), os.lstat(link).st_ino)

        self.update(ws, _GIDS[1])
        self.assertEqual((os.readlink(link), os.lstat(link).st_ino), before)

    @needs_two_groups
    def test_a_partial_policy_answer_does_not_move_the_link(self):
        """A preparer skipped for reused packages never got to say what the
        policy is; treating its silence as 'unprotected' would move the link
        out of its partition on every update."""
        self.register(_FetchOnlyEnvGroupPreparer)
        ws = self.workspace("ws")
        link = self.update(ws, _GIDS[1])
        before = os.readlink(link)

        self.update(ws, _GIDS[1])
        self.assertEqual(os.readlink(link), before)

    @needs_two_groups
    def test_a_real_tree_is_never_refreshed_by_the_check(self):
        self.register(_EnvGroupPreparer)
        ws = self.workspace("ws")
        link = self.update(ws, _GIDS[0])
        # Stand-in for a deps-dir copy: a real, writable tree at the path.
        os.unlink(link)
        os.makedirs(os.path.join(link, "bin"))
        marker = os.path.join(link, "local-edit.txt")
        with open(marker, "w") as fp:
            fp.write("mine\n")

        self.update(ws, _GIDS[1])
        self.assertFalse(os.path.islink(link))
        self.assertTrue(os.path.isfile(marker))


if __name__ == "__main__":
    unittest.main()
