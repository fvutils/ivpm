#****************************************************************************
#* test_remote_fetch.py
#*
#* Tests for ivpm.remote.fetch_manifest (the --from fetch helper) and the
#* reader's allow_include guard. All HTTP is mocked — these tests do not touch
#* the network.
#****************************************************************************
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_UNIT_DIR))
_SRC_DIR = os.path.join(_ROOT_DIR, "src")
sys.path.insert(0, _SRC_DIR)

from ivpm.remote import fetch_manifest, FetchedManifest
from ivpm.yamlsrc import SrcLoaderError
from ivpm.ivpm_yaml_reader import IvpmYamlReader


_MANIFEST = (
    "package:\n"
    "  name: acme-tools\n"
    "  description: remote catalog\n"
    "  dep-sets:\n"
    "    - name: default\n"
    "      deps:\n"
    "        - name: pyyaml\n"
    "          src: pypi\n"
)


@contextmanager
def _mock_urlopen(body=b"", status=200, raise_exc=None):
    """Patch urllib.request.urlopen to return a fake response (or raise)."""
    def _fake(url, *a, **kw):
        if raise_exc is not None:
            raise raise_exc
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp
    with mock.patch("urllib.request.urlopen", _fake):
        yield


class TestFetchManifestLocal(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "ivpm.yaml")
        with open(self.path, "w") as f:
            f.write(_MANIFEST)

    def tearDown(self):
        self._tmp.cleanup()

    def test_bare_path_resolves(self):
        fetched = fetch_manifest(self.path)
        self.assertFalse(fetched.is_remote)
        self.assertEqual(fetched.local_path, self.path)
        self.assertEqual(fetched.origin, self.path)
        # cleanup() must not remove a local (non-temp) file
        fetched.cleanup()
        self.assertTrue(os.path.isfile(self.path))

    def test_file_url_resolves(self):
        url = "file://" + self.path
        fetched = fetch_manifest(url)
        self.assertFalse(fetched.is_remote)
        self.assertEqual(fetched.local_path, self.path)

    def test_directory_resolves_to_ivpm_yaml(self):
        # A directory is treated as a location containing ivpm.yaml.
        fetched = fetch_manifest(self.dir)
        self.assertFalse(fetched.is_remote)
        self.assertEqual(fetched.local_path, self.path)
        self.assertEqual(fetched.origin, self.path)

    def test_directory_without_manifest_is_rejected(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        with self.assertRaises(SrcLoaderError):
            fetch_manifest(empty.name)

    def test_missing_path_is_rejected(self):
        with self.assertRaises(SrcLoaderError):
            fetch_manifest(os.path.join(self.dir, "nope.yaml"))


class TestFetchManifestRemote(unittest.TestCase):

    def test_http_writes_tempfile_and_cleanup(self):
        with _mock_urlopen(body=_MANIFEST.encode()):
            fetched = fetch_manifest("https://example.com/ivpm.yaml")
        self.assertTrue(fetched.is_remote)
        self.assertEqual(fetched.origin, "https://example.com/ivpm.yaml")
        self.assertTrue(os.path.isfile(fetched.local_path))
        with open(fetched.local_path) as f:
            self.assertIn("acme-tools", f.read())
        tmp = fetched.local_path
        fetched.cleanup()
        self.assertFalse(os.path.isfile(tmp))
        # idempotent
        fetched.cleanup()

    def test_http_bare_url_appends_ivpm_yaml(self):
        seen = {}

        def _fake(url, *a, **kw):
            seen["url"] = url
            resp = mock.MagicMock()
            resp.status = 200
            resp.read.return_value = _MANIFEST.encode()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            return resp

        with mock.patch("urllib.request.urlopen", _fake):
            fetched = fetch_manifest("https://edapack.github.io")
        self.assertEqual(seen["url"], "https://edapack.github.io/ivpm.yaml")
        self.assertTrue(fetched.is_remote)
        self.assertEqual(fetched.origin, "https://edapack.github.io/ivpm.yaml")

    def test_http_trailing_slash_appends_ivpm_yaml(self):
        seen = {}

        def _fake(url, *a, **kw):
            seen["url"] = url
            resp = mock.MagicMock()
            resp.status = 200
            resp.read.return_value = _MANIFEST.encode()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            return resp

        with mock.patch("urllib.request.urlopen", _fake):
            fetch_manifest("https://edapack.github.io/sub/")
        self.assertEqual(seen["url"], "https://edapack.github.io/sub/ivpm.yaml")

    def test_http_non_200_is_fatal(self):
        with _mock_urlopen(status=404):
            with self.assertRaises(SrcLoaderError):
                fetch_manifest("https://example.com/missing.yaml")

    def test_http_oserror_is_fatal(self):
        with _mock_urlopen(raise_exc=OSError("connection refused")):
            with self.assertRaises(SrcLoaderError):
                fetch_manifest("https://example.com/ivpm.yaml")


class TestReaderAllowInclude(unittest.TestCase):
    """The allow_include guard used for remote manifests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _read(self, content, allow_include=True):
        path = os.path.join(self.dir, "ivpm.yaml")
        with open(path, "w") as f:
            f.write(content)
        with open(path) as fp:
            return IvpmYamlReader().read(fp, path, allow_include=allow_include)

    def test_include_rejected_when_disallowed(self):
        content = (
            "package:\n"
            "  name: demo\n"
            "  include: [other.yaml]\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps: []\n"
        )
        with self.assertRaises(SrcLoaderError) as ctx:
            self._read(content, allow_include=False)
        self.assertIn("Remote manifests may not use 'include:'", str(ctx.exception))

    def test_no_include_ok_when_disallowed(self):
        """A remote manifest without include parses fine under allow_include=False."""
        proj = self._read(_MANIFEST, allow_include=False)
        self.assertEqual(proj.name, "acme-tools")
        self.assertEqual(proj.description, "remote catalog")


if __name__ == "__main__":
    unittest.main()
