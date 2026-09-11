"""
How a package update failure that never reached the diagnostics layer reads.

An exception raised deep in a fetch path (an OSError from an extractor, a
subprocess, a cache link) is caught by the batch driver and turned into one
line.  Rendering it with ``str(exc)`` alone loses everything the user needs:
an OSError raised without a filename prints as a bare
"[Errno 2] No such file or directory" -- no operation, no path, and no hint
which part of ivpm was running.
"""
import os
import unittest

from ivpm.utils import describe_exception
from ivpm.package_updater import _failure_message
from ivpm.diagnostics import SrcLoaderError


class _Pkg(object):
    def __init__(self, name="verilator"):
        self.name = name
        self.src_type = "gh-rls"
        self.url = "https://github.com/edapack/verilator-bin"
        self.srcinfo = None


class TestDescribeException(unittest.TestCase):

    def test_names_the_exception_type(self):
        msg = describe_exception(FileNotFoundError(2, "No such file or directory"))
        self.assertIn("FileNotFoundError", msg)
        self.assertIn("No such file or directory", msg)

    def test_empty_message_still_identifies_the_failure(self):
        self.assertEqual("RuntimeError", describe_exception(RuntimeError()))

    def test_oserror_filename_is_added(self):
        exc = FileNotFoundError(2, "No such file or directory", "/tmp/nope.tgz")
        msg = describe_exception(exc)
        self.assertIn("/tmp/nope.tgz", msg)

    def test_filename_not_repeated_when_str_has_it(self):
        exc = FileNotFoundError(2, "No such file or directory", "/tmp/nope.tgz")
        self.assertEqual(1, describe_exception(exc).count("/tmp/nope.tgz"))

    def test_ivpm_frame_is_reported(self):
        """The raising ivpm file:line -- the thing a bare OSError never says."""
        from ivpm.pkg_types.package_file import PackageFile

        pkg = PackageFile("p")
        pkg.url = "http://example.com/x.tar.gz"
        try:
            pkg._install_tgz("/definitely/not/here.tar.gz", "/tmp")
        except Exception as e:
            msg = describe_exception(e)
        self.assertIn("raised at", msg)
        self.assertIn("package_file.py", msg)

    def test_non_ivpm_frames_are_not_reported(self):
        try:
            open("/definitely/not/here")
        except Exception as e:
            msg = describe_exception(e)
        self.assertNotIn("raised at", msg)


class TestFailureMessage(unittest.TestCase):

    def test_unreported_exception_is_described_in_full(self):
        msg = _failure_message(_Pkg(), FileNotFoundError(2, "No such file or directory"))
        self.assertIn("Failed to update package verilator", msg)
        self.assertIn("FileNotFoundError", msg)
        # the source spec still identifies the dependency entry
        self.assertIn("gh-rls", msg)
        self.assertIn("verilator-bin", msg)

    def test_already_reported_exception_is_not_re_described(self):
        """A fatal() failure was rendered in full where it was hit."""
        exc = SrcLoaderError("boom", ["a-diagnostic"])
        msg = _failure_message(_Pkg(), exc)
        self.assertNotIn("boom", msg)
        self.assertIn("Failed to update package verilator", msg)


if __name__ == "__main__":
    unittest.main()
