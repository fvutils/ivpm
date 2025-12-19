import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from .test_base import TestBase

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.cache import Cache, is_github_url, parse_github_url, CacheResult
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
    
    def test_git_no_cache_readonly(self):
        """Test that cache=false makes files read-only without cache."""
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
        self.assertTrue(os.path.isdir(pkg_dir))
        
        # Verify files are read-only
        test_file = os.path.join(pkg_dir, "test.txt")
        mode = os.stat(test_file).st_mode
        self.assertFalse(mode & stat.S_IWUSR)
        
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
