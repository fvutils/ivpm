#****************************************************************************
#* test_cache_provider.py
#*
#* Unit tests for the cache provider seam (cache_provider.py), the
#* SiteConfig.get_cache_provider factory, and the ProjectUpdateInfo
#* session helper.
#****************************************************************************
import os
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.cache import DirectoryCacheStore
from ivpm.cache_provider import (
    CacheContext, CacheState, CacheLookupResult, CacheProvider,
    NullCacheProvider, DirectoryCacheProvider,
)
from ivpm.project_ops_info import ProjectUpdateInfo
from ivpm.site_config import SiteConfig, get_site_config, reset_site_config


def mkpkg(name, cache=None):
    """A minimal stand-in for a Package: just .name and .cache."""
    return types.SimpleNamespace(name=name, cache=cache)


def mkctx(deps_dir):
    return CacheContext(
        root_name="root", root_version="1.0",
        root_dir="/root", deps_dir=deps_dir)


class TestCacheLookupResult(unittest.TestCase):

    def test_hit(self):
        r = CacheLookupResult(CacheState.HIT, "/some/path")
        self.assertTrue(r.is_hit)
        self.assertFalse(r.is_miss)
        self.assertFalse(r.is_disabled)
        self.assertEqual(r.cached_path, "/some/path")

    def test_miss(self):
        r = CacheLookupResult(CacheState.MISS)
        self.assertTrue(r.is_miss)
        self.assertFalse(r.is_hit)
        self.assertIsNone(r.cached_path)

    def test_disabled(self):
        r = CacheLookupResult(CacheState.DISABLED)
        self.assertTrue(r.is_disabled)
        self.assertFalse(r.is_hit)
        self.assertIsNone(r.cached_path)


class TestNullCacheProvider(unittest.TestCase):

    def setUp(self):
        self.p = NullCacheProvider(mkctx("/tmp/deps"))

    def test_never_cacheable(self):
        self.assertFalse(self.p.is_cacheable(mkpkg("a", cache=True)))
        self.assertFalse(self.p.is_cacheable(mkpkg("a", cache=False)))

    def test_lookup_always_disabled(self):
        r = self.p.lookup(mkpkg("a", cache=True), "v1")
        self.assertTrue(r.is_disabled)
        self.assertIsNone(r.cached_path)

    def test_store_is_noop_returning_input(self):
        self.assertEqual(self.p.store(mkpkg("a"), "v1", "/src/path"), "/src/path")

    def test_materialize_raises(self):
        with self.assertRaises(RuntimeError):
            self.p.materialize(mkpkg("a"), "v1")


class TestDirectoryCacheProvider(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.tmp, "cache")
        self.deps_dir = os.path.join(self.tmp, "deps")
        os.makedirs(self.cache_dir)
        os.makedirs(self.deps_dir)
        self.store = DirectoryCacheStore(self.cache_dir)
        self.p = DirectoryCacheProvider(mkctx(self.deps_dir), self.store)

    def tearDown(self):
        # cache entries are read-only; restore write bits before cleanup
        for root, dirs, files in os.walk(self.tmp):
            for d in dirs:
                try: os.chmod(os.path.join(root, d), 0o755)
                except OSError: pass
            for f in files:
                try: os.chmod(os.path.join(root, f), 0o644)
                except OSError: pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mksrc(self, name, content="hello"):
        src = os.path.join(self.tmp, "src_" + name)
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "file.txt"), "w") as f:
            f.write(content)
        return src

    def test_is_cacheable_only_when_flag_true(self):
        self.assertTrue(self.p.is_cacheable(mkpkg("a", cache=True)))
        self.assertFalse(self.p.is_cacheable(mkpkg("a", cache=False)))
        self.assertFalse(self.p.is_cacheable(mkpkg("a", cache=None)))
        # missing attribute -> not cacheable
        self.assertFalse(self.p.is_cacheable(types.SimpleNamespace(name="a")))

    def test_lookup_disabled_when_not_cacheable(self):
        # Even though a store dir is configured, an opted-out dep is DISABLED.
        r = self.p.lookup(mkpkg("a", cache=False), "v1")
        self.assertTrue(r.is_disabled)

    def test_miss_store_hit_materialize_lifecycle(self):
        pkg = mkpkg("mypkg", cache=True)

        # initially a miss
        self.assertTrue(self.p.lookup(pkg, "v1").is_miss)

        # store populates the cache
        src = self._mksrc("mypkg")
        stored = self.p.store(pkg, "v1", src)
        self.assertTrue(os.path.isdir(stored))
        self.assertEqual(stored, self.store.get_version_cache_dir("mypkg", "v1"))

        # now a hit, with the cached path
        r = self.p.lookup(pkg, "v1")
        self.assertTrue(r.is_hit)
        self.assertEqual(r.cached_path, stored)

        # materialize creates a symlink in deps_dir pointing at the entry
        link = self.p.materialize(pkg, "v1")
        self.assertEqual(link, os.path.join(self.deps_dir, "mypkg"))
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), os.path.realpath(stored))

    def test_materialize_uses_context_deps_dir(self):
        pkg = mkpkg("p", cache=True)
        self.p.store(pkg, "v1", self._mksrc("p"))
        link = self.p.materialize(pkg, "v1")
        self.assertTrue(link.startswith(self.deps_dir))

    def test_shared_provider_multiple_packages_and_versions(self):
        """One session provider serves distinct packages and versions."""
        a = mkpkg("aaa", cache=True)
        b = mkpkg("bbb", cache=True)
        self.p.store(a, "v1", self._mksrc("aaa_v1", "a1"))
        self.p.store(a, "v2", self._mksrc("aaa_v2", "a2"))
        self.p.store(b, "v1", self._mksrc("bbb_v1", "b1"))

        self.assertTrue(self.p.lookup(a, "v1").is_hit)
        self.assertTrue(self.p.lookup(a, "v2").is_hit)
        self.assertTrue(self.p.lookup(b, "v1").is_hit)
        self.assertTrue(self.p.lookup(b, "v2").is_miss)  # never stored

        # entries are independent on disk
        self.assertNotEqual(
            self.store.get_version_cache_dir("aaa", "v1"),
            self.store.get_version_cache_dir("aaa", "v2"))


class _NoCacheConfig(SiteConfig):
    def get_default_cache_dir(self):
        return ""
    def get_ivpm_install_args(self):
        return ["ivpm"]


class _DefaultDirConfig(SiteConfig):
    def __init__(self, d):
        self._d = d
    def get_default_cache_dir(self):
        return self._d
    def get_ivpm_install_args(self):
        return ["ivpm"]


class TestCacheProviderFactory(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = mkctx(os.path.join(self.tmp, "deps"))
        self._saved = os.environ.pop("IVPM_CACHE", None)
        reset_site_config()

    def tearDown(self):
        if self._saved is not None:
            os.environ["IVPM_CACHE"] = self._saved
        else:
            os.environ.pop("IVPM_CACHE", None)
        reset_site_config()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_null_when_ivpm_cache_empty(self):
        with patch.dict(os.environ, {"IVPM_CACHE": ""}):
            p = _DefaultDirConfig("/some/dir").get_cache_provider(self.ctx)
            self.assertIsInstance(p, NullCacheProvider)

    def test_null_when_site_default_empty_and_env_unset(self):
        p = _NoCacheConfig().get_cache_provider(self.ctx)
        self.assertIsInstance(p, NullCacheProvider)

    def test_directory_when_ivpm_cache_set(self):
        with patch.dict(os.environ, {"IVPM_CACHE": self.tmp}):
            p = _NoCacheConfig().get_cache_provider(self.ctx)
            self.assertIsInstance(p, DirectoryCacheProvider)

    def test_env_overrides_site_default(self):
        # site default points elsewhere; IVPM_CACHE wins
        with patch.dict(os.environ, {"IVPM_CACHE": self.tmp}):
            p = _DefaultDirConfig("/some/other/dir").get_cache_provider(self.ctx)
            self.assertIsInstance(p, DirectoryCacheProvider)
            self.assertEqual(p._store.cache_dir, self.tmp)

    def test_directory_from_site_default(self):
        p = _DefaultDirConfig(self.tmp).get_cache_provider(self.ctx)
        self.assertIsInstance(p, DirectoryCacheProvider)
        self.assertEqual(p._store.cache_dir, self.tmp)

    def test_never_returns_none(self):
        for cfg in (_NoCacheConfig(), _DefaultDirConfig(""), _DefaultDirConfig(self.tmp)):
            self.assertIsNotNone(cfg.get_cache_provider(self.ctx))

    def test_custom_provider_override_is_honored(self):
        sentinel = NullCacheProvider(self.ctx)

        class _CustomConfig(SiteConfig):
            def get_default_cache_dir(self_):
                return ""
            def get_ivpm_install_args(self_):
                return ["ivpm"]
            def get_cache_provider(self_, context):
                return sentinel

        self.assertIs(_CustomConfig().get_cache_provider(self.ctx), sentinel)


class TestUpdateInfoCacheProvider(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.pop("IVPM_CACHE", None)
        reset_site_config()

    def tearDown(self):
        if self._saved is not None:
            os.environ["IVPM_CACHE"] = self._saved
        else:
            os.environ.pop("IVPM_CACHE", None)
        reset_site_config()

    def test_context_carries_root_project_fields(self):
        captured = {}

        class _CapturingConfig(SiteConfig):
            def get_default_cache_dir(self_):
                return ""
            def get_ivpm_install_args(self_):
                return ["ivpm"]
            def get_cache_provider(self_, context):
                captured["ctx"] = context
                return NullCacheProvider(context)

        ui = ProjectUpdateInfo(
            None, "/proj/deps",
            project_name="proj", project_version="2.3", project_dir="/proj")
        with patch("ivpm.site_config.get_site_config", return_value=_CapturingConfig()):
            ui.get_cache_provider()
        ctx = captured["ctx"]
        self.assertEqual(ctx.root_name, "proj")
        self.assertEqual(ctx.root_version, "2.3")
        self.assertEqual(ctx.root_dir, "/proj")
        self.assertEqual(ctx.deps_dir, "/proj/deps")

    def test_memoized_single_instance(self):
        ui = ProjectUpdateInfo(None, "/proj/deps")
        with patch("ivpm.site_config.get_site_config",
                   return_value=_NoCacheConfig()):
            p1 = ui.get_cache_provider()
            p2 = ui.get_cache_provider()
        self.assertIs(p1, p2)

    def test_null_provider_when_cache_unset(self):
        ui = ProjectUpdateInfo(None, "/proj/deps")
        with patch("ivpm.site_config.get_site_config",
                   return_value=_NoCacheConfig()):
            self.assertIsInstance(ui.get_cache_provider(), NullCacheProvider)


class TestArtifactCacheIdentity(unittest.TestCase):
    """The cache version must identify the *resolved artifact*, not the inputs
    that chose it.

    The cache is keyed on (package name, version), and one package name can
    resolve to a different URL per platform once its ``url:`` reads a platform
    variable. If the version does not say which, a shared cache serves the
    first writer's bytes to every other platform -- silently.
    """

    def _http_pkg(self, url, derived=None):
        from ivpm.pkg_types.package_http import PackageHttp
        p = PackageHttp("emsdk")
        p.url = url
        p.used_derived_vars = set(derived or ())
        return p

    def _version(self, pkg, base="etag-abc"):
        with patch.object(type(pkg), "_get_url_version", return_value=base):
            return pkg._cache_version()

    def test_same_package_two_platforms_differ(self):
        # Same package, same ETag-shaped base version, different artifact.
        linux = self._http_pkg("https://ex.com/linux/wasm.tar.xz", {"p"})
        macos = self._http_pkg("https://ex.com/mac/wasm.tar.xz", {"p"})
        self.assertNotEqual(self._version(linux), self._version(macos))

    def test_no_derived_vars_is_byte_identical_to_the_base_version(self):
        # The guard against invalidating every existing cache entry: a
        # package that uses no platform variable must key exactly as before.
        pkg = self._http_pkg("https://ex.com/x.tar.gz")
        self.assertEqual("etag-abc", self._version(pkg))

    def test_derived_version_extends_the_base_version(self):
        pkg = self._http_pkg("https://ex.com/linux/x.tar.xz", {"p"})
        version = self._version(pkg)
        self.assertTrue(version.startswith("etag-abc_"), version)
        self.assertNotEqual("etag-abc", version)

    def test_version_is_stable_for_the_same_url(self):
        a = self._http_pkg("https://ex.com/linux/x.tar.xz", {"p"})
        b = self._http_pkg("https://ex.com/linux/x.tar.xz", {"p", "sfx"})
        self.assertEqual(self._version(a), self._version(b))


class TestGhRlsCacheIdentity(unittest.TestCase):
    """The design 2.4 regression: the gh-rls cache key omitted the selection
    inputs, so two machines that select *different* assets stored them under
    one key. Keying on the selected asset URL is stable no matter what a
    future selector consults."""

    def _pkg(self):
        from ivpm.pkg_types.package_gh_rls import PackageGhRls
        return PackageGhRls("mytool")

    def _version(self, asset_url, release_tag="v1.2.3"):
        # The production key computation, exercised without the network:
        # _update_with_cache calls exactly this with the selected asset URL.
        return self._pkg()._cache_version(release_tag, asset_url)

    def _select(self, assets, arch, glibc):
        pkg = self._pkg()
        return pkg._select_linux_asset(assets, arch, glibc)

    def test_two_glibc_values_select_different_assets_and_keys(self):
        assets = [
            {"name": "mytool-manylinux_2_17_x86_64.tar.gz",
             "browser_download_url": "https://ex.com/a-2-17.tar.gz"},
            {"name": "mytool-manylinux_2_34_x86_64.tar.gz",
             "browser_download_url": "https://ex.com/a-2-34.tar.gz"},
        ]
        old = self._select(assets, "x86_64", (2, 17))
        new = self._select(assets, "x86_64", (2, 34))
        self.assertIsNotNone(old)
        self.assertIsNotNone(new)
        old_url = old["browser_download_url"]
        new_url = new["browser_download_url"]
        self.assertNotEqual(old_url, new_url)
        # Before the fix both were "v1.2.3_linux_x86_64".
        self.assertNotEqual(self._version(old_url), self._version(new_url))

    def test_two_distro_versions_select_different_assets_and_keys(self):
        assets = [
            {"name": "mytool-ubuntu-22.04-x86_64.tar.gz",
             "browser_download_url": "https://ex.com/jammy.tar.gz"},
            {"name": "mytool-ubuntu-24.04-x86_64.tar.gz",
             "browser_download_url": "https://ex.com/noble.tar.gz"},
        ]
        pkg = self._pkg()
        with patch.object(type(pkg), "_get_linux_distro_info",
                          return_value=("ubuntu", "22.04")):
            jammy = pkg._select_linux_asset(assets, "x86_64", (2, 35))
        with patch.object(type(pkg), "_get_linux_distro_info",
                          return_value=("ubuntu", "24.04")):
            noble = pkg._select_linux_asset(assets, "x86_64", (2, 39))
        self.assertIsNotNone(jammy)
        self.assertIsNotNone(noble)
        jammy_url = jammy["browser_download_url"]
        noble_url = noble["browser_download_url"]
        self.assertNotEqual(jammy_url, noble_url)
        self.assertNotEqual(self._version(jammy_url), self._version(noble_url))

    def test_same_asset_gives_the_same_key(self):
        url = "https://ex.com/a-2-17.tar.gz"
        self.assertEqual(self._version(url), self._version(url))


if __name__ == '__main__':
    unittest.main()
