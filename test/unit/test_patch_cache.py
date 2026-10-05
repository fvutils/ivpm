#****************************************************************************
#* test_patch_cache.py
#*
#* Phase 4 -- cache-mode resolver (PatchAwareResolver). Hermetic: the source
#* "fetch" copies a local fixture tree (no git, no network), exercised through
#* a real DirectoryCacheProvider over a temp store. Covers fetch-and-patch in
#* cache staging, MISS->build->HIT (no re-apply), divergent-set segregation +
#* dedup, the disabled (editable) path, and partial-apply cleanup.
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

import ivpm.patch as patchmod
from ivpm.patch import (
    PatchSpec, PatchSet, PatchAwareResolver, effective_version, read_manifest,
    md5_file,
)
from ivpm.cache import DirectoryCacheStore
from ivpm.cache_provider import CacheContext, DirectoryCacheProvider

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch")
BASE = "abc123def456"


def mkspec(patch_name, strip=1, directory=None):
    p = os.path.join(DATA, patch_name)
    return PatchSpec(name=patch_name, source=patch_name, resolved_path=p,
                     md5=md5_file(p), strip=strip, directory=directory)


class FakePkg:
    """Minimal patch-capable source: fetch_pristine copies the fixture tree."""
    def __init__(self, name, patches, cache=True, src_type="git"):
        self.name = name
        self.cache = cache
        self.src_type = src_type
        self.patches = list(patches)
        self.fetch_count = 0

    @property
    def patchset(self):
        return PatchSet(tuple(self.patches))

    def fetch_pristine(self, update_info, dest_dir, base_version):
        self.fetch_count += 1
        shutil.copytree(os.path.join(DATA, "srctree"), dest_dir)

    def retain_base(self, pkg_dir, base_version, update_info):
        self._base_ref = {"kind": "test", "version": base_version}


class FakeUpdateInfo:
    def __init__(self, deps_dir, provider):
        self.deps_dir = deps_dir
        self._provider = provider
        self.hits = 0
        self.misses = 0
        self.unconfigured = 0

    def get_cache_provider(self):
        return self._provider


    def report_cache_hit(self):
        self.hits += 1

    def report_cache_miss(self):
        self.misses += 1

    def report_cache_unconfigured(self):
        self.unconfigured += 1


class _ResolverBase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.root, "cache")
        self.deps_dir = os.path.join(self.root, "deps")
        os.makedirs(self.deps_dir)
        store = DirectoryCacheStore(self.cache_dir)
        ctx = CacheContext(root_name="root", root_version="1",
                           root_dir=self.root, deps_dir=self.deps_dir)
        self.provider = DirectoryCacheProvider(ctx, store)
        self.store = store

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def ui(self):
        return FakeUpdateInfo(self.deps_dir, self.provider)

    def resolve(self, pkg, update_info=None):
        return PatchAwareResolver().resolve(update_info or self.ui(), pkg, BASE)

    def link_target(self, name):
        link = os.path.join(self.deps_dir, name)
        return os.path.realpath(link)

    def cache_entry(self, name, version):
        return self.store.get_version_cache_dir(name, version)


class TestCacheMiss(_ResolverBase):

    def test_miss_builds_and_links_patched_variant(self):
        pkg = FakePkg("somelib", [mkspec("fix.patch")])
        ui = self.ui()
        self.resolve(pkg, ui)

        eff = effective_version(BASE, pkg.patchset)
        # deps/<pkg> symlinks the patched variant in the cache.
        self.assertTrue(os.path.islink(os.path.join(self.deps_dir, "somelib")))
        self.assertEqual(self.link_target("somelib"),
                         os.path.realpath(self.cache_entry("somelib", eff)))
        # The variant content is patched, and carries a manifest.
        self.assertEqual(
            open(os.path.join(self.deps_dir, "somelib", "hello.txt")).read(),
            "hello patched\n")
        m = read_manifest(os.path.join(self.deps_dir, "somelib"))
        self.assertEqual(m["patchset_id"], pkg.patchset.patchset_id)
        self.assertEqual(m["base"], {"kind": "fetch", "version": BASE})
        self.assertEqual(ui.misses, 1)
        self.assertEqual(ui.hits, 0)

    def test_patches_in_cache_staging_without_a_base_entry(self):
        """The pristine tree is fetched into cache-side staging and patched
        there: no separate base entry, and no copy of one."""
        pkg = FakePkg("somelib", [mkspec("fix.patch")])
        fetched_into = []
        orig = pkg.fetch_pristine

        def recording(update_info, dest_dir, base_version):
            fetched_into.append(dest_dir)
            return orig(update_info, dest_dir, base_version)
        pkg.fetch_pristine = recording

        from unittest.mock import patch
        with patch("ivpm.fscopy.copy_tree",
                   side_effect=AssertionError("patching copied a tree")):
            self.resolve(pkg)

        self.assertEqual(pkg.fetch_count, 1)
        self.assertTrue(fetched_into[0].startswith(
            self.store.get_package_cache_dir("somelib") + os.sep))
        self.assertFalse(self.store.has_version("somelib", BASE))

    def test_manifest_result_records_modified_path(self):
        pkg = FakePkg("somelib", [mkspec("fix.patch")])
        self.resolve(pkg)
        m = read_manifest(os.path.join(self.deps_dir, "somelib"))
        mods = [r for r in m["result"] if r["path"] == "hello.txt"]
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["op"], "modified")


class TestCacheHit(_ResolverBase):

    def test_rerun_is_hit_with_no_refetch_no_reapply(self):
        spec = [mkspec("fix.patch")]

        # First run: MISS, builds the variant.
        p1 = FakePkg("somelib", spec)
        self.resolve(p1)
        self.assertEqual(p1.fetch_count, 1)

        # Count apply_patchset calls on the second run.
        calls = {"n": 0}
        orig = patchmod.apply_patchset
        def counting(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)
        patchmod.apply_patchset = counting
        try:
            p2 = FakePkg("somelib", spec)
            ui = self.ui()
            self.resolve(p2, ui)
        finally:
            patchmod.apply_patchset = orig

        self.assertEqual(p2.fetch_count, 0)   # no re-fetch
        self.assertEqual(calls["n"], 0)       # no re-apply
        self.assertEqual(ui.hits, 1)
        self.assertEqual(ui.misses, 0)


class TestDivergentSets(_ResolverBase):

    def test_distinct_sets_distinct_entries(self):
        a = FakePkg("somelib", [mkspec("fix.patch")])
        b = FakePkg("somelib", [mkspec("fix.patch"),
                                mkspec("sub.patch", directory="src")])
        self.resolve(a)
        self.resolve(b)

        eff_a = effective_version(BASE, a.patchset)
        eff_b = effective_version(BASE, b.patchset)
        self.assertNotEqual(eff_a, eff_b)
        self.assertTrue(self.store.has_version("somelib", eff_a))
        self.assertTrue(self.store.has_version("somelib", eff_b))
        # Each distinct set is fetched once, straight into its own staging.
        self.assertEqual((a.fetch_count, b.fetch_count), (1, 1))

    def test_identical_sets_share_one_entry(self):
        a = FakePkg("somelib", [mkspec("fix.patch")])
        b = FakePkg("somelib", [mkspec("fix.patch")])
        self.resolve(a)
        ui = self.ui()
        self.resolve(b, ui)
        # Second consumer with the same set is a pure HIT.
        self.assertEqual(b.fetch_count, 0)
        self.assertEqual(ui.hits, 1)


class TestDisabled(_ResolverBase):

    def test_uncacheable_patched_dep_applies_editable(self):
        # cache not True -> provider lookup is DISABLED -> editable in-place
        # patching (the Phase-6 reconciler), not a cache symlink.
        pkg = FakePkg("somelib", [mkspec("fix.patch")], cache=False)
        ui = self.ui()
        self.resolve(pkg, ui)

        pkg_dir = os.path.join(self.deps_dir, "somelib")
        self.assertFalse(os.path.islink(pkg_dir))           # real editable tree
        self.assertEqual(open(os.path.join(pkg_dir, "hello.txt")).read(),
                         "hello patched\n")
        m = read_manifest(pkg_dir)
        self.assertEqual(m["patchset_id"], pkg.patchset.patchset_id)
        self.assertEqual(ui.unconfigured, 1)


class TestPartialApplySafety(_ResolverBase):

    def test_rejected_hunk_leaves_no_variant_or_staging(self):
        pkg = FakePkg("somelib", [mkspec("reject.patch")])
        with self.assertRaises(patchmod.PatchError):
            self.resolve(pkg)

        eff = effective_version(BASE, pkg.patchset)
        # No variant entry, no base entry, no leftover staging anywhere.
        self.assertFalse(self.store.has_version("somelib", eff))
        self.assertFalse(self.store.has_version("somelib", BASE))
        self.assertEqual(os.listdir(self.deps_dir), [])
        pkg_dir = self.store.get_package_cache_dir("somelib")
        self.assertEqual([n for n in os.listdir(pkg_dir)
                          if ".staging." in n], [])


if __name__ == "__main__":
    unittest.main()
