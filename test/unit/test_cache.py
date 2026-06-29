import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from .test_base import TestBase

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.cache import Cache, DirectoryCacheStore, is_github_url, parse_github_url
from ivpm.project_ops_info import ProjectUpdateInfo


class TestCacheHelpers(unittest.TestCase):
    """Test helper functions in cache module."""
    
    def test_is_github_url_https(self):
        self.assertTrue(is_github_url("https://github.com/owner/repo.git"))
        self.assertTrue(is_github_url("https://github.com/owner/repo"))
        
    def test_is_github_url_git(self):
        self.assertTrue(is_github_url("git@github.com:owner/repo.git"))
        
    def test_is_github_url_false(self):
        self.assertFalse(is_github_url("https://gitlab.com/owner/repo.git"))
        self.assertFalse(is_github_url("https://bitbucket.org/owner/repo.git"))
        
    def test_parse_github_url_https(self):
        owner, repo = parse_github_url("https://github.com/fvutils/vlsim.git")
        self.assertEqual(owner, "fvutils")
        self.assertEqual(repo, "vlsim")
        
    def test_parse_github_url_https_no_git(self):
        owner, repo = parse_github_url("https://github.com/fvutils/vlsim")
        self.assertEqual(owner, "fvutils")
        self.assertEqual(repo, "vlsim")
        
    def test_parse_github_url_ssh(self):
        owner, repo = parse_github_url("git@github.com:fvutils/vlsim.git")
        self.assertEqual(owner, "fvutils")
        self.assertEqual(repo, "vlsim")
        
    def test_parse_github_url_with_path(self):
        owner, repo = parse_github_url("https://github.com/fvutils/vlsim/tree/main")
        self.assertEqual(owner, "fvutils")
        self.assertEqual(repo, "vlsim")
        
    def test_parse_github_url_invalid(self):
        owner, repo = parse_github_url("https://gitlab.com/owner/repo.git")
        self.assertIsNone(owner)
        self.assertIsNone(repo)


class TestCache(unittest.TestCase):
    """Test Cache class."""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.deps_dir = os.path.join(self.test_dir, "deps")
        os.makedirs(self.deps_dir)
        self.cache = Cache(self.cache_dir)
        
    def tearDown(self):
        # Need to make files writable before removal
        for root, dirs, files in os.walk(self.test_dir):
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    os.chmod(dir_path, stat.S_IRWXU)
                except:
                    pass
            for f in files:
                file_path = os.path.join(root, f)
                try:
                    os.chmod(file_path, stat.S_IRWXU)
                except:
                    pass
        shutil.rmtree(self.test_dir)
        
    def test_get_package_cache_dir(self):
        path = self.cache.get_package_cache_dir("mypackage")
        self.assertEqual(path, os.path.join(self.cache_dir, "mypackage"))
        
    def test_get_version_cache_dir(self):
        path = self.cache.get_version_cache_dir("mypackage", "abc123")
        self.assertEqual(path, os.path.join(self.cache_dir, "mypackage", "abc123"))
        
    def test_has_version_false(self):
        self.assertFalse(self.cache.has_version("mypackage", "abc123"))
        
    def test_has_version_true(self):
        version_dir = self.cache.get_version_cache_dir("mypackage", "abc123")
        os.makedirs(version_dir)
        self.assertTrue(self.cache.has_version("mypackage", "abc123"))
        
    def test_ensure_cache_dir(self):
        pkg_dir = self.cache.ensure_cache_dir("mypackage")
        self.assertTrue(os.path.isdir(pkg_dir))
        self.assertEqual(pkg_dir, self.cache.get_package_cache_dir("mypackage"))
        
    def test_store_version(self):
        # Create a source directory
        source_dir = os.path.join(self.test_dir, "source")
        os.makedirs(source_dir)
        with open(os.path.join(source_dir, "test.txt"), "w") as f:
            f.write("test content")
            
        # Store in cache
        cached_path = self.cache.store_version("mypackage", "abc123", source_dir)
        
        # Verify
        self.assertTrue(os.path.isdir(cached_path))
        self.assertTrue(os.path.isfile(os.path.join(cached_path, "test.txt")))
        self.assertFalse(os.path.exists(source_dir))  # Source was moved
        
        # Verify read-only
        test_file = os.path.join(cached_path, "test.txt")
        mode = os.stat(test_file).st_mode
        self.assertFalse(mode & stat.S_IWUSR)
        
    def test_link_to_deps(self):
        # Create a cached version
        version_dir = self.cache.get_version_cache_dir("mypackage", "abc123")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "test.txt"), "w") as f:
            f.write("test content")
            
        # Link to deps
        link_path = self.cache.link_to_deps("mypackage", "abc123", self.deps_dir)
        
        # Verify
        self.assertTrue(os.path.islink(link_path))
        self.assertEqual(os.path.realpath(link_path), version_dir)
        self.assertTrue(os.path.isfile(os.path.join(link_path, "test.txt")))
        
    def test_link_to_deps_replaces_existing(self):
        # Create existing directory
        existing = os.path.join(self.deps_dir, "mypackage")
        os.makedirs(existing)
        with open(os.path.join(existing, "old.txt"), "w") as f:
            f.write("old content")
            
        # Create a cached version
        version_dir = self.cache.get_version_cache_dir("mypackage", "abc123")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "new.txt"), "w") as f:
            f.write("new content")
            
        # Link to deps
        link_path = self.cache.link_to_deps("mypackage", "abc123", self.deps_dir)
        
        # Verify old directory replaced with link
        self.assertTrue(os.path.islink(link_path))
        self.assertTrue(os.path.isfile(os.path.join(link_path, "new.txt")))
        self.assertFalse(os.path.exists(os.path.join(link_path, "old.txt")))
        
    def test_get_cache_info_empty(self):
        info = self.cache.get_cache_info()
        self.assertEqual(info["packages"], [])
        self.assertEqual(info["total_size"], 0)
        
    def test_get_cache_info_with_data(self):
        # Create some cached packages
        v1 = self.cache.get_version_cache_dir("pkg1", "v1")
        os.makedirs(v1)
        with open(os.path.join(v1, "test.txt"), "w") as f:
            f.write("content")
            
        v2 = self.cache.get_version_cache_dir("pkg1", "v2")
        os.makedirs(v2)
        with open(os.path.join(v2, "test.txt"), "w") as f:
            f.write("more content")
            
        info = self.cache.get_cache_info()
        self.assertEqual(len(info["packages"]), 1)
        self.assertEqual(info["packages"][0]["name"], "pkg1")
        self.assertEqual(len(info["packages"][0]["versions"]), 2)
        self.assertGreater(info["total_size"], 0)
        
    def test_clean_older_than(self):
        import time
        
        # Create a cached version
        v1 = self.cache.get_version_cache_dir("pkg1", "v1")
        os.makedirs(v1)
        with open(os.path.join(v1, "test.txt"), "w") as f:
            f.write("content")
        self.cache._make_readonly(v1)
        
        # Set modification time to 10 days ago
        old_time = time.time() - (10 * 24 * 60 * 60)
        os.utime(v1, (old_time, old_time))
        
        # Create a recent version
        v2 = self.cache.get_version_cache_dir("pkg1", "v2")
        os.makedirs(v2)
        with open(os.path.join(v2, "test.txt"), "w") as f:
            f.write("new content")
            
        # Clean entries older than 7 days
        removed = self.cache.clean_older_than(7)
        
        self.assertEqual(removed, 1)
        self.assertFalse(self.cache.has_version("pkg1", "v1"))
        self.assertTrue(self.cache.has_version("pkg1", "v2"))


class TestCacheStaleTracking(unittest.TestCase):
    """Sidecar-based last-used tracking for cache GC."""

    DAY = 24 * 60 * 60

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.deps_dir = os.path.join(self.test_dir, "deps")
        os.makedirs(self.deps_dir)
        self.cache = DirectoryCacheStore(self.cache_dir)

    def tearDown(self):
        for root, dirs, files in os.walk(self.test_dir):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), stat.S_IRWXU)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), stat.S_IRWXU)
                except OSError:
                    pass
        shutil.rmtree(self.test_dir)

    # -- helpers --

    def _mk_source(self, name="src", content="hello"):
        src = os.path.join(self.test_dir, name)
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write(content)
        return src

    def _meta_path(self, pkg, version):
        return os.path.join(self.cache_dir, pkg, version + ".meta.json")

    def _read_meta(self, pkg, version):
        with open(self._meta_path(pkg, version)) as f:
            return json.load(f)

    def _backdate_dir(self, pkg, version, days):
        t = time.time() - days * self.DAY
        os.utime(self.cache.get_version_cache_dir(pkg, version), (t, t))

    def _set_meta(self, pkg, version, **kw):
        meta = self._read_meta(pkg, version)
        meta.update(kw)
        with open(self._meta_path(pkg, version), "w") as f:
            json.dump(meta, f)

    # -- sidecar lifecycle --

    def test_store_writes_sidecar(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        meta = self._read_meta("pkg", "v1")
        self.assertEqual(meta["schema"], 1)
        self.assertIn("stored", meta)
        self.assertIn("last_linked", meta)
        # stored == last_linked at creation
        self.assertAlmostEqual(meta["stored"], meta["last_linked"], places=2)

    def test_link_refreshes_last_linked_preserving_stored(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        # Back-date both so a fresh link is detectably newer
        old = time.time() - 5 * self.DAY
        self._set_meta("pkg", "v1", stored=old, last_linked=old)

        self.cache.link_to_deps("pkg", "v1", self.deps_dir)

        meta = self._read_meta("pkg", "v1")
        self.assertAlmostEqual(meta["stored"], old, places=2)   # preserved
        self.assertGreater(meta["last_linked"], old)            # refreshed

    def test_sidecar_write_is_atomic_no_tmp_left(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        leftovers = [n for n in os.listdir(os.path.join(self.cache_dir, "pkg"))
                     if ".tmp." in n]
        self.assertEqual(leftovers, [])

    def test_sidecar_is_group_writable(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        mode = os.stat(self._meta_path("pkg", "v1")).st_mode
        self.assertTrue(mode & stat.S_IWGRP)

    # -- GC by last-used --

    def test_gc_prunes_when_last_used_is_old(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        self._backdate_dir("pkg", "v1", 10)
        self._set_meta("pkg", "v1",
                       stored=time.time() - 10 * self.DAY,
                       last_linked=time.time() - 10 * self.DAY)

        removed = self.cache.clean_older_than(7)

        self.assertEqual(removed, 1)
        self.assertFalse(self.cache.has_version("pkg", "v1"))
        # sidecar removed with the entry
        self.assertFalse(os.path.exists(self._meta_path("pkg", "v1")))

    def test_gc_keeps_recently_linked_even_if_stored_is_old(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        # Entry first cached long ago (old dir mtime + old stored) ...
        self._backdate_dir("pkg", "v1", 30)
        self._set_meta("pkg", "v1", stored=time.time() - 30 * self.DAY)
        # ... but referenced into a workspace just now.
        self.cache.touch_linked("pkg", "v1")

        removed = self.cache.clean_older_than(7)

        self.assertEqual(removed, 0)
        self.assertTrue(self.cache.has_version("pkg", "v1"))

    # -- backward compatibility (no sidecar) --

    def test_gc_falls_back_to_mtime_without_sidecar(self):
        # Hand-build an entry with no sidecar (pre-existing cache)
        v = self.cache.get_version_cache_dir("pkg", "v1")
        os.makedirs(v)
        with open(os.path.join(v, "f.txt"), "w") as f:
            f.write("x")
        self._backdate_dir("pkg", "v1", 10)
        self.assertFalse(os.path.exists(self._meta_path("pkg", "v1")))

        removed = self.cache.clean_older_than(7)

        self.assertEqual(removed, 1)
        self.assertFalse(self.cache.has_version("pkg", "v1"))

    # -- orphan sweep --

    def test_orphan_sidecar_is_swept(self):
        self.cache.ensure_cache_dir("pkg")
        orphan = self._meta_path("pkg", "ghost")
        with open(orphan, "w") as f:
            json.dump({"schema": 1, "stored": 0, "last_linked": 0}, f)

        self.cache.clean_older_than(7)

        self.assertFalse(os.path.exists(orphan))

    # -- touch via symlink target --

    def test_touch_linked_target_resolves_symlink(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        link = self.cache.link_to_deps("pkg", "v1", self.deps_dir)
        old = time.time() - 5 * self.DAY
        self._set_meta("pkg", "v1", last_linked=old)

        self.assertTrue(self.cache.touch_linked_target(link))
        self.assertGreater(self._read_meta("pkg", "v1")["last_linked"], old)

    def test_touch_linked_target_ignores_outside_cache(self):
        outside = os.path.join(self.test_dir, "elsewhere")
        os.makedirs(outside)
        self.assertFalse(self.cache.touch_linked_target(outside))

    # -- dry-run --

    def test_dry_run_removes_nothing(self):
        self.cache.store_version("pkg", "v1", self._mk_source())
        self._backdate_dir("pkg", "v1", 10)
        self._set_meta("pkg", "v1",
                       stored=time.time() - 10 * self.DAY,
                       last_linked=time.time() - 10 * self.DAY)

        removed = self.cache.clean_older_than(7, dry_run=True)

        self.assertEqual(removed, 1)            # reports the candidate
        self.assertTrue(self.cache.has_version("pkg", "v1"))  # but keeps it


class TestCacheGit(TestBase):
    """Test git caching integration."""
    
    def setUp(self):
        super().setUp()
        # Set up IVPM_CACHE for caching tests
        self.cache_dir = os.path.join(self.testdir, ".ivpm_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.environ["IVPM_CACHE"] = self.cache_dir
    
    def tearDown(self):
        # Clean up IVPM_CACHE env var
        if "IVPM_CACHE" in os.environ:
            del os.environ["IVPM_CACHE"]
        super().tearDown()
    
    def _init_git_repo(self, path, files=None, branch='main'):
        """Initialize a git repo for testing."""
        os.makedirs(path, exist_ok=True)
        subprocess.check_call(["git", "init", "-b", branch], cwd=path)
        subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=path)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=path)
        if files:
            for rel, content in files.items():
                full = os.path.join(path, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
                with open(full, 'w') as f:
                    f.write(content)
        if not os.path.exists(os.path.join(path, 'ivpm.yaml')):
            with open(os.path.join(path, 'ivpm.yaml'), 'w') as f:
                f.write('package:\n  name: sample\n  dep-sets:\n    - name: default-dev\n      deps: []\n')
        subprocess.check_call(["git", "add", "-A"], cwd=path)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path)
    
    def test_git_cache_local_file_url(self):
        """Test that cache=true works for local file:// git URLs using git ls-remote."""
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "test content"})
        
        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
                      cache: true
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        # Should be a symlink to cache
        self.assertTrue(os.path.islink(pkg_dir))
        self.assertTrue(os.path.isfile(os.path.join(pkg_dir, "test.txt")))

        # Verify files are read-only since cached
        test_file = os.path.join(pkg_dir, "test.txt")
        mode = os.stat(test_file).st_mode
        self.assertFalse(mode & stat.S_IWUSR)

    def test_git_cache_rerun_refreshes_last_linked(self):
        """The already-loaded fast path bumps last_linked on a second update."""
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "test content"})

        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
                      cache: true
        """)

        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        version = os.path.basename(os.path.realpath(pkg_dir))
        meta_path = os.path.join(self.cache_dir, "test_pkg", version + ".meta.json")
        self.assertTrue(os.path.exists(meta_path))

        # Back-date last_linked so a refresh is detectable.
        with open(meta_path) as f:
            meta = json.load(f)
        old = time.time() - 10 * 24 * 60 * 60
        meta["last_linked"] = old
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        # Second update hits the already-loaded fast path (symlink exists).
        self.ivpm_update(skip_venv=True)

        with open(meta_path) as f:
            refreshed = json.load(f)
        self.assertGreater(refreshed["last_linked"], old)

    def test_git_no_cache_editable(self):
        """Test that cache=false produces an editable clone without using shared cache."""
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "test content"})
        
        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
                      cache: false
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        # Should be a real directory, not a symlink
        self.assertTrue(os.path.isdir(pkg_dir))
        self.assertFalse(os.path.islink(pkg_dir))
        
        # Verify files are writable (editable)
        test_file = os.path.join(pkg_dir, "test.txt")
        mode = os.stat(test_file).st_mode
        self.assertTrue(mode & stat.S_IWUSR)
        
    def test_git_no_cache_with_depth(self):
        """Test that cache=false with depth=1 produces a shallow editable clone."""
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "test content"})
        
        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
                      cache: false
                      depth: 1
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        self.assertTrue(os.path.isdir(pkg_dir))
        self.assertFalse(os.path.islink(pkg_dir))
        self.assertTrue(os.path.isdir(os.path.join(pkg_dir, ".git")))
        
        # Verify files are writable (editable)
        test_file = os.path.join(pkg_dir, "test.txt")
        mode = os.stat(test_file).st_mode
        self.assertTrue(mode & stat.S_IWUSR)
        
        # Verify it's a shallow clone (depth=1)
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, cwd=pkg_dir
        )
        if result.returncode == 0:
            self.assertEqual(result.stdout.strip(), "1")

    def test_git_unspecified_cache_full_clone(self):
        """Test that cache unspecified does full clone with write access."""
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "test content"})
        
        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        self.assertTrue(os.path.isdir(pkg_dir))
        self.assertTrue(os.path.isdir(os.path.join(pkg_dir, ".git")))
        
        # Verify files are writable (default behavior)
        test_file = os.path.join(pkg_dir, "test.txt")
        mode = os.stat(test_file).st_mode
        self.assertTrue(mode & stat.S_IWUSR)


class TestCacheUnconfigured(TestBase):
    """Test behavior when cache: true is set but IVPM_CACHE is not configured."""

    def setUp(self):
        super().setUp()
        # Ensure IVPM_CACHE is NOT set for these tests
        self._saved_cache = os.environ.pop("IVPM_CACHE", None)
        # Simulate no site config default so the cache is truly disabled.
        # Use a real SiteConfig subclass (not a MagicMock) so the default
        # get_cache_provider() runs and yields a NullCacheProvider.
        from ivpm.site_config import SiteConfig

        class _NoCacheConfig(SiteConfig):
            def get_default_cache_dir(self):
                return ""
            def get_ivpm_install_args(self):
                return ["ivpm"]

        self._site_config_patcher = patch(
            "ivpm.site_config.get_site_config", return_value=_NoCacheConfig())
        self._site_config_patcher.start()

    def tearDown(self):
        self._site_config_patcher.stop()
        if self._saved_cache is not None:
            os.environ["IVPM_CACHE"] = self._saved_cache
        elif "IVPM_CACHE" in os.environ:
            del os.environ["IVPM_CACHE"]
        super().tearDown()

    def _init_git_repo(self, path, files=None, branch='main'):
        os.makedirs(path, exist_ok=True)
        subprocess.check_call(["git", "init", "-b", branch], cwd=path)
        subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=path)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=path)
        if files:
            for rel, content in files.items():
                full = os.path.join(path, rel)
                if os.path.dirname(full):
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write(content)
        if not os.path.exists(os.path.join(path, 'ivpm.yaml')):
            with open(os.path.join(path, 'ivpm.yaml'), 'w') as f:
                f.write('package:\n  name: sample\n  dep-sets:\n    - name: default-dev\n      deps: []\n')
        subprocess.check_call(["git", "add", "-A"], cwd=path)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path)

    def test_cache_true_without_ivpm_cache_shows_warning(self):
        """When cache: true but IVPM_CACHE is unset, the summary warns the user."""
        import io
        from contextlib import redirect_stdout

        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "content"})

        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_unconfigured_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
                      cache: true
        """)

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.ivpm_update(skip_venv=True)
        output = buf.getvalue()

        # Package should still be fetched (falls back to full clone)
        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        self.assertTrue(os.path.isdir(pkg_dir))

        # Summary should contain the IVPM_CACHE warning
        self.assertIn("IVPM_CACHE", output)
        self.assertIn("cache: true", output)

    def test_cache_true_without_ivpm_cache_still_fetches(self):
        """When IVPM_CACHE is unset, cache: true packages fall back to a full clone."""
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "content"})

        self.mkFile("ivpm.yaml", f"""
        package:
            name: cache_unconfigured_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test_pkg
                      url: file://{src_repo}
                      src: git
                      cache: true
        """)

        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "test_pkg")
        self.assertTrue(os.path.isdir(pkg_dir))
        self.assertTrue(os.path.isdir(os.path.join(pkg_dir, ".git")))
        # Falls back to full clone so it should be writable
        mode = os.stat(pkg_dir).st_mode
        self.assertTrue(mode & stat.S_IWUSR)



class TestCacheHttp(TestBase):
    """Test HTTP URL caching."""
    
    def setUp(self):
        super().setUp()
        # Set up IVPM_CACHE for caching tests
        self.cache_dir = os.path.join(self.testdir, ".ivpm_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.environ["IVPM_CACHE"] = self.cache_dir
    
    def tearDown(self):
        # Clean up IVPM_CACHE env var
        if "IVPM_CACHE" in os.environ:
            del os.environ["IVPM_CACHE"]
        super().tearDown()
    
    def test_http_cache_true(self):
        """Test that cache=true works for HTTP URLs."""
        self.mkFile("ivpm.yaml", """
        package:
            name: http_cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: googletest
                      url: https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
                      cache: true
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "googletest")
        # Should be a symlink to cache
        self.assertTrue(os.path.islink(pkg_dir) or os.path.isdir(pkg_dir))
        self.assertTrue(os.path.isfile(os.path.join(pkg_dir, "README.md")))
        
    def test_http_no_cache_readonly(self):
        """Test that cache=false makes HTTP downloads read-only."""
        self.mkFile("ivpm.yaml", """
        package:
            name: http_cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: googletest
                      url: https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
                      cache: false
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "googletest")
        self.assertTrue(os.path.isdir(pkg_dir))
        
        # Verify files are read-only
        readme = os.path.join(pkg_dir, "README.md")
        mode = os.stat(readme).st_mode
        self.assertFalse(mode & stat.S_IWUSR)


class TestCacheGitHub(TestBase):
    """Test GitHub git URL caching (requires network)."""
    
    def setUp(self):
        super().setUp()
        # Set up IVPM_CACHE for caching tests
        self.cache_dir = os.path.join(self.testdir, ".ivpm_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.environ["IVPM_CACHE"] = self.cache_dir
    
    def tearDown(self):
        # Clean up IVPM_CACHE env var
        if "IVPM_CACHE" in os.environ:
            del os.environ["IVPM_CACHE"]
        super().tearDown()
    
    def test_github_cache_creates_symlink(self):
        """Test that cache=true for GitHub URLs creates a symlink."""
        self.mkFile("ivpm.yaml", """
        package:
            name: github_cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
                      cache: true
        """)
        
        self.ivpm_update(skip_venv=True)
        
        pkg_dir = os.path.join(self.testdir, "packages", "vlsim")
        # Should be a symlink to cache
        self.assertTrue(os.path.islink(pkg_dir))
        self.assertTrue(os.path.isfile(os.path.join(pkg_dir, "README.md")))
        
        # Verify files are read-only since cached
        readme = os.path.join(pkg_dir, "README.md")
        mode = os.stat(readme).st_mode
        self.assertFalse(mode & stat.S_IWUSR)
    
    def test_github_cache_hit(self):
        """Test that cache hit scenario works (second update uses cached version)."""
        # First update - creates cache entry
        self.mkFile("ivpm.yaml", """
        package:
            name: github_cache_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
                      branch: master
                      cache: true
        """)
        
        self.ivpm_update(skip_venv=True)
        
        # Verify cached
        pkg_dir = os.path.join(self.testdir, "packages", "vlsim")
        self.assertTrue(os.path.islink(pkg_dir))
        
        # Record the cache target
        cache_target = os.path.realpath(pkg_dir)
        
        # Remove the symlink but keep the cache
        os.unlink(pkg_dir)
        
        # Second update - should use cached version
        self.ivpm_update(skip_venv=True)
        
        # Should be same cache target
        self.assertTrue(os.path.islink(pkg_dir))
        self.assertEqual(os.path.realpath(pkg_dir), cache_target)


class TestCacheGeneralGit(TestBase):
    """Test general git URL support (non-GitHub) using git ls-remote."""
    
    def _init_git_repo(self, path, files=None, branch='main'):
        """Initialize a git repo for testing."""
        os.makedirs(path, exist_ok=True)
        subprocess.check_call(["git", "init", "-b", branch], cwd=path)
        subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=path)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=path)
        if files:
            for rel, content in files.items():
                full = os.path.join(path, rel)
                dirname = os.path.dirname(full)
                if dirname and not os.path.isdir(dirname):
                    os.makedirs(dirname)
                with open(full, 'w') as f:
                    f.write(content)
        if not os.path.exists(os.path.join(path, 'ivpm.yaml')):
            with open(os.path.join(path, 'ivpm.yaml'), 'w') as f:
                f.write('package:\n  name: sample\n  dep-sets:\n    - name: default-dev\n      deps: []\n')
        subprocess.check_call(["git", "add", "-A"], cwd=path)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path)
    
    def test_git_ls_remote_hash_retrieval(self):
        """Test that git ls-remote can be used to get hash for local repos."""
        from ivpm.pkg_types.package_git import PackageGit
        
        src_repo = os.path.join(self.testdir, 'src_repo')
        self._init_git_repo(src_repo, files={"test.txt": "content"})
        
        # Get expected hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=src_repo,
            capture_output=True,
            text=True
        )
        expected_hash = result.stdout.strip()
        
        # Test PackageGit can get hash
        pkg = PackageGit(name="test", url=f"file://{src_repo}")
        retrieved_hash = pkg._get_commit_hash_ls_remote("HEAD")
        
        self.assertEqual(retrieved_hash, expected_hash)


class TestProjectUpdateInfoCache(unittest.TestCase):
    """Test ProjectUpdateInfo cache hit/miss tracking."""
    
    def test_cache_hit_tracking(self):
        update_info = ProjectUpdateInfo(None, "/tmp/deps")
        self.assertEqual(update_info.cache_hits, 0)
        self.assertEqual(update_info.cache_misses, 0)
        
        update_info.report_cache_hit()
        self.assertEqual(update_info.cache_hits, 1)
        
        update_info.report_cache_hit()
        self.assertEqual(update_info.cache_hits, 2)
        
    def test_cache_miss_tracking(self):
        update_info = ProjectUpdateInfo(None, "/tmp/deps")
        
        update_info.report_cache_miss()
        self.assertEqual(update_info.cache_misses, 1)


if __name__ == '__main__':
    unittest.main()
