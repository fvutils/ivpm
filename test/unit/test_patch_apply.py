#****************************************************************************
#* test_patch_apply.py
#*
#* Unit tests for the patch apply engine (patch.py, Phase 2): apply_patchset,
#* the git-apply / GNU-patch engines, strip/directory handling, fail-closed
#* behavior, the idempotency net, and path-traversal rejection.
#*
#* Fixtures live under test/unit/data/patch/ (a pristine source tree + a set
#* of patch files in git-diff and context-diff formats). Each test applies to
#* a fresh copy of the tree.
#****************************************************************************
import os
import types
import shutil
import tempfile
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.patch import (
    PatchSpec, PatchSet, PatchError,
    md5_file, apply_patchset, _git_apply, _patch_tool,
)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch")
HAS_PATCH = shutil.which("patch") is not None

PKG = types.SimpleNamespace(name="somelib")


def mkspec(patch_name, strip=1, directory=None, tool=None, name=None):
    p = os.path.join(DATA, patch_name)
    return PatchSpec(
        name=name or patch_name, source=patch_name, resolved_path=p,
        md5=md5_file(p), strip=strip, directory=directory, tool=tool)


def read(path):
    with open(path) as fp:
        return fp.read()


class _TreeTest(unittest.TestCase):

    def setUp(self):
        self._tmpdirs = []

    def tearDown(self):
        for d in self._tmpdirs:
            shutil.rmtree(d, ignore_errors=True)

    def fresh_tree(self):
        d = tempfile.mkdtemp()
        self._tmpdirs.append(d)
        tree = os.path.join(d, "tree")
        shutil.copytree(os.path.join(DATA, "srctree"), tree)
        return tree


class TestApplyPatchset(_TreeTest):

    def test_git_apply_unified(self):
        tree = self.fresh_tree()
        apply_patchset(tree, PatchSet((mkspec("fix.patch"),)), "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "hello.txt")), "hello patched\n")

    def test_strip_and_directory(self):
        tree = self.fresh_tree()
        spec = mkspec("sub.patch", strip=1, directory="src")
        apply_patchset(tree, PatchSet((spec,)), "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "src", "foo.txt")), "foo patched\n")
        # Untouched sibling stays pristine.
        self.assertEqual(read(os.path.join(tree, "hello.txt")), "hello\n")

    def test_ordered_multi_spec(self):
        tree = self.fresh_tree()
        ps = PatchSet((mkspec("fix.patch"), mkspec("sub.patch", directory="src")))
        apply_patchset(tree, ps, "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "hello.txt")), "hello patched\n")
        self.assertEqual(read(os.path.join(tree, "src", "foo.txt")), "foo patched\n")

    @unittest.skipUnless(HAS_PATCH, "GNU patch not available")
    def test_gnu_patch_fallback_context_diff(self):
        # git apply cannot parse a context diff; apply_patchset must fall back.
        tree = self.fresh_tree()
        apply_patchset(tree, PatchSet((mkspec("context.patch", strip=0),)), "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "ctx.txt")), "hello ctx\n")

    def test_rejected_hunk_raises_and_leaves_tree_intact(self):
        tree = self.fresh_tree()
        before = read(os.path.join(tree, "hello.txt"))
        with self.assertRaises(PatchError):
            apply_patchset(tree, PatchSet((mkspec("reject.patch"),)), "v1", PKG)
        # No partial mutation: --check/--dry-run gate prevents touching the file.
        self.assertEqual(read(os.path.join(tree, "hello.txt")), before)

    def test_idempotent_reapply_does_not_reverse(self):
        tree = self.fresh_tree()
        ps = PatchSet((mkspec("fix.patch"),))
        apply_patchset(tree, ps, "v1", PKG)
        # Re-applying the same patch is a no-op success, not a reverse-apply.
        apply_patchset(tree, ps, "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "hello.txt")), "hello patched\n")


class TestToolOverride(_TreeTest):

    def test_force_git(self):
        tree = self.fresh_tree()
        apply_patchset(tree, PatchSet((mkspec("fix.patch", tool="git"),)), "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "hello.txt")), "hello patched\n")

    @unittest.skipUnless(HAS_PATCH, "GNU patch not available")
    def test_force_patch(self):
        tree = self.fresh_tree()
        spec = mkspec("context.patch", strip=0, tool="patch")
        apply_patchset(tree, PatchSet((spec,)), "v1", PKG)
        self.assertEqual(read(os.path.join(tree, "ctx.txt")), "hello ctx\n")

    def test_force_git_on_context_diff_fails(self):
        # tool=git disables the patch fallback, so a context diff is a hard error.
        tree = self.fresh_tree()
        spec = mkspec("context.patch", strip=0, tool="git")
        with self.assertRaises(PatchError):
            apply_patchset(tree, PatchSet((spec,)), "v1", PKG)


class TestPathTraversal(_TreeTest):

    def test_apply_patchset_rejects_traversal(self):
        tree = self.fresh_tree()
        with self.assertRaises(PatchError):
            apply_patchset(tree, PatchSet((mkspec("traversal.patch"),)), "v1", PKG)
        # The guard fires before any engine runs -> nothing escaped the tree.
        self.assertFalse(os.path.exists("/tmp/ivpm_evil.txt"))

    def test_git_engine_natively_rejects_traversal(self):
        # Independent of our guard: git apply itself refuses escaping paths
        # (we never pass --unsafe-paths).
        tree = self.fresh_tree()
        self.assertFalse(_git_apply(tree, mkspec("traversal.patch")))


class TestEnginesDirect(_TreeTest):

    def test_git_apply_returns_false_on_context_diff(self):
        # The signal that drives the fallback: git cannot parse a context diff.
        tree = self.fresh_tree()
        self.assertFalse(_git_apply(tree, mkspec("context.patch", strip=0)))

    @unittest.skipUnless(HAS_PATCH, "GNU patch not available")
    def test_patch_tool_applies_context_diff(self):
        tree = self.fresh_tree()
        self.assertTrue(_patch_tool(tree, mkspec("context.patch", strip=0)))
        self.assertEqual(read(os.path.join(tree, "ctx.txt")), "hello ctx\n")

    @unittest.skipUnless(HAS_PATCH, "GNU patch not available")
    def test_patch_tool_idempotent_reverse_safe(self):
        # Applying an already-applied patch reports success without reversing.
        tree = self.fresh_tree()
        spec = mkspec("context.patch", strip=0)
        self.assertTrue(_patch_tool(tree, spec))
        self.assertTrue(_patch_tool(tree, spec))   # already applied -> True, no reverse
        self.assertEqual(read(os.path.join(tree, "ctx.txt")), "hello ctx\n")


if __name__ == "__main__":
    unittest.main()
