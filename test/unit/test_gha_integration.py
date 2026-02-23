"""
Layer 3 integration tests for GHACacheBackend and GHACacheClient against a
real GitHub Actions cache server running in Docker.

These tests are **opt-in**: they are skipped unless the environment variable
``IVPM_TEST_GHA_SERVER=1`` is set.  The Docker daemon must be available and
able to pull ``ghcr.io/falcondev-oss/github-actions-cache-server``.

Usage::

    IVPM_TEST_GHA_SERVER=1 python3 -m unittest unit.test_gha_integration -v
"""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from .test_base import TestBase

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_SKIP_REASON = (
    "Set IVPM_TEST_GHA_SERVER=1 and ensure Docker is available to run GHA "
    "integration tests."
)

_RUN_INTEGRATION = os.environ.get("IVPM_TEST_GHA_SERVER", "").strip() in ("1", "true", "yes")

# Docker image for the self-hosted GHA cache server.
# Use v8.x – the last major version that still supports the REST API v1
# used by GHACacheClient.  v9+ dropped v1 in favour of the Twirp API.
_DOCKER_IMAGE = "ghcr.io/falcondev-oss/github-actions-cache-server:8"

# Fake token accepted by the falcondev server (any non-empty string works)
_FAKE_TOKEN = "ivpm-test-token"


# ---------------------------------------------------------------------------
# Docker lifecycle helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_cache_server(data_dir: str) -> tuple:
    """Start the GHA cache server container.

    Returns ``(container_id, port, cache_url)``.
    """
    port = _free_port()
    container_name = f"ivpm-gha-test-{port}"
    cmd = [
        "docker", "run", "--rm", "-d",
        "--name", container_name,
        "-p", f"127.0.0.1:{port}:3000",
        "-e", f"API_BASE_URL=http://127.0.0.1:{port}",
        "-e", "STORAGE_DRIVER=filesystem",
        "-e", "STORAGE_FILESYSTEM_PATH=/data/cache",
        "-e", "DB_DRIVER=sqlite",
        "-e", "DB_SQLITE_PATH=/data/cache-server.db",
        "-v", f"{data_dir}:/data",
        _DOCKER_IMAGE,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to start cache server: {result.stderr}"
        )
    container_id = result.stdout.strip()
    cache_url = f"http://127.0.0.1:{port}/"
    return container_id, port, cache_url


def _wait_for_server(port: int, timeout: float = 30.0) -> None:
    """Poll until the cache server is accepting connections or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                # Give the HTTP layer a moment to initialise after TCP is up
                time.sleep(0.5)
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"GHA cache server did not start on port {port} within {timeout}s")


def _stop_container(container_id: str) -> None:
    """Stop and remove a container (best-effort)."""
    subprocess.run(
        ["docker", "stop", container_id],
        capture_output=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Base class that manages the server lifecycle
# ---------------------------------------------------------------------------

@unittest.skipUnless(_RUN_INTEGRATION, _SKIP_REASON)
class GHAIntegrationBase(TestBase):
    """TestCase base that starts/stops the Docker cache server per class."""

    _container_id: str = ""
    _cache_url: str = ""
    _server_port: int = 0
    _data_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._data_dir = tempfile.mkdtemp(prefix="ivpm-gha-data-")
        try:
            cid, port, url = _start_cache_server(cls._data_dir)
        except RuntimeError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        cls._container_id = cid
        cls._server_port = port
        cls._cache_url = url
        try:
            _wait_for_server(port, timeout=60)
        except TimeoutError as exc:
            _stop_container(cid)
            raise unittest.SkipTest(str(exc)) from exc

    @classmethod
    def tearDownClass(cls):
        if cls._container_id:
            _stop_container(cls._container_id)
        if cls._data_dir and os.path.isdir(cls._data_dir):
            shutil.rmtree(cls._data_dir, ignore_errors=True)

    def _make_client(self, key_prefix: str = "ivpm"):
        from ivpm.cache_backend.gha_client import GHACacheClient
        return GHACacheClient(
            cache_url=self._cache_url,
            token=_FAKE_TOKEN,
            key_prefix=key_prefix,
            os_name="TestOS",
        )

    def _make_src_dir(self, name: str = "pkg", content: str = "hello") -> str:
        src = os.path.join(self.testdir, f"src_{name}")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "data.txt"), "w") as f:
            f.write(content)
        return src


# ---------------------------------------------------------------------------
# L3-A: GHACacheClient against the real server
# ---------------------------------------------------------------------------

class TestGHACacheClientIntegration(GHAIntegrationBase):
    """GHACacheClient round-trip tests against the Docker cache server."""

    def test_lookup_miss(self):
        client = self._make_client()
        self.assertIsNone(client.lookup("ivpm-pkg-TestOS-missing-deadbeef"))

    def test_upload_and_lookup(self):
        client = self._make_client()
        src = self._make_src_dir("up", "upload-test")
        key = "ivpm-pkg-TestOS-upload-v1"
        ok = client.upload(key, src)
        self.assertTrue(ok)
        url = client.lookup(key)
        self.assertIsNotNone(url)

    def test_roundtrip(self):
        client = self._make_client()
        src = self._make_src_dir("rt", "roundtrip-data")
        key = "ivpm-pkg-TestOS-roundtrip-v1"
        client.upload(key, src)

        url = client.lookup(key)
        self.assertIsNotNone(url)

        dest = os.path.join(self.testdir, "dest")
        client.download(url, dest)

        self.assertTrue(os.path.isfile(os.path.join(dest, "data.txt")))
        with open(os.path.join(dest, "data.txt")) as f:
            self.assertEqual(f.read(), "roundtrip-data")

    def test_upload_idempotent(self):
        """Uploading the same key twice must not raise."""
        client = self._make_client()
        src = self._make_src_dir("idem", "v1-content")
        key = "ivpm-pkg-TestOS-idem-v1"
        self.assertTrue(client.upload(key, src))
        src2 = self._make_src_dir("idem2", "v2-content")
        # Second upload should be treated as success (409 → idempotent)
        self.assertTrue(client.upload(key, src2))


# ---------------------------------------------------------------------------
# L3-B: GHACacheBackend against the real server
# ---------------------------------------------------------------------------

class TestGHACacheBackendIntegration(GHAIntegrationBase):
    """GHACacheBackend integration tests against the Docker cache server."""

    def _make_backend(self):
        from ivpm.cache_backend.gha import GHACacheBackend
        from ivpm.cache_backend.gha_client import GHACacheClient
        local_dir = os.path.join(self.testdir, "local_cache")
        os.makedirs(local_dir, exist_ok=True)
        client = GHACacheClient(
            cache_url=self._cache_url,
            token=_FAKE_TOKEN,
            key_prefix="ivpm",
            os_name="TestOS",
        )
        return GHACacheBackend(local_dir=local_dir, client=client)

    def _make_pkg_dir(self, name: str = "mypkg", content: str = "pkg-data") -> str:
        src = os.path.join(self.testdir, f"src_{name}")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "pkg.txt"), "w") as f:
            f.write(content)
        return src

    def test_store_and_has_version(self):
        backend = self._make_backend()
        src = self._make_pkg_dir("store")
        backend.store_version("mypkg", "abc123", src)
        backend.deactivate(success=True)
        self.assertTrue(backend.has_version("mypkg", "abc123"))

    def test_l2_restore_after_l1_eviction(self):
        """Populate L2, evict L1, then verify L2 restore repopulates L1."""
        backend = self._make_backend()
        src = self._make_pkg_dir("evict", "important-data")
        backend.store_version("evict_pkg", "v1", src)
        backend.deactivate(success=True)

        # Remove the L1 entry to force L2 restore
        from ivpm.cache_backend.filesystem import FilesystemCacheBackend
        from .test_base import _force_rmtree
        local_dir = backend._local_dir
        pkg_dir = os.path.join(local_dir, "evict_pkg")
        if os.path.isdir(pkg_dir):
            _force_rmtree(pkg_dir)

        # Fresh backend using the same local cache dir and same server
        from ivpm.cache_backend.gha import GHACacheBackend
        from ivpm.cache_backend.gha_client import GHACacheClient
        client2 = GHACacheClient(
            cache_url=self._cache_url,
            token=_FAKE_TOKEN,
            key_prefix="ivpm",
            os_name="TestOS",
        )
        backend2 = GHACacheBackend(local_dir=local_dir, client=client2)
        # This must hit L2 and restore into L1
        self.assertTrue(backend2.has_version("evict_pkg", "v1"))
        # And L1 must now have the file
        restored = os.path.join(local_dir, "evict_pkg", "v1")
        self.assertTrue(os.path.isdir(restored))
        self.assertTrue(os.path.isfile(os.path.join(restored, "pkg.txt")))

    def test_miss_returns_false(self):
        backend = self._make_backend()
        self.assertFalse(backend.has_version("nonexistent_pkg", "v999"))

    def test_deactivate_waits_for_uploads(self):
        """deactivate(success=True) must join all pending uploads without error."""
        backend = self._make_backend()
        for i in range(3):
            src = self._make_pkg_dir(f"multi{i}", f"data-{i}")
            backend.store_version(f"multipkg{i}", f"v{i}", src)
        # Should complete without raising
        backend.deactivate(success=True)
        # All uploads should now be visible via lookup
        from ivpm.cache_backend.gha_client import GHACacheClient
        client = GHACacheClient(
            cache_url=self._cache_url,
            token=_FAKE_TOKEN,
            key_prefix="ivpm",
            os_name="TestOS",
        )
        for i in range(3):
            key = client.pkg_key(f"multipkg{i}", f"v{i}")
            self.assertIsNotNone(client.lookup(key))



# ---------------------------------------------------------------------------
# Layer 4: tests against the REAL GitHub Actions cache service
# ---------------------------------------------------------------------------

_GHA_CACHE_URL = os.environ.get("ACTIONS_CACHE_URL", "")
_GHA_TOKEN = os.environ.get("ACTIONS_RUNTIME_TOKEN", "")
_GHA_RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
_GHA_RUN_ATTEMPT = os.environ.get("GITHUB_RUN_ATTEMPT", "1")

_REAL_GHA_SKIP_REASON = (
    "Not running inside GitHub Actions (ACTIONS_CACHE_URL / "
    "ACTIONS_RUNTIME_TOKEN not set)."
)


@unittest.skipUnless(_GHA_CACHE_URL and _GHA_TOKEN, _REAL_GHA_SKIP_REASON)
class TestGHACacheReal(TestBase):
    """Tests against the live GitHub Actions cache service.

    These tests run automatically when the workflow executes on a GHA
    runner (ACTIONS_CACHE_URL and ACTIONS_RUNTIME_TOKEN are present).
    They are skipped silently in all other environments.

    Cache keys include the run ID and attempt number so parallel runs and
    re-runs never collide on the same key.
    """

    # Unique prefix per run so retries and concurrent jobs don't conflict
    _KEY_PREFIX = f"ivpm-test-{_GHA_RUN_ID}-{_GHA_RUN_ATTEMPT}"

    def _make_client(self):
        from ivpm.cache_backend.gha_client import GHACacheClient
        return GHACacheClient(
            cache_url=_GHA_CACHE_URL,
            token=_GHA_TOKEN,
            key_prefix=self._KEY_PREFIX,
            os_name=os.environ.get("RUNNER_OS", "Linux"),
        )

    def _make_src_dir(self, name: str = "pkg", content: str = "hello") -> str:
        src = os.path.join(self.testdir, f"src_{name}")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "data.txt"), "w") as f:
            f.write(content)
        return src

    # --- GHACacheClient against real service ---

    def test_real_lookup_miss(self):
        """A key that was never uploaded must return None."""
        client = self._make_client()
        result = client.lookup(client.pkg_key("no-such-pkg", "v0"))
        self.assertIsNone(result)

    def test_real_upload_and_lookup(self):
        """Upload a key and immediately look it up."""
        client = self._make_client()
        src = self._make_src_dir("real-up", "real-upload")
        key = client.pkg_key("real-upload-pkg", "v1")
        ok = client.upload(key, src)
        self.assertTrue(ok, "upload should succeed against real GHA service")
        url = client.lookup(key)
        self.assertIsNotNone(url, "lookup should find recently uploaded key")

    def test_real_roundtrip(self):
        """Upload a directory, download it, verify contents."""
        client = self._make_client()
        src = self._make_src_dir("real-rt", "roundtrip-content")
        key = client.pkg_key("real-roundtrip-pkg", "v1")
        self.assertTrue(client.upload(key, src))

        url = client.lookup(key)
        self.assertIsNotNone(url)

        dest = os.path.join(self.testdir, "real-dest")
        client.download(url, dest)
        fpath = os.path.join(dest, "data.txt")
        self.assertTrue(os.path.isfile(fpath))
        with open(fpath) as f:
            self.assertEqual(f.read(), "roundtrip-content")

    # --- GHACacheBackend L2 restore against real service ---

    def test_real_backend_l2_restore(self):
        """Store via backend, evict L1, verify L2 restores into L1."""
        from ivpm.cache_backend.gha import GHACacheBackend
        from .test_base import _force_rmtree

        local_dir = os.path.join(self.testdir, "l1")
        os.makedirs(local_dir)
        backend = GHACacheBackend(
            local_dir=local_dir,
            key_prefix=self._KEY_PREFIX,
        )

        # Build a small source package
        src = self._make_src_dir("real-bk", "backend-data")
        backend.store_version("real-bk-pkg", "v1", src)
        backend.deactivate(success=True)

        # Evict L1
        pkg_dir = os.path.join(local_dir, "real-bk-pkg")
        if os.path.isdir(pkg_dir):
            _force_rmtree(pkg_dir)

        # Fresh backend — must restore from L2
        backend2 = GHACacheBackend(
            local_dir=local_dir,
            key_prefix=self._KEY_PREFIX,
        )
        self.assertTrue(
            backend2.has_version("real-bk-pkg", "v1"),
            "GHA L2 should restore the package after L1 eviction",
        )
        restored = os.path.join(local_dir, "real-bk-pkg", "v1")
        self.assertTrue(os.path.isfile(os.path.join(restored, "data.txt")))


if __name__ == "__main__":
    unittest.main()

