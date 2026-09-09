"""
Unit tests for source-type resolution on the URL/file/http family.

``Package.src_type`` used to carry two different things: the source-type
*spec* the user wrote (``file``, ``http``, ``gh-rls``) and the archive
*extension* the unpacker keys on (``.tar.gz``). Whichever one was written
last won, and the loser's consumer broke silently:

* ``src: http`` and ``src: file`` stored a spec, so the unpacker refused it
  with "unsupported archive type 'http'".
* the auto-detected form stored an extension, so the lock file's typed
  branches never matched and a downloaded archive kept no ``url``/``etag``.
* ``gh-rls`` overwrote its own spec mid-update, dropping the release tag and
  resolved version out of the lock.

``archive_ext`` now carries the extension and ``src_type`` the spec.
Separately, ``src: url`` resolved to ``PackageURL`` -- the abstract base of
the others, whose inherited ``update()`` is a no-op -- so it reported success
and fetched nothing.
"""
import os
import tarfile
import tempfile
import unittest

from ivpm.package_lock import _lock_src
from ivpm.pkg_types.package_dir import PackageDir
from ivpm.pkg_types.package_file import PackageFile
from ivpm.pkg_types.package_gh_rls import PackageGhRls
from ivpm.pkg_types.package_http import PackageHttp
from ivpm.pkg_types.package_url import PackageURL
from ivpm.yamlsrc import SrcLoaderError


class TestExtFromUrl(unittest.TestCase):

    def test_two_part_extensions(self):
        for url, ext in (
                ("https://x/a.tar.gz", ".tar.gz"),
                ("https://x/a.tar.xz", ".tar.xz"),
                ("https://x/a.tar.bz2", ".tar.bz2"),
                ("https://x/a.tgz", ".tar.gz"),
                ("https://x/a.zip", ".zip"),
                ("https://x/a.jar", ".jar")):
            self.assertEqual(ext, PackageFile.ext_from_url(url), url)

    def test_no_extension(self):
        self.assertEqual("", PackageFile.ext_from_url("https://github.com/org/tool"))

    def test_bare_compression_suffix_does_not_eat_the_host(self):
        # A lone ".gz" with no inner dot must not walk backwards off the name.
        self.assertEqual(".gz", PackageFile.ext_from_url("a.gz"))


class TestSpecAndExtensionAreSeparate(unittest.TestCase):

    def _mk(self, cls, opts):
        pkg = cls("p")
        pkg.process_options(opts, None)
        return pkg

    def test_explicit_http_keeps_the_extension_for_unpacking(self):
        pkg = self._mk(PackageHttp, {"src": "http", "url": "https://x/a.tar.xz"})
        self.assertEqual("http", pkg.src_type)
        self.assertEqual(".tar.xz", pkg.archive_ext)

    def test_explicit_file_keeps_the_extension_for_unpacking(self):
        pkg = self._mk(PackageFile, {"src": "file", "url": "file:///t/a.tar.gz"})
        self.assertEqual("file", pkg.src_type)
        self.assertEqual(".tar.gz", pkg.archive_ext)

    def test_auto_detected_form_is_unchanged(self):
        # No 'src': the extension doubles as the source type, exactly as
        # before, so existing locks and status output do not move.
        pkg = self._mk(PackageHttp, {"url": "https://x/a.tar.gz"})
        self.assertEqual(".tar.gz", pkg.src_type)
        self.assertEqual(".tar.gz", pkg.archive_ext)

    def test_extension_valued_src_forces_the_extension(self):
        # The escape hatch for a URL whose own extension is absent or wrong.
        pkg = self._mk(PackageHttp, {"src": ".tar.gz", "url": "https://x/download?id=7"})
        self.assertEqual(".tar.gz", pkg.archive_ext)

    def test_jar_still_defaults_to_no_unpack(self):
        self.assertFalse(self._mk(PackageFile, {"url": "https://x/a.jar"}).unpack)
        self.assertTrue(self._mk(PackageFile, {"url": "https://x/a.zip"}).unpack)

    def test_explicit_src_jar_also_defaults_to_no_unpack(self):
        # Previously src_type was "http" here, so the .jar check missed and a
        # jar was unpacked instead of kept whole.
        pkg = self._mk(PackageHttp, {"src": "http", "url": "https://x/a.jar"})
        self.assertFalse(pkg.unpack)


class TestResolveArchiveExt(unittest.TestCase):
    """Not every package is built through the reader, so the extension has to
    be resolvable without ``process_options`` having run."""

    def test_uses_archive_ext_when_set(self):
        pkg = PackageHttp("p")
        pkg.process_options({"src": "http", "url": "https://x/a.tar.xz"}, None)
        self.assertEqual(".tar.xz", pkg.resolve_archive_ext())

    def test_falls_back_to_an_extension_valued_src_type(self):
        # Direct construction, no URL: the old overloaded spelling still works.
        pkg = PackageFile("p")
        pkg.src_type = ".tar.gz"
        self.assertEqual(".tar.gz", pkg.resolve_archive_ext())

    def test_tgz_src_type_normalizes(self):
        pkg = PackageFile("p")
        pkg.src_type = ".tgz"
        self.assertEqual(".tar.gz", pkg.resolve_archive_ext())

    def test_lock_reconstructed_package_resolves_from_its_url(self):
        # IvpmLockReader assigns src_type = "tgz" (a spec, not an extension)
        # and never runs process_options. Re-reading the URL is what lets a
        # reproduced workspace actually unpack.
        pkg = PackageHttp("p")
        pkg.url = "https://x/a.tar.gz"
        pkg.src_type = "tgz"
        self.assertEqual(".tar.gz", pkg.resolve_archive_ext())

    def test_unresolvable_reports_none_detected_not_a_crash(self):
        # A package with neither extension nor URL must produce the diagnostic,
        # not an AttributeError from formatting its (absent) source location.
        pkg = PackageFile("p")
        pkg.src_type = "http"
        with self.assertRaises(Exception) as ctx:
            pkg._install("/nonexistent", "/tmp/nowhere")
        self.assertIn("unsupported archive type", str(ctx.exception))


class TestGhRlsKeepsItsSpec(unittest.TestCase):

    def test_determine_src_type_does_not_clobber_the_spec(self):
        pkg = PackageGhRls("tool")
        pkg.process_options({"src": "gh-rls", "url": "https://github.com/org/tool"}, None)
        self.assertEqual("gh-rls", pkg.src_type)

        pkg._determine_src_type("https://x/tool-linux-x86_64.tar.gz", None)
        self.assertEqual(".tar.gz", pkg.archive_ext)
        # The spec must survive: the lock's gh-rls branch keys on it, and it
        # is what records the release tag and resolved version.
        self.assertEqual("gh-rls", pkg.src_type)
        self.assertEqual("gh-rls", _lock_src(pkg))

    def test_forced_ext_is_honoured(self):
        pkg = PackageGhRls("tool")
        pkg.process_options({"src": "gh-rls", "url": "https://github.com/org/tool"}, None)
        pkg._determine_src_type("https://x/tool-linux", ".zip")
        self.assertEqual(".zip", pkg.archive_ext)
        self.assertEqual("gh-rls", pkg.src_type)


class TestSrcUrlDispatch(unittest.TestCase):
    """``src: url`` must resolve to the type the reader would have
    auto-detected. Returning the abstract PackageURL made it a silent no-op:
    ``ivpm update`` reported success and created no package directory."""

    def _create(self, url):
        return PackageURL.create("p", {"src": "url", "url": url}, None)

    def test_https_resolves_to_http(self):
        pkg = self._create("https://x/a.tar.gz")
        self.assertIsInstance(pkg, PackageHttp)
        # Not the literal "url": the lock must record a reproducible type.
        self.assertEqual(".tar.gz", pkg.src_type)
        self.assertEqual("tgz", _lock_src(pkg))

    def test_it_is_not_the_abstract_base(self):
        pkg = self._create("https://x/a.tar.gz")
        self.assertIsNot(type(pkg), PackageURL)
        # The bug in one assertion: the base class has no update() of its own.
        self.assertIsNot(type(pkg).update, PackageURL.update)

    def test_file_archive_resolves_to_file(self):
        pkg = self._create("file:///t/a.tar.gz")
        self.assertIsInstance(pkg, PackageFile)
        self.assertEqual(".tar.gz", pkg.archive_ext)

    def test_file_directory_resolves_to_dir(self):
        pkg = self._create("file:///t/somedir")
        self.assertIsInstance(pkg, PackageDir)
        self.assertEqual("dir", pkg.src_type)

    def test_git_url_is_rejected_with_a_pointer(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._create("https://github.com/org/repo.git")
        self.assertIn("src: git", str(ctx.exception))

    def test_unknown_scheme_is_rejected(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._create("ftp://example.com/a.tar.gz")
        self.assertIn("cannot determine how to fetch", str(ctx.exception))

    def test_missing_url_is_rejected(self):
        with self.assertRaises(SrcLoaderError):
            PackageURL.create("p", {"src": "url"}, None)


class TestFileUrlIsUnpacked(unittest.TestCase):
    """A local archive may be named with a file:// URL; the extractors take a
    path, so the prefix has to come off or tarfile raises ENOENT on the
    literal string 'file:///...'."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ivpm-srctype-")
        inner = os.path.join(self._dir, "top", "sub")
        os.makedirs(inner)
        with open(os.path.join(inner, "f.txt"), "w") as fp:
            fp.write("hi\n")
        self.archive = os.path.join(self._dir, "a.tar.gz")
        with tarfile.open(self.archive, "w:gz") as tf:
            tf.add(os.path.join(self._dir, "top"), arcname="top")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def _install_from(self, url):
        pkg = PackageFile("p")
        pkg.process_options({"src": "file", "url": url}, None)
        dest = os.path.join(self._dir, "out")
        pkg._install(pkg.url, dest)
        return dest

    def test_file_url(self):
        dest = self._install_from("file://" + self.archive)
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "f.txt")))

    def test_plain_path_still_works(self):
        dest = self._install_from(self.archive)
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "f.txt")))


if __name__ == "__main__":
    unittest.main()
