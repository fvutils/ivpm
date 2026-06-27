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
from ivpm.patch import PatchSpec, md5_file, read_manifest
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

    def get_cache_provider(self):
        return self._provider

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


if __name__ == "__main__":
    unittest.main()
