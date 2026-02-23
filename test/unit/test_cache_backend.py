"""
Layer 1 unit tests for the cache backend abstraction.

Tests the ABC contract, FilesystemCacheBackend, BackendRegistry, and
GHACacheBackend logic (with the GHA client mocked out).
"""
import os
import stat
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from .test_base import TestBase


class TestFilesystemCacheBackend(TestBase):
    """Tests for FilesystemCacheBackend."""

    def setUp(self):
        super().setUp()
        from ivpm.cache_backend.filesystem import FilesystemCacheBackend
        self.cache_dir = os.path.join(self.testdir, "cache")
        os.makedirs(self.cache_dir)
        self.backend = FilesystemCacheBackend(self.cache_dir)

    def _make_pkg_dir(self, name="mypkg"):
        """Create a temporary source directory with a dummy file."""
        src = os.path.join(self.testdir, f"src_{name}")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "file.txt"), "w") as f:
            f.write("content")
        return src

    def test_is_available_with_env(self):
        from ivpm.cache_backend.filesystem import FilesystemCacheBackend
        with patch.dict(os.environ, {"IVPM_CACHE": "/some/path"}):
            self.assertTrue(FilesystemCacheBackend.is_available())

    def test_is_available_without_env(self):
        from ivpm.cache_backend.filesystem import FilesystemCacheBackend
        env = {k: v for k, v in os.environ.items() if k != "IVPM_CACHE"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(FilesystemCacheBackend.is_available())

    def test_has_version_miss(self):
        self.assertFalse(self.backend.has_version("mypkg", "abc123"))

    def test_store_and_has_version(self):
        src = self._make_pkg_dir()
        self.backend.store_version("mypkg", "abc123", src)
        self.assertTrue(self.backend.has_version("mypkg", "abc123"))
        self.assertFalse(os.path.exists(src))  # source was moved

    def test_store_version_makes_readonly(self):
        src = self._make_pkg_dir()
        cached = self.backend.store_version("mypkg", "abc123", src)
        fpath = os.path.join(cached, "file.txt")
        mode = os.stat(fpath).st_mode
        self.assertFalse(mode & stat.S_IWUSR, "file should be read-only")

    def test_store_idempotent(self):
        src = self._make_pkg_dir()
        path1 = self.backend.store_version("mypkg", "abc123", src)
        # Second call with same version should not fail even if src is gone
        src2 = self._make_pkg_dir()
        path2 = self.backend.store_version("mypkg", "abc123", src2)
        self.assertEqual(path1, path2)

    def test_link_to_deps(self):
        src = self._make_pkg_dir()
        self.backend.store_version("mypkg", "abc123", src)
        deps_dir = os.path.join(self.testdir, "deps")
        os.makedirs(deps_dir)
        link = self.backend.link_to_deps("mypkg", "abc123", deps_dir)
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.isfile(os.path.join(link, "file.txt")))

    def test_clean_older_than_removes_old(self):
        import time
        src = self._make_pkg_dir()
        ver_dir = self.backend.store_version("mypkg", "abc123", src)
        # Back-date the mtime
        old_time = time.time() - (40 * 86400)
        os.utime(ver_dir, (old_time, old_time))
        removed = self.backend.clean_older_than(30)
        self.assertEqual(removed, 1)
        self.assertFalse(self.backend.has_version("mypkg", "abc123"))

    def test_clean_older_than_keeps_new(self):
        src = self._make_pkg_dir()
        self.backend.store_version("mypkg", "abc123", src)
        removed = self.backend.clean_older_than(30)
        self.assertEqual(removed, 0)
        self.assertTrue(self.backend.has_version("mypkg", "abc123"))

    def test_get_info(self):
        src = self._make_pkg_dir()
        self.backend.store_version("mypkg", "abc123", src)
        info = self.backend.get_info()
        self.assertEqual(len(info["packages"]), 1)
        self.assertEqual(info["packages"][0]["name"], "mypkg")
        self.assertGreater(info["total_size"], 0)


class TestBackendRegistry(TestBase):
    """Tests for BackendRegistry auto-detect and explicit selection."""

    def _clean_env(self):
        env = dict(os.environ)
        env.pop("IVPM_CACHE", None)
        env.pop("IVPM_CACHE_BACKEND", None)
        env.pop("ACTIONS_CACHE_URL", None)
        env.pop("ACTIONS_RUNTIME_TOKEN", None)
        return env

    def test_select_none(self):
        from ivpm.cache_backend.registry import BackendRegistry
        self.assertIsNone(BackendRegistry.select("none"))

    def test_select_explicit_filesystem(self):
        from ivpm.cache_backend.registry import BackendRegistry
        from ivpm.cache_backend.filesystem import FilesystemCacheBackend
        with patch.dict(os.environ, {"IVPM_CACHE": self.testdir}):
            backend = BackendRegistry.select("filesystem")
        self.assertIsInstance(backend, FilesystemCacheBackend)

    def test_auto_detect_filesystem(self):
        from ivpm.cache_backend.registry import BackendRegistry
        from ivpm.cache_backend.filesystem import FilesystemCacheBackend
        env = self._clean_env()
        env["IVPM_CACHE"] = self.testdir
        with patch.dict(os.environ, env, clear=True):
            backend = BackendRegistry.select()
        self.assertIsInstance(backend, FilesystemCacheBackend)

    def test_auto_detect_no_backend(self):
        from ivpm.cache_backend.registry import BackendRegistry
        env = self._clean_env()
        with patch.dict(os.environ, env, clear=True):
            backend = BackendRegistry.select()
        self.assertIsNone(backend)

    def test_env_var_override(self):
        from ivpm.cache_backend.registry import BackendRegistry
        env = self._clean_env()
        env["IVPM_CACHE_BACKEND"] = "none"
        with patch.dict(os.environ, env, clear=True):
            backend = BackendRegistry.select()
        self.assertIsNone(backend)

    def test_explicit_beats_env_var(self):
        from ivpm.cache_backend.registry import BackendRegistry
        env = self._clean_env()
        env["IVPM_CACHE_BACKEND"] = "filesystem"
        env["IVPM_CACHE"] = self.testdir
        with patch.dict(os.environ, env, clear=True):
            backend = BackendRegistry.select("none")
        self.assertIsNone(backend)


class TestGHACacheBackendLogic(TestBase):
    """Layer 1: GHACacheBackend logic with mocked GHACacheClient."""

    def setUp(self):
        super().setUp()
        self.local_dir = os.path.join(self.testdir, "local_cache")
        os.makedirs(self.local_dir)

    def _make_backend(self, mock_client):
        from ivpm.cache_backend.gha import GHACacheBackend
        return GHACacheBackend(local_dir=self.local_dir, client=mock_client)

    def _make_pkg_src(self, name="pkg1"):
        src = os.path.join(self.testdir, f"src_{name}")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("data")
        return src

    def test_is_available_with_env(self):
        from ivpm.cache_backend.gha import GHACacheBackend
        with patch.dict(os.environ, {
            "ACTIONS_CACHE_URL": "http://x/",
            "ACTIONS_RUNTIME_TOKEN": "tok",
        }):
            self.assertTrue(GHACacheBackend.is_available())

    def test_is_available_without_env(self):
        from ivpm.cache_backend.gha import GHACacheBackend
        env = {k: v for k, v in os.environ.items()
               if k not in ("ACTIONS_CACHE_URL", "ACTIONS_RUNTIME_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(GHACacheBackend.is_available())

    def test_has_version_local_hit(self):
        """L1 hit should not call the GHA client."""
        mock_client = MagicMock()
        backend = self._make_backend(mock_client)
        src = self._make_pkg_src()
        backend.store_version("pkg1", "v1", src)
        # L1 now populated; lookup should not hit the client
        result = backend.has_version("pkg1", "v1")
        self.assertTrue(result)
        mock_client.lookup.assert_not_called()

    def test_has_version_gha_hit(self):
        """L2 hit should download and return True."""
        mock_client = MagicMock()
        mock_client.pkg_key.return_value = "ivpm-pkg-Linux-pkg1-v1"
        mock_client.lookup.return_value = "http://example.com/archive.tar.gz"

        # Simulate download by creating the destination dir
        def fake_download(url, dest):
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, "f.txt"), "w") as f:
                f.write("data")
        mock_client.download.side_effect = fake_download

        backend = self._make_backend(mock_client)
        result = backend.has_version("pkg1", "v1")
        self.assertTrue(result)
        mock_client.lookup.assert_called_once()
        mock_client.download.assert_called_once()

    def test_has_version_gha_miss(self):
        """Complete miss: local and GHA both miss."""
        mock_client = MagicMock()
        mock_client.pkg_key.return_value = "ivpm-pkg-Linux-pkg1-v1"
        mock_client.lookup.return_value = None
        backend = self._make_backend(mock_client)
        result = backend.has_version("pkg1", "v1")
        self.assertFalse(result)

    def test_store_version_triggers_upload(self):
        """store_version should store locally and schedule a GHA upload."""
        mock_client = MagicMock()
        mock_client.pkg_key.return_value = "ivpm-pkg-Linux-pkg1-v1"
        mock_client.upload.return_value = True
        backend = self._make_backend(mock_client)
        src = self._make_pkg_src()
        backend.store_version("pkg1", "v1", src)
        # The upload is async; drain the executor
        backend.deactivate(success=True)
        mock_client.upload.assert_called_once()

    def test_deactivate_failure_skips_venv_save(self):
        """deactivate(success=False) should NOT upload venv."""
        mock_client = MagicMock()
        backend = self._make_backend(mock_client)
        backend._venv_rebuilt = True
        backend._venv_dir = self.testdir
        backend._py_version = "3.11"
        backend._req_hash = "abc"
        backend.deactivate(success=False)
        mock_client.upload.assert_not_called()

    def test_pip_cache_dir_exposed(self):
        """pip_cache_dir should be set after activate()."""
        mock_client = MagicMock()
        mock_client.pip_restore_key.return_value = "ivpm-pip-Linux-"
        mock_client.lookup.return_value = None  # cache miss
        backend = self._make_backend(mock_client)
        backend.activate()
        self.assertIsNotNone(backend.pip_cache_dir)
        self.assertTrue(os.path.isdir(backend.pip_cache_dir))


if __name__ == "__main__":
    unittest.main()
