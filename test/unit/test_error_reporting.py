#****************************************************************************
#* test_error_reporting.py
#*
#* How a failing update is *reported*, as opposed to what makes it fail.
#*
#* A fetch failure passes through two reporting layers: the code that hits it
#* (which renders the details -- git's output, the auth hint) and the batch
#* driver (which adds the dependency entry and its file:line). Both must fire,
#* and neither may repeat the other's content: while a TUI is deferring errors
#* the two land adjacent at the very bottom of the run, which is exactly where
#* the reader is looking.
#****************************************************************************
import os
import unittest

from ivpm import msg
from ivpm.diagnostics import (
    CollectingSink, DiagnosticReporter, Severity, SrcLoaderError,
)

from .test_base import TestBase


class TestFetchFailureIsReportedOnce(TestBase):

    def setUp(self):
        super().setUp()
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)
        return super().tearDown()

    def _errors(self):
        return [d for d in self._sink.records if d.severity >= Severity.ERROR]

    def _write_bad_git_dep(self):
        self.mkFile("ivpm.yaml",
            "package:\n"
            "    name: err_root\n"
            "    dep-sets:\n"
            "        - name: default-dev\n"
            "          deps:\n"
            "                    - name: dep1\n"
            "                      url: file://%s\n"
            "                      src: git\n"
            "                      branch: main\n" % (
                os.path.join(self.testdir, "no-such-repo"),))

    def test_failure_body_is_not_printed_twice(self):
        """The details render once; the wrapper only adds the dependency."""
        self._write_bad_git_dep()

        with self.assertRaises(SrcLoaderError) as ctx:
            self.ivpm_update(skip_venv=True)

        # The exception still carries the reason, even though it is only
        # rendered once: callers that catch it read str(e).
        self.assertIn("no-such-repo", str(ctx.exception))

        errors = self._errors()
        self.assertEqual(len(errors), 2, "\n---\n".join(d.format() for d in errors))

        detail, wrapper = errors[0].message, errors[1].message

        # The first report is the one that owns the details.
        self.assertIn("no-such-repo", detail)

        # The second names the dependency and its source spec, and does NOT
        # re-quote the first. Comparing on the detail's first line keeps this
        # from depending on how git happens to word the failure.
        self.assertIn("dep1", wrapper)
        self.assertIn("src: git", wrapper)
        self.assertNotIn(detail.splitlines()[0], wrapper)

    def test_wrapper_still_locates_the_dependency(self):
        """Dropping the body must not drop the file:line the user needs."""
        self._write_bad_git_dep()

        with self.assertRaises(SrcLoaderError):
            self.ivpm_update(skip_venv=True)

        wrapper = self._errors()[1]
        self.assertIsNotNone(wrapper.srcinfo)
        self.assertTrue(wrapper.format().startswith(str(wrapper.srcinfo)),
                        wrapper.format())

    def test_a_non_git_failure_is_also_reported_once(self):
        """Not git-specific: any source that reports through fatal() gets the
        same treatment."""
        self.mkFile("ivpm.yaml",
            "package:\n"
            "    name: err_root2\n"
            "    dep-sets:\n"
            "        - name: default-dev\n"
            "          deps:\n"
            "                    - name: missing\n"
            "                      url: file:///nonexistent/path/xyz\n"
            "                      src: dir\n")

        with self.assertRaises(SrcLoaderError):
            self.ivpm_update(skip_venv=True)

        errors = self._errors()
        self.assertEqual(len(errors), 2, "\n---\n".join(d.format() for d in errors))
        self.assertIn("/nonexistent/path/xyz", errors[0].message)
        self.assertNotIn(errors[0].message, errors[1].message)


class TestFailureMessage(unittest.TestCase):
    """_failure_message decides whether the reason still has to be quoted."""

    class _Pkg:
        name = "dep1"
        src_type = "git"
        url = "https://example.invalid/r.git"

    def test_reported_exception_is_not_requoted(self):
        from ivpm.package_updater import _failure_message
        exc = SrcLoaderError("the whole story", [object()])
        out = _failure_message(self._Pkg(), exc)
        self.assertNotIn("the whole story", out)
        self.assertIn("dep1", out)
        self.assertIn("src: git", out)

    def test_unreported_exception_is_quoted(self):
        """A plain exception was rendered nowhere, so dropping its message
        would leave the user with no reason at all."""
        from ivpm.package_updater import _failure_message
        out = _failure_message(self._Pkg(), RuntimeError("disk on fire"))
        self.assertIn("disk on fire", out)

    def test_bare_src_loader_error_is_quoted(self):
        """A SrcLoaderError with no diagnostics (e.g. a raw YAML parse error)
        never went through the reporter either."""
        from ivpm.package_updater import _failure_message
        out = _failure_message(self._Pkg(), SrcLoaderError("bad yaml"))
        self.assertIn("bad yaml", out)


if __name__ == "__main__":
    unittest.main()
