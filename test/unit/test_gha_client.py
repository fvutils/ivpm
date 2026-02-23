"""
Layer 2 tests for GHACacheClient against an in-process mock HTTP server.

The mock server implements the subset of the GHA cache REST protocol that
GHACacheClient uses:
  GET  /_apis/artifactcache/cache?keys=K&version=V  – lookup
  POST /_apis/artifactcache/caches                  – reserve → {cacheId}
  PATCH /_apis/artifactcache/caches/{id}            – upload chunk
  POST  /_apis/artifactcache/caches/{id}            – commit

The server runs in a daemon thread; each test class gets its own instance
on a free OS-assigned port.
"""
import io
import json
import os
import shutil
import tarfile
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional

from .test_base import TestBase


# ---------------------------------------------------------------------------
# Minimal GHA cache protocol server
# ---------------------------------------------------------------------------

class _GHACacheStore:
    """Shared state for the mock server across requests."""
    def __init__(self):
        self.entries: Dict[str, bytes] = {}          # key → committed archive bytes
        self.reserved: Dict[int, dict] = {}          # cacheId → {key, chunks}
        self._next_id = 1
        self.lock = threading.Lock()

    def reserve(self, key: str) -> int:
        with self.lock:
            cid = self._next_id
            self._next_id += 1
            self.reserved[cid] = {"key": key, "data": io.BytesIO()}
            return cid

    def append_chunk(self, cache_id: int, data: bytes) -> bool:
        with self.lock:
            if cache_id not in self.reserved:
                return False
            self.reserved[cache_id]["data"].write(data)
            return True

    def commit(self, cache_id: int) -> bool:
        with self.lock:
            if cache_id not in self.reserved:
                return False
            entry = self.reserved.pop(cache_id)
            self.entries[entry["key"]] = entry["data"].getvalue()
            return True

    def lookup(self, keys: str) -> Optional[str]:
        """Return a pseudo-URL for the first matching key, or None."""
        with self.lock:
            for k in keys.split(","):
                k = k.strip()
                if k in self.entries:
                    return f"__store__:{k}"
            return None

    def get_archive(self, key: str) -> Optional[bytes]:
        with self.lock:
            return self.entries.get(key)


class _GHAHandler(BaseHTTPRequestHandler):
    store: _GHACacheStore  # set on the class before the server starts

    def log_message(self, fmt, *args):
        pass  # suppress server log output during tests

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/_apis/artifactcache/cache":
            params = urllib.parse.parse_qs(parsed.query)
            keys = params.get("keys", [""])[0]
            url = self.store.lookup(keys)
            if url is None:
                self._send_json(204, None)
            else:
                self._send_json(200, {"archiveLocation": url})
        elif parsed.path.startswith("/_download/"):
            # Serve the stored archive
            key = urllib.parse.unquote(parsed.path[len("/_download/"):])
            data = self.store.get_archive(key)
            if data is None:
                self._send(404, b"Not found")
            else:
                self._send(200, data, content_type="application/octet-stream")
        else:
            self._send(404, b"Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        body = self._read_body()
        if parsed.path == "/_apis/artifactcache/caches":
            data = json.loads(body)
            key = data.get("key", "")
            if key in self.store.entries:
                self._send(409, b"Conflict")
                return
            cache_id = self.store.reserve(key)
            self._send_json(201, {"cacheId": cache_id})
        elif parsed.path.startswith("/_apis/artifactcache/caches/"):
            try:
                cache_id = int(parsed.path.split("/")[-1])
            except ValueError:
                self._send(400, b"Bad id")
                return
            ok = self.store.commit(cache_id)
            if ok:
                self._send(204, b"")
            else:
                self._send(400, b"Unknown cache id")
        else:
            self._send(404, b"Not found")

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/_apis/artifactcache/caches/"):
            try:
                cache_id = int(parsed.path.split("/")[-1])
            except ValueError:
                self._send(400, b"Bad id")
                return
            data = self._read_body()
            ok = self.store.append_chunk(cache_id, data)
            if ok:
                self._send(204, b"")
            else:
                self._send(400, b"Unknown cache id")
        else:
            self._send(404, b"Not found")

    # helpers

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, code: int, data) -> None:
        if data is None:
            self._send(code, b"")
        else:
            body = json.dumps(data).encode()
            self._send(code, body, content_type="application/json")

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def _make_server(store: _GHACacheStore):
    """Create an HTTPServer on a random free port using the given store."""
    handler_cls = type("Handler", (_GHAHandler,), {"store": store})
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    return server


# ---------------------------------------------------------------------------
# Override GHACacheClient.download to handle __store__: URLs
# ---------------------------------------------------------------------------

def _patched_download(self, download_url: str, dest_dir: str) -> None:
    """Replace archive download with a store lookup for test URLs."""
    if download_url.startswith("__store__:"):
        key = download_url[len("__store__:"):]
        data = _active_store.get_archive(key)
        if data is None:
            raise FileNotFoundError(f"No archive for key {key!r}")
        import io, tarfile, os
        os.makedirs(dest_dir, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(dest_dir)
    else:
        # Fall through to real HTTP download
        import urllib.request
        import tarfile as _tf
        req = urllib.request.Request(download_url)
        with urllib.request.urlopen(req) as resp:
            with _tf.open(fileobj=resp, mode="r|gz") as tf:
                tf.extractall(dest_dir)


_active_store: Optional[_GHACacheStore] = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGHACacheClient(TestBase):

    @classmethod
    def setUpClass(cls):
        global _active_store
        cls._store = _GHACacheStore()
        _active_store = cls._store
        cls._server = _make_server(cls._store)
        port = cls._server.server_address[1]
        cls._base_url = f"http://127.0.0.1:{port}/"
        cls._thread = threading.Thread(target=cls._server.serve_forever)
        cls._thread.daemon = True
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()

    def setUp(self):
        super().setUp()
        # Clear store between tests
        self._store.entries.clear()
        self._store.reserved.clear()
        # Patch download to use __store__: URLs
        from ivpm.cache_backend import gha_client
        self._orig_download = gha_client.GHACacheClient.download
        gha_client.GHACacheClient.download = _patched_download

    def tearDown(self):
        from ivpm.cache_backend import gha_client
        gha_client.GHACacheClient.download = self._orig_download
        super().tearDown()

    def _make_client(self):
        from ivpm.cache_backend.gha_client import GHACacheClient
        return GHACacheClient(
            cache_url=self._base_url,
            token="test-token",
            key_prefix="ivpm",
            os_name="TestOS",
        )

    def _make_src_dir(self, name="pkg"):
        src = os.path.join(self.testdir, f"src_{name}")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "data.txt"), "w") as f:
            f.write(f"content of {name}")
        return src

    # --- lookup ---

    def test_lookup_miss(self):
        client = self._make_client()
        result = client.lookup("ivpm-pkg-TestOS-mypkg-abc123")
        self.assertIsNone(result)

    def test_lookup_hit_after_upload(self):
        client = self._make_client()
        src = self._make_src_dir()
        key = "ivpm-pkg-TestOS-mypkg-abc123"
        ok = client.upload(key, src)
        self.assertTrue(ok)
        result = client.lookup(key)
        self.assertIsNotNone(result)

    # --- upload ---

    def test_upload_creates_entry(self):
        client = self._make_client()
        src = self._make_src_dir()
        key = "ivpm-pkg-TestOS-mypkg-v1"
        ok = client.upload(key, src)
        self.assertTrue(ok)
        self.assertIn(key, self._store.entries)

    def test_upload_existing_key_idempotent(self):
        """Uploading the same key twice should succeed (409 treated as success)."""
        client = self._make_client()
        src1 = self._make_src_dir("p1")
        src2 = self._make_src_dir("p2")
        key = "ivpm-pkg-TestOS-mypkg-v1"
        self.assertTrue(client.upload(key, src1))
        self.assertTrue(client.upload(key, src2))  # 409 → treated as success

    # --- roundtrip ---

    def test_roundtrip_upload_download(self):
        client = self._make_client()
        src = self._make_src_dir()
        key = "ivpm-pkg-TestOS-roundtrip-v1"
        client.upload(key, src)

        url = client.lookup(key)
        self.assertIsNotNone(url)

        dest = os.path.join(self.testdir, "dest")
        client.download(url, dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, "data.txt")))
        with open(os.path.join(dest, "data.txt")) as f:
            self.assertEqual(f.read(), "content of pkg")

    def test_roundtrip_large_upload(self):
        """Test multi-chunk upload with a >32 MB payload."""
        src = self._make_src_dir("large")
        big_file = os.path.join(src, "big.bin")
        # Write 35 MB of data to force chunking
        with open(big_file, "wb") as f:
            f.write(b"\xAB" * (35 * 1024 * 1024))

        client = self._make_client()
        key = "ivpm-pkg-TestOS-large-v1"
        ok = client.upload(key, src)
        self.assertTrue(ok)

        url = client.lookup(key)
        dest = os.path.join(self.testdir, "dest_large")
        client.download(url, dest)
        self.assertEqual(
            os.path.getsize(os.path.join(dest, "big.bin")),
            35 * 1024 * 1024,
        )

    # --- key construction ---

    def test_pkg_key_format(self):
        client = self._make_client()
        self.assertEqual(
            client.pkg_key("mylib", "deadbeef"),
            "ivpm-pkg-TestOS-mylib-deadbeef",
        )

    def test_venv_key_format(self):
        client = self._make_client()
        self.assertEqual(
            client.venv_key("3.11", "abc"),
            "ivpm-pyenv-TestOS-3.11-abc",
        )

    def test_pip_key_format(self):
        client = self._make_client()
        self.assertEqual(
            client.pip_key("abc"),
            "ivpm-pip-TestOS-abc",
        )


if __name__ == "__main__":
    unittest.main()
