"""End to end: a real `ivpm clone` of a local bare repo through the git
provider, with a clone preparer that takes its policy from the source's
manifest (req.config()) before anything is cloned."""
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_clone_preparers_cmd import _Base, _git, _install_preparers

from ivpm.clone.clone_preparer import (
    ClonePreparer, ClonePrepareResult, ManifestLoadError)
from ivpm.site_config import reset_site_config
from ivpm.yamlsrc import SrcLoaderError


ROOT = """\
package:
  name: p
  include: [cfg/policy.yaml]
  dep-sets:
    - name: default-dev
      with:
        python: { venv: false }
      deps: []
"""

POLICY_MAIN = """\
package:
  with:
    acme:
      export: open
"""

POLICY_FEAT = """\
package:
  with:
    acme:
      export: restricted
"""


class Acme(ClonePreparer):
    """Refuses export-restricted sources; puts the workspace in a group."""
    name = "acme"
    group = None
    seen = []

    def check(self, req):
        try:
            cfg = req.config()
        except ManifestLoadError as e:
            return ClonePrepareResult.warn("no policy: %s" % e)
        type(self).seen.append(cfg)
        if cfg.get("export") == "restricted":
            return ClonePrepareResult.deny(
                "export-restricted sources may not be cloned here",
                "clone into an export-controlled area")

    def prepare(self, req):
        if type(self).group is None:
            return None
        os.makedirs(req.target_dir)
        os.chown(req.target_dir, -1, type(self).group)
        os.chmod(req.target_dir, 0o2770)


def _secondary_group():
    primary = os.getgid()
    for g in os.getgroups():
        if g != primary:
            return g
    return None


class TestClonePreparerE2E(_Base):

    def setUp(self):
        super().setUp()
        work = os.path.join(self.testdir, "work")
        _git("init", "-q", "-b", "main", work)
        os.makedirs(os.path.join(work, "cfg"))
        self._write(work, "ivpm.yaml", ROOT)
        self._write(work, "cfg/policy.yaml", POLICY_MAIN)
        self._write(work, "src/a.txt", "a\n")
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", "one", cwd=work)
        _git("checkout", "-q", "-b", "feat", cwd=work)
        self._write(work, "cfg/policy.yaml", POLICY_FEAT)
        _git("commit", "-q", "-am", "two", cwd=work)
        _git("checkout", "-q", "main", cwd=work)
        self.bare = os.path.join(self.testdir, "repo.git")
        _git("clone", "-q", "--bare", work, self.bare)
        _git("config", "uploadpack.allowFilter", "true", cwd=self.bare)

        self.tmp = os.path.join(self.testdir, "tmp")
        os.makedirs(self.tmp)
        self.target = os.path.join(self.testdir, "ws")
        Acme.group = None
        Acme.seen = []
        _install_preparers(Acme)

        env = {"IVPM_USER_CONFIG": os.path.join(self.testdir, "no-user.toml"),
               "IVPM_SITE_CONFIG": os.path.join(self.testdir, "no-site.toml")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        os.environ.pop("IVPM_GIT_URL_MAP", None)
        reset_site_config()

    def tearDown(self):
        self._env.stop()
        reset_site_config()
        super().tearDown()

    @staticmethod
    def _write(root, rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def _clone_with_tmp(self, argv):
        with mock.patch.object(tempfile, "tempdir", self.tmp):
            self._clone(argv)

    def _assert_no_scratch(self):
        self.assertEqual([], [n for n in os.listdir(self.tmp)
                              if n.startswith("ivpm-clone-src-")])

    def test_check_refuses_from_included_policy(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._clone_with_tmp(["clone", "-b", "feat", "file://" + self.bare,
                                  self.target])
        self.assertIn("export-restricted", str(ctx.exception))
        self.assertEqual([{"export": "restricted"}], Acme.seen)
        self.assertFalse(os.path.exists(self.target))
        self._assert_no_scratch()

    def test_check_passes_prepare_sets_group(self):
        group = _secondary_group()
        if group is None:
            self.skipTest("the user has no secondary group")
        Acme.group = group
        self._clone_with_tmp(["clone", "file://" + self.bare, self.target])
        self.assertEqual([{"export": "open"}], Acme.seen)

        # The clone and the post-clone update both ran...
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "src", "a.txt")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "packages", "ivpm.json")))
        # ...and everything they wrote inherited the prepared group.
        self.assertTrue(os.stat(self.target).st_mode & stat.S_ISGID)
        wrong = []
        for dirpath, dirnames, filenames in os.walk(self.target):
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                if os.lstat(p).st_gid != group:
                    wrong.append(os.path.relpath(p, self.target))
        self.assertEqual([], wrong)
        self._assert_no_scratch()

    def test_failed_clone_removes_prepared_workspace(self):
        group = _secondary_group()
        if group is None:
            self.skipTest("the user has no secondary group")
        Acme.group = group
        missing = os.path.join(self.testdir, "missing.git")
        with self.assertRaises((SrcLoaderError, SystemExit)):
            self._clone_with_tmp(["clone", "file://" + missing, self.target])
        # Nothing to read, so no policy -- the preparer still prepared, and the
        # directory it created is gone again.
        self.assertEqual([{}], Acme.seen)
        self.assertFalse(os.path.exists(self.target))
        self._assert_no_scratch()

    def test_failed_tag_checkout_keeps_populated_workspace(self):
        # The git provider clones, then checks out the tag: by the time a bad
        # -t fails, the workspace holds a clone.  Rollback removes only empty
        # directories, so it stays -- as it does with no preparers installed.
        group = _secondary_group()
        if group is None:
            self.skipTest("the user has no secondary group")
        Acme.group = group
        with self.assertRaises((SrcLoaderError, SystemExit)):
            self._clone_with_tmp(["clone", "-t", "no-such-tag",
                                  "file://" + self.bare, self.target])
        self.assertEqual([{}], Acme.seen)
        self.assertTrue(os.path.isdir(os.path.join(self.target, ".git")))
        self._assert_no_scratch()


if __name__ == "__main__":
    unittest.main()
