#****************************************************************************
#* test_patch_reconcile.py
#*
#* Phase 6 -- editable reconciliation state machine (design sec. 5.12):
#* classify() over the 5 on-disk states and reconcile() over all 12 rows of
#* the editable transition matrix, plus the resolver wiring (DISABLED ->
#* _apply_in_place). Hermetic: a real *local* git repo (the editable tree)
#* and a local origin repo for the Absent (fetch) rows.
#****************************************************************************
import os
import types
import shutil
import tempfile
import subprocess
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

import ivpm.patch as patchmod
from ivpm.patch import (
    PatchSpec, PatchSet, PatchState, classify, reconcile, read_manifest,
    md5_file, PatchAwareResolver,
)
from ivpm.pkg_types.package_git import PackageGit
from ivpm.cache_provider import CacheContext, NullCacheProvider

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch")
HAS_GIT = shutil.which("git") is not None

GENV = dict(os.environ,
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def mkspec(patch_name, strip=1, directory=None):
    p = os.path.join(DATA, patch_name)
    return PatchSpec(name=patch_name, source=patch_name, resolved_path=p,
                     md5=md5_file(p), strip=strip, directory=directory)


def read(path):
    with open(path) as fp:
        return fp.read()


def write(path, text):
    with open(path, "w") as fp:
        fp.write(text)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd,
                          capture_output=True, text=True, env=GENV)


def _init_repo(pkg_dir):
    shutil.copytree(os.path.join(DATA, "srctree"), pkg_dir)
    _git(pkg_dir, "init", "-q")
    _git(pkg_dir, "add", "-A")
    _git(pkg_dir, "commit", "-q", "-m", "base")
    return _git(pkg_dir, "rev-parse", "HEAD").stdout.strip()


def _hash_tree(root):
    """Map every file (incl. .ivpm) to its sha256 -- for byte-identity asserts."""
    import hashlib
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dp, fn)
            with open(full, "rb") as fp:
                out[os.path.relpath(full, root)] = hashlib.sha256(fp.read()).hexdigest()
    return out


@unittest.skipUnless(HAS_GIT, "git not available")
class _GitBase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.deps = os.path.join(self.root, "deps")
        os.makedirs(self.deps)
        self.pkg_dir = os.path.join(self.deps, "somelib")
        self.base = _init_repo(self.pkg_dir)
        self.pkg = PackageGit(name="somelib")
        self.pkg.src_type = "git"
        self.ui = types.SimpleNamespace(args=None, deps_dir=self.deps,
                                        suppress_output=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def reconcile(self, patches):
        ps = PatchSet(tuple(patches))
        st = classify(self.pkg_dir, self.base, ps, self.pkg)
        return reconcile(st, self.ui, self.pkg, self.pkg_dir, self.base, ps)

    def make_patched_clean(self, patches):
        # Bring the tree to PATCHED_CLEAN(patches) via a first apply.
        self.reconcile(patches)
        st = classify(self.pkg_dir, self.base, PatchSet(tuple(patches)), self.pkg)
        self.assertEqual(st.kind, PatchState.PATCHED_CLEAN)

    def kind(self, patches):
        return classify(self.pkg_dir, self.base,
                        PatchSet(tuple(patches)), self.pkg).kind


class TestClassify(_GitBase):

    def test_absent(self):
        shutil.rmtree(self.pkg_dir)
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.ABSENT)

    def test_pristine(self):
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.PRISTINE)

    def test_dirty_pristine(self):
        write(os.path.join(self.pkg_dir, "hello.txt"), "user edit\n")
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.DIRTY_PRISTINE)

    def test_patched_clean(self):
        self.make_patched_clean([mkspec("fix.patch")])
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.PATCHED_CLEAN)

    def test_patched_drift_on_unrelated_edit(self):
        self.make_patched_clean([mkspec("fix.patch")])
        write(os.path.join(self.pkg_dir, "ctx.txt"), "user edit\n")
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.PATCHED_DRIFT)

    def test_patched_drift_on_moved_head(self):
        self.make_patched_clean([mkspec("fix.patch")])
        _git(self.pkg_dir, "commit", "-aqm", "user commit")   # HEAD != base
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.PATCHED_DRIFT)


class TestReconcileRows(_GitBase):

    # Row 2 (Absent -> patched) uses a real local clone.
    def test_row2_absent_to_patched(self):
        shutil.rmtree(self.pkg_dir)
        origin = os.path.join(self.root, "origin")
        _init_repo(origin)
        self.pkg.url = origin
        self.reconcile([mkspec("fix.patch")])
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.PATCHED_CLEAN)

    def test_row1_absent_to_unpatched(self):
        shutil.rmtree(self.pkg_dir)
        origin = os.path.join(self.root, "origin")
        _init_repo(origin)
        self.pkg.url = origin
        self.reconcile([])
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello\n")
        self.assertIsNone(read_manifest(self.pkg_dir))

    def test_row3_pristine_unpatched_noop(self):
        before = _hash_tree(self.pkg_dir)
        self.reconcile([])
        self.assertEqual(_hash_tree(self.pkg_dir), before)
        self.assertIsNone(read_manifest(self.pkg_dir))

    def test_row4_pristine_to_patched(self):
        self.reconcile([mkspec("fix.patch")])
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")
        m = read_manifest(self.pkg_dir)
        self.assertEqual(m["base"], {"kind": "git", "version": self.base})

    def test_row5_patched_to_unpatched_restores(self):
        self.make_patched_clean([mkspec("fix.patch")])
        self.reconcile([])
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello\n")
        self.assertFalse(os.path.exists(os.path.join(self.pkg_dir, ".ivpm")))

    def test_row6_idempotent_noop(self):
        self.make_patched_clean([mkspec("fix.patch")])
        calls = {"n": 0}
        orig = patchmod.apply_patchset
        patchmod.apply_patchset = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), orig(*a, **k))[1]
        try:
            self.reconcile([mkspec("fix.patch")])
        finally:
            patchmod.apply_patchset = orig
        self.assertEqual(calls["n"], 0)   # no re-apply
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")

    def test_row7_patchset_change_reestablishes(self):
        self.make_patched_clean([mkspec("fix.patch")])
        self.reconcile([mkspec("fix.patch"), mkspec("sub.patch", directory="src")])
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")
        self.assertEqual(read(os.path.join(self.pkg_dir, "src", "foo.txt")), "foo patched\n")

    def test_row8_drift_same_set_noop_leaves_work(self):
        self.make_patched_clean([mkspec("fix.patch")])
        write(os.path.join(self.pkg_dir, "ctx.txt"), "dev work\n")
        self.reconcile([mkspec("fix.patch")])      # same set -> leave it
        self.assertEqual(read(os.path.join(self.pkg_dir, "ctx.txt")), "dev work\n")

    def test_row11_dirty_pristine_unpatched_noop(self):
        write(os.path.join(self.pkg_dir, "hello.txt"), "dev work\n")
        self.reconcile([])
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "dev work\n")


@unittest.skipUnless(HAS_GIT, "git not available")
class TestReconcileErrorRows(_GitBase):
    """Rows 9, 10, 12 must error WITHOUT mutating the tree."""

    def _assert_errors_without_mutating(self, setup_patches, desired_patches):
        if setup_patches is not None:
            self.make_patched_clean(setup_patches)
        # introduce drift / dirt
        write(os.path.join(self.pkg_dir, "ctx.txt"), "dev work\n")
        before = _hash_tree(self.pkg_dir)
        with self.assertRaises(Exception) as ctx:
            self.reconcile(desired_patches)
        self.assertIn("refusing to re-establish", str(ctx.exception))
        self.assertEqual(_hash_tree(self.pkg_dir), before)   # byte-identical

    def test_row9_drift_patchset_change_errors(self):
        self._assert_errors_without_mutating(
            [mkspec("fix.patch")],
            [mkspec("fix.patch"), mkspec("sub.patch", directory="src")])

    def test_row10_drift_to_unpatched_errors(self):
        self._assert_errors_without_mutating([mkspec("fix.patch")], [])

    def test_row12_dirty_pristine_to_patched_errors(self):
        # No setup -> dirty pristine (no manifest) + desired patches.
        self._assert_errors_without_mutating(None, [mkspec("fix.patch")])


@unittest.skipUnless(HAS_GIT, "git not available")
class TestChangeDetection(_GitBase):

    def test_changed_patch_file_triggers_reestablish(self):
        # Tree is patched-clean with fix.patch; now the *same logical patch slot*
        # carries different bytes -> different patchset_id -> the recorded set no
        # longer matches -> classify is PATCHED_CLEAN but desired != recorded, so
        # reconcile re-establishes (restore base, then re-apply the new patch).
        self.make_patched_clean([mkspec("fix.patch")])
        changed = PatchSpec(
            name="fix.patch", source="fix.patch",
            resolved_path=os.path.join(DATA, "reject.patch"),
            md5=md5_file(os.path.join(DATA, "reject.patch")), strip=1)
        ps = PatchSet((changed,))
        st = classify(self.pkg_dir, self.base, ps, self.pkg)
        self.assertEqual(st.kind, PatchState.PATCHED_CLEAN)
        # The new bytes (reject.patch) do not apply onto the restored base ->
        # re-establish restores pristine then fails the apply (fail-closed).
        with self.assertRaises(patchmod.PatchError):
            reconcile(st, self.ui, self.pkg, self.pkg_dir, self.base, ps)


@unittest.skipUnless(HAS_GIT, "git not available")
class TestResolverWiring(_GitBase):

    def test_disabled_routes_to_apply_in_place(self):
        # NullCacheProvider -> lookup DISABLED -> _apply_in_place -> reconcile.
        ctx = CacheContext(root_name="r", root_version="1",
                           root_dir=self.root, deps_dir=self.deps)
        provider = NullCacheProvider(ctx)
        ui = types.SimpleNamespace(
            args=None, deps_dir=self.deps, suppress_output=False,
            get_cache_provider=lambda: provider,
            report_cache_unconfigured=lambda: None,
            report_cache_hit=lambda: None, report_cache_miss=lambda: None)
        self.pkg.patches = [mkspec("fix.patch")]
        PatchAwareResolver().resolve(ui, self.pkg, self.base)
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")
        self.assertEqual(self.kind([mkspec("fix.patch")]), PatchState.PATCHED_CLEAN)


if __name__ == "__main__":
    unittest.main()
