#****************************************************************************
#* test_update_spec_change.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software
#* distributed under the License is distributed on an "AS IS" BASIS,
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#* See the License for the specific language governing permissions and
#* limitations under the License.
#*
#****************************************************************************
"""Changing a dependency's spec and re-running ``ivpm update``.

The reported bug: bump ``commit:`` in ivpm.yaml, run ``ivpm update``, and the
clone stays on the old commit. Nothing covered the end-to-end path -- the
drifted *decision* was unit-tested, but no test ever ran a second update
against a resident package and looked at what was on disk afterwards.

The contract these pin down, which follows the patched-tree rows in
test_patch_reconcile.py: a changed spec is ACTED ON when the tree can be
safely replaced, and REFUSED (loudly, tree untouched) when it holds work that
would be destroyed. ``--force`` converts the refusal into a discard.

Offline: everything runs against a local ``file://`` repo.
"""
import json
import os
import subprocess

from .test_base import TestBase

from ivpm.load_plan import LoadAction, LoadPlanner, LoadState
from ivpm.project_ops import ProjectOps


# Pinned dates so a commit hash is a pure function of tree+message+parent;
# two fixtures built either side of a second boundary would otherwise diverge
# under CI load. See test_patch_reconcile.py for the flake this prevents.
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


class _SpecChangeBase(TestBase):
    """A two-commit origin repo and a manifest that pins one of them."""

    def setUp(self):
        super().setUp()
        self.origin = os.path.join(self.testdir, "origin")
        os.makedirs(self.origin)
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "config", "commit.gpgsign", "false")

        self.c1 = self._commit("v1", "first")
        _git(self.origin, "tag", "v1.0")
        self.c2 = self._commit("v2", "second")
        _git(self.origin, "tag", "v2.0")
        self.assertNotEqual(self.c1, self.c2)

    def _commit(self, content, msg):
        with open(os.path.join(self.origin, "f.txt"), "w") as fp:
            fp.write(content + "\n")
        _git(self.origin, "add", "f.txt")
        _git(self.origin, "commit", "-q", "-m", msg)
        return _git(self.origin, "rev-parse", "HEAD").stdout.strip()

    # -- workspace helpers ------------------------------------------------ #

    def deps_dir(self):
        return os.path.join(self.testdir, "packages")

    def pkg_dir(self, name="dep1"):
        return os.path.join(self.deps_dir(), name)

    def head(self, name="dep1"):
        return _git(self.pkg_dir(name), "rev-parse", "HEAD").stdout.strip()

    def lock(self, name="dep1"):
        with open(os.path.join(self.deps_dir(), "package-lock.json")) as fp:
            return json.load(fp)["packages"][name]

    def manifest(self, **pin):
        """Write ivpm.yaml pinning dep1 by exactly one of commit=/tag=/branch=."""
        (key, value), = pin.items()
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: spec_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: dep1\n"
                    "                  url: file://%s\n"
                    "                  src: git\n"
                    "                  %s: %s\n" % (self.origin, key, value))

    def dirty(self, text="LOCAL WORK\n"):
        """An uncommitted edit -- work a refresh must refuse to discard."""
        with open(os.path.join(self.pkg_dir(), "f.txt"), "a") as fp:
            fp.write(text)


class TestCommitChangeIsApplied(_SpecChangeBase):
    """The reported bug, and the lock record it corrupted."""

    def test_changed_commit_moves_the_clone(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), self.c1)

        self.manifest(commit=self.c2)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), self.c2,
                         "the clone stayed on the old commit")

    def test_changed_tag_moves_the_clone(self):
        self.manifest(tag="v1.0")
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), self.c1)

        self.manifest(tag="v2.0")
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), self.c2)

    def test_lock_records_what_actually_happened(self):
        """The lock used to record commit_requested=<new> alongside
        commit_resolved=<old> -- a checkout that never happened, which also
        silenced the drift report on every subsequent run."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        self.manifest(commit=self.c2)
        self.ivpm_update(skip_venv=True)

        entry = self.lock()
        self.assertEqual(entry["commit_requested"], self.c2)
        self.assertEqual(entry["commit_resolved"], self.c2,
                         "lock claims a commit that was never checked out")

    def test_settles_after_one_update(self):
        """A third run must be a no-op: the spec, the lock and the tree agree."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.manifest(commit=self.c2)
        self.ivpm_update(skip_venv=True)

        marker = os.path.join(self.pkg_dir(), "MARKER")
        with open(marker, "w") as fp:
            fp.write("x")
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(marker),
                        "a settled package was re-fetched again")
        self.assertEqual(self.head(), self.c2)

    def test_unchanged_spec_is_not_refetched(self):
        """The other half of the contract: no spec change, no re-fetch."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        marker = os.path.join(self.pkg_dir(), "MARKER")
        with open(marker, "w") as fp:
            fp.write("x")
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(marker), "a resident dep was re-fetched")


class TestRefreshSafetyGate(_SpecChangeBase):
    """A changed spec never silently destroys local work."""

    def test_dirty_tree_refuses_and_keeps_the_work(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.dirty()

        self.manifest(commit=self.c2)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)

        msg = str(ctx.exception)
        self.assertIn("dep1", msg)
        self.assertIn("--force", msg, "the refusal must name the way out")

        self.assertEqual(self.head(), self.c1, "the tree was moved anyway")
        with open(os.path.join(self.pkg_dir(), "f.txt")) as fp:
            self.assertIn("LOCAL WORK", fp.read(), "local work was discarded")

    def test_unpushed_commit_refuses(self):
        """Committed-but-unpushed work is just as unrecoverable as an edit."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.dirty()
        _git(self.pkg_dir(), "add", "f.txt")
        _git(self.pkg_dir(), "commit", "-q", "-m", "local work")
        local_head = self.head()

        self.manifest(commit=self.c2)
        with self.assertRaises(Exception):
            self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), local_head)

    def test_force_discards_and_moves(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.dirty()

        self.manifest(commit=self.c2)
        self.ivpm_update(skip_venv=True, force=True)

        self.assertEqual(self.head(), self.c2)
        with open(os.path.join(self.pkg_dir(), "f.txt")) as fp:
            self.assertNotIn("LOCAL WORK", fp.read())

    def test_clean_tree_needs_no_force(self):
        """The common case must not require a flag: nothing to lose, so it
        just moves."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.manifest(commit=self.c2)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), self.c2)


class TestRefreshAll(_SpecChangeBase):
    """``--refresh-all`` was accepted, plumbed through ProjectOps.update, and
    then never read -- the parameter existed only in the signature."""

    def test_refresh_all_refetches_an_unchanged_package(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        # The probe lives inside .git/ so that `git status` never sees it: a
        # marker in the worktree would be untracked *local work*, and the gate
        # would correctly refuse to discard it, testing the wrong thing. An
        # inode probe is no good either -- the filesystem reuses the number
        # when the directory is recreated at the same path.
        probe = os.path.join(self.pkg_dir(), ".git", "REFRESH_PROBE")
        with open(probe, "w") as fp:
            fp.write("x")

        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(probe),
                        "a plain update re-fetched a resident package")

        self.ivpm_update(skip_venv=True, refresh_all=True)
        self.assertFalse(os.path.isfile(probe),
                         "--refresh-all did not re-fetch the package")
        self.assertEqual(self.head(), self.c1)

    def test_refresh_all_still_respects_the_gate(self):
        """It re-fetches everything; it does not become --force."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.dirty()

        with self.assertRaises(Exception):
            self.ivpm_update(skip_venv=True, refresh_all=True)
        with open(os.path.join(self.pkg_dir(), "f.txt")) as fp:
            self.assertIn("LOCAL WORK", fp.read())

    def test_force_implies_refresh_all(self):
        """--force exists to suppress refresh errors, which is meaningless
        without a refresh; the CLI help says it implies --refresh-all."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        probe = os.path.join(self.pkg_dir(), ".git", "REFRESH_PROBE")
        with open(probe, "w") as fp:
            fp.write("x")

        self.ivpm_update(skip_venv=True, force=True)
        self.assertFalse(os.path.isfile(probe))


class TestCommitPinIsDetached(_SpecChangeBase):
    """A commit pin produces a detached HEAD, not a branch parked on the pin.

    `git reset --hard` moves the *branch* pointer, which leaves the clone on an
    ordinary branch merely sitting behind its upstream. Everything downstream
    read that as "this dependency tracks main": `ivpm status` showed the branch
    name, and `ivpm sync` fast-forwarded it off the pin. The destroy gate had
    already been written against the detached behaviour -- its comment says a
    pin "leaves a detached HEAD" -- so the clone step was the odd one out.
    """

    def test_pinned_checkout_is_detached(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        branch = _git(self.pkg_dir(), "rev-parse", "--abbrev-ref",
                      "HEAD").stdout.strip()
        self.assertEqual(branch, "HEAD", "a commit pin left the clone on a branch")
        self.assertEqual(self.head(), self.c1)

    def test_unpinned_checkout_stays_on_its_branch(self):
        """Only a pin detaches; an ordinary dep is still a normal checkout."""
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: spec_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: dep1\n"
                    "                  url: file://%s\n"
                    "                  src: git\n" % self.origin)
        self.ivpm_update(skip_venv=True)
        branch = _git(self.pkg_dir(), "rev-parse", "--abbrev-ref",
                      "HEAD").stdout.strip()
        self.assertNotEqual(branch, "HEAD")

    def test_status_reports_the_pin(self):
        from ivpm.status_tui import _branch_label

        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        _, results = ProjectOps(self.testdir).status(args=None)
        st = [r for r in results if r.name == "dep1"]
        self.assertEqual(len(st), 1)
        st = st[0]
        self.assertEqual(st.pinned_commit, self.c1)
        # Without the pin this would render the bare short hash as an
        # anonymous detached HEAD, indistinguishable from a manual checkout.
        self.assertEqual(_branch_label(st), "pinned:%s" % self.c1[:7])

    def test_status_of_an_unpinned_package_is_unchanged(self):
        from ivpm.status_tui import _branch_label

        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: spec_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: dep1\n"
                    "                  url: file://%s\n"
                    "                  src: git\n" % self.origin)
        self.ivpm_update(skip_venv=True)

        _, results = ProjectOps(self.testdir).status(args=None)
        st = [r for r in results if r.name == "dep1"][0]
        self.assertIsNone(st.pinned_commit)
        self.assertNotIn("pinned:", _branch_label(st))

    def test_commit_on_a_detached_pin_blocks_a_refresh(self):
        """Committing while detached is the case @{u} cannot see: those commits
        are reachable from nothing but HEAD and would be lost on a re-clone."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.dirty()
        _git(self.pkg_dir(), "add", "f.txt")
        _git(self.pkg_dir(), "commit", "-q", "-m", "work on a detached head")
        local_head = self.head()

        self.manifest(commit=self.c2)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)
        self.assertIn("unpushed", str(ctx.exception))
        self.assertEqual(self.head(), local_head, "local commit was discarded")

    def test_clean_detached_pin_refreshes_freely(self):
        """The pin itself must not read as unrecoverable work -- that commit is
        on the remote, so there is nothing to lose."""
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)
        self.manifest(commit=self.c2)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.head(), self.c2)


class TestLinkedPackageDrift(TestBase):
    """A cache hit / deps-source hit materializes as a symlink.

    Residency for those used to be answered and *returned* before the lock was
    ever consulted, so a linked dependency never drift-checked at all: a
    `cache: true` git dep stayed pinned to whatever it first resolved to, no
    matter what the manifest said afterwards. Comparing a spec to a lock entry
    is a dict comparison -- it does not traverse the shared target, which is
    the property that early return was protecting.
    """

    def deps_dir(self):
        return os.path.join(self.testdir, "packages")

    def pkg_dir(self):
        return os.path.join(self.deps_dir(), "leaf")

    def _mkleaf(self, name, content):
        path = os.path.join(self.testdir, name)
        os.makedirs(path)
        with open(os.path.join(path, "marker.txt"), "w") as fp:
            fp.write(content)
        with open(os.path.join(path, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n  name: leaf\n  dep-sets:\n"
                     "    - name: default-dev\n      deps: []\n")
        return path

    def _manifest(self, target):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: link_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: leaf\n"
                    "                  url: file://%s\n"
                    "                  src: dir\n"
                    "                  link: true\n" % target)

    def _content(self):
        with open(os.path.join(self.pkg_dir(), "marker.txt")) as fp:
            return fp.read().strip()

    def test_linked_package_follows_a_changed_spec(self):
        first = self._mkleaf("leafA", "A")
        second = self._mkleaf("leafB", "B")

        self._manifest(first)
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.islink(self.pkg_dir()), "expected a symlink")
        self.assertEqual(self._content(), "A")

        self._manifest(second)
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.islink(self.pkg_dir()))
        self.assertEqual(self._content(), "B",
                         "a linked package ignored its changed spec")

    def test_linked_package_is_reused_when_unchanged(self):
        first = self._mkleaf("leafA", "A")
        self._manifest(first)
        self.ivpm_update(skip_venv=True)
        before = os.readlink(self.pkg_dir())

        self.ivpm_update(skip_venv=True)
        self.assertEqual(os.readlink(self.pkg_dir()), before)


class TestDriftDecision(_SpecChangeBase):
    """The planner-level decision behind the behaviour above."""

    def _pkg_for(self, commit):
        from ivpm.pkg_types.package_git import PackageGit
        pkg = PackageGit("dep1")
        # src_type selects the comparison branch in _spec_matches_lock; a
        # hand-built Package has none until the yaml reader sets it.
        pkg.src_type = "git"
        pkg.url = "file://%s" % self.origin
        pkg.commit = commit
        pkg.scope_key = "dep1"
        pkg.path = self.pkg_dir()
        return pkg

    def test_drift_decides_refresh(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.deps_dir(), "package-lock.json")) as fp:
            lock = json.load(fp)

        d = LoadPlanner(self.deps_dir(), lock).decide(self._pkg_for(self.c2))
        self.assertIs(d.state, LoadState.RESIDENT_DRIFTED)
        self.assertIs(d.action, LoadAction.REFRESH)
        self.assertTrue(d.should_fetch)
        self.assertFalse(d.is_resident)
        self.assertEqual(d.drift["locked"]["commit_requested"], self.c1)

    def test_matching_spec_decides_reuse(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.deps_dir(), "package-lock.json")) as fp:
            lock = json.load(fp)

        d = LoadPlanner(self.deps_dir(), lock).decide(self._pkg_for(self.c1))
        self.assertIs(d.state, LoadState.RESIDENT_MATCHING)
        self.assertIs(d.action, LoadAction.REUSE)

    def test_refresh_all_overrides_a_matching_spec(self):
        self.manifest(commit=self.c1)
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.deps_dir(), "package-lock.json")) as fp:
            lock = json.load(fp)

        d = LoadPlanner(self.deps_dir(), lock,
                        refresh_all=True).decide(self._pkg_for(self.c1))
        self.assertIs(d.state, LoadState.RESIDENT_MATCHING)
        self.assertIs(d.action, LoadAction.REFRESH)
        self.assertIn("refresh-all", d.reason)
