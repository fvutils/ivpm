#****************************************************************************
#* test_patch_http.py
#*
#* Gap 2 -- PackageHttp must route patched deps through the PatchAwareResolver
#* and provide fetch_pristine (tier-1 patch hook). Previously patches on an http
#* dep were silently ignored. Hermetic: the network boundary (_download_file /
#* _get_url_version) is stubbed to a local fixture tarball; a real
#* DirectoryCacheProvider over a temp store exercises the cache-mode path.
#****************************************************************************
import os
import sys
import shutil
import tarfile
import tempfile
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
sys.path.insert(0, SRCDIR)

from ivpm.pkg_types.package_http import PackageHttp
from ivpm.patch import PatchSet, PatchSpec, md5_file, read_manifest
from ivpm.cache import DirectoryCacheStore
from ivpm.cache_provider import CacheContext, DirectoryCacheProvider

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch")


def mkspec(name, strip=1, directory=None):
    p = os.path.join(DATA, name)
    return PatchSpec(name=name, source=name, resolved_path=p,
                     md5=md5_file(p), strip=strip, directory=directory)


class FakeUpdateInfo:
    def __init__(self, deps_dir, provider):
        self.deps_dir = deps_dir
        self._provider = provider
        self.deps_source = None
        self.hits = self.misses = self.unconfigured = 0
        self.cacheable = self.editable = 0
        self._load_planner = None

    def get_cache_provider(self):
        return self._provider

    def get_load_planner(self):
        # Providers ask the planner whether a package needs loading. No lock
        # data here: these fixtures decide purely on what is on disk.
        if self._load_planner is None:
            from ivpm.load_plan import LoadPlanner
            self._load_planner = LoadPlanner(self.deps_dir, None)
        return self._load_planner

    def report_package(self, cacheable=False, editable=False):
        self.cacheable += int(cacheable)
        self.editable += int(editable)

    def report_cache_hit(self):
        self.hits += 1

    def report_cache_miss(self):
        self.misses += 1

    def report_cache_unconfigured(self):
        self.unconfigured += 1


class _HttpPatchBase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.deps_dir = os.path.join(self.root, "deps")
        os.makedirs(self.deps_dir)
        store = DirectoryCacheStore(os.path.join(self.root, "cache"))
        ctx = CacheContext(root_name="root", root_version="1",
                           root_dir=self.root, deps_dir=self.deps_dir)
        self.provider = DirectoryCacheProvider(ctx, store)
        # A real .tar.gz of the fixture srctree (so _install/unpack runs for real).
        # _install_tgz strips a top-level wrapper directory (real-world archives
        # carry one, e.g. <pkg>-<ver>/...), so wrap the fixture under "somelib/".
        self.tarball = os.path.join(self.root, "somelib.tar.gz")
        with tarfile.open(self.tarball, "w:gz") as tf:
            tf.add(os.path.join(DATA, "srctree"), arcname="somelib")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def ui(self):
        return FakeUpdateInfo(self.deps_dir, self.provider)

    def _pkg(self, patches, cache=True):
        pkg = PackageHttp("somelib")
        pkg.url = "http://example.invalid/somelib.tar.gz"
        pkg.src_type = ".tar.gz"
        pkg.unpack = True
        pkg.cache = cache
        pkg.patches = list(patches)
        # Stub the network boundary -- deterministic base + local archive bytes.
        pkg._get_url_version = lambda url: "v1"
        pkg._download_file = lambda url, dest: shutil.copy(self.tarball, dest)
        return pkg

    def _read(self, *rel):
        with open(os.path.join(self.deps_dir, "somelib", *rel)) as fp:
            return fp.read()


class TestHttpPatchedUpdate(_HttpPatchBase):

    def test_patched_http_builds_variant(self):
        ui = self.ui()
        pkg = self._pkg([mkspec("fix.patch")])
        pkg.update(ui)

        pdir = os.path.join(self.deps_dir, "somelib")
        self.assertTrue(os.path.exists(pdir))
        # The patch actually applied (previously silently ignored).
        self.assertEqual(self._read("hello.txt"), "hello patched\n")
        m = read_manifest(pdir)
        self.assertIsNotNone(m)
        self.assertEqual(m["patchset_id"], pkg.patchset.patchset_id)
        self.assertEqual(ui.misses, 1)

    def test_patched_http_second_update_is_hit(self):
        # First build, then a fresh pkg/dir reuses the cached variant (no re-apply).
        self._pkg([mkspec("fix.patch")]).update(self.ui())
        shutil.rmtree(os.path.join(self.deps_dir, "somelib"), ignore_errors=True)
        if os.path.islink(os.path.join(self.deps_dir, "somelib")):
            os.unlink(os.path.join(self.deps_dir, "somelib"))

        ui2 = self.ui()
        self._pkg([mkspec("fix.patch")]).update(ui2)
        self.assertEqual(self._read("hello.txt"), "hello patched\n")
        self.assertEqual(ui2.hits, 1)
        self.assertEqual(ui2.misses, 0)

    def test_unpatched_http_unaffected(self):
        # No patches -> legacy cache path, no resolver, no manifest.
        ui = self.ui()
        self._pkg([]).update(ui)
        pdir = os.path.join(self.deps_dir, "somelib")
        self.assertTrue(os.path.exists(pdir))
        self.assertEqual(self._read("hello.txt"), "hello\n")
        self.assertIsNone(read_manifest(pdir))

    def test_fetch_pristine_writable_unpatched_tree(self):
        pkg = self._pkg([mkspec("fix.patch")])
        dest = os.path.join(self.root, "pristine")
        pkg.fetch_pristine(self.ui(), dest, "v1")
        # Pristine (no patch applied) and writable (resolver owns RO locking).
        hello = os.path.join(dest, "hello.txt")
        self.assertEqual(open(hello).read(), "hello\n")
        self.assertTrue(os.access(hello, os.W_OK))
        # Archive retained for tier-2 retain_base().
        self.assertTrue(os.path.isfile(pkg._pristine_archive))


class TestHttpEditableBase(_HttpPatchBase):
    """An editable (uncached) archive dep keeps a base to check its tree
    against, even when it is first patched after it was fetched; and a patched
    tree that an older ivpm left with no base is checked by recovering one,
    rather than refused as drift."""

    def setUp(self):
        super().setUp()
        self.pdir = os.path.join(self.deps_dir, "somelib")

    def _update(self, patches):
        pkg = self._pkg(patches, cache=None)
        pkg.update(self.ui())
        return pkg

    def _reconcile(self, patches):
        from ivpm.patch import PatchSet, classify, reconcile
        pkg = self._pkg(patches, cache=None)
        ps = PatchSet(tuple(patches))
        st = classify(self.pdir, "v1", ps, pkg, self.ui())
        reconcile(st, self.ui(), pkg, self.pdir, "v1", ps)
        return st

    def _bases(self):
        d = os.path.join(self.pdir, ".ivpm")
        return sorted(f for f in os.listdir(d) if f.startswith("base.")) \
            if os.path.isdir(d) else []

    def _legacy_patched(self, patches):
        # A patched tree as an older ivpm left it: manifest, no retained base.
        self._update([])
        self._reconcile(patches)
        for f in self._bases():
            os.unlink(os.path.join(self.pdir, ".ivpm", f))

    def test_first_patched_after_fetch_retains_base(self):
        self._update([])
        self.assertIsNone(read_manifest(self.pdir))
        self._reconcile([mkspec("fix.patch")])
        self.assertEqual(self._bases(), ["base.tar.gz"])
        self.assertEqual(self._read("hello.txt"), "hello patched\n")

    def test_patchset_change_after_first_patch_reestablishes(self):
        self._update([])
        self._reconcile([mkspec("fix.patch")])
        two = [mkspec("fix.patch"), mkspec("sub.patch", directory="src")]
        self._reconcile(two)
        self.assertEqual(read_manifest(self.pdir)["patchset_id"],
                         PatchSet(tuple(two)).patchset_id)

    def test_legacy_tree_recovers_base_and_reestablishes(self):
        from ivpm.patch import PatchState
        self._legacy_patched([mkspec("fix.patch")])
        self.assertEqual(self._bases(), [])
        two = [mkspec("fix.patch"), mkspec("sub.patch", directory="src")]
        st = self._reconcile(two)
        self.assertEqual(st.kind, PatchState.PATCHED_CLEAN)
        self.assertEqual(self._bases(), ["base.tar.gz"])
        self.assertEqual(read_manifest(self.pdir)["patchset_id"],
                         PatchSet(tuple(two)).patchset_id)
        self.assertEqual(self._read("hello.txt"), "hello patched\n")

    def test_legacy_tree_with_edits_still_refused(self):
        self._legacy_patched([mkspec("fix.patch")])
        with open(os.path.join(self.pdir, "ctx.txt"), "w") as fp:
            fp.write("dev work\n")
        with self.assertRaises(Exception) as ctx:
            self._reconcile([mkspec("fix.patch"), mkspec("sub.patch", directory="src")])
        self.assertIn("local modifications beyond its applied patches", str(ctx.exception))
        self.assertEqual(self._read("ctx.txt"), "dev work\n")
        self.assertEqual(self._bases(), [])      # an unexplaining base is not kept

    def test_unrecoverable_base_says_so(self):
        self._legacy_patched([mkspec("fix.patch")])
        pkg_cls = PackageHttp
        orig = pkg_cls.recover_base
        pkg_cls.recover_base = lambda self, *a: False
        try:
            with self.assertRaises(Exception) as ctx:
                self._reconcile([mkspec("fix.patch"), mkspec("sub.patch", directory="src")])
        finally:
            pkg_cls.recover_base = orig
        self.assertIn("cannot be checked for local modifications", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
