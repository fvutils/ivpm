#****************************************************************************
#* test_install_failure_report.py
#*
#* The readability of a failed Python install, from a real failure: uv could
#* not build pyboolector (no CPython 3.14 wheel; broken sdist) in a phase of
#* 54 requirements. uv named the package in its first error line, yet the
#* report blamed all 44 contributors, showed only a traceback tail, and was
#* printed twice. Each test below pins one of the fixes.
#****************************************************************************
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.content_attrib import (
    ENROLLED_SRC_TYPE, ContentOrigin, OriginMap, format_content_failure,
)
from ivpm.diagnostics import Diagnostic, Severity
from ivpm.handlers.package_handler import task_error_text
from ivpm.handlers.package_handler_python import (
    _installer_failed_dist, _installer_failed_spec, _rerun_one,
    _save_installer_log,
)
from ivpm.installer_run import InstallerResult, format_output_tail
from ivpm.package import Package
from ivpm.pkg_types.package_pypi import PackagePyPi
from ivpm.wheel_hint import (
    _pythons_from_filenames, _ranges, explain_source_build, split_requirement,
)
from ivpm.yamlsrc.loader import SrcLoaderError


# uv --verbose output for the failure, trimmed: the summary block comes first,
# then a long build log, then lock-release trace.
UV_OUTPUT = (
    ["Using Python 3.14.2 environment at: python",
     "Resolved 133 packages in 20ms",
     "   Building pyboolector==3.2.4.19342042739",
     "Downloading lxml (5.0MiB)",
     "  × Failed to build `pyboolector==3.2.4.19342042739`",
     "  ├─▶ The build backend returned an error",
     "  ╰─▶ Call to `setuptools.build_meta:__legacy__.build_wheel` failed (exit",
     "      status: 1)",
     "",
     "      [stdout]",
     "      running build_ext",
     "",
     "      [stderr]"]
    + ["      Traceback frame %d" % i for i in range(60)]
    + ["      Cython.Compiler.Errors.InternalError: Internal compiler error:",
       "      'pyboolector_enums.pxd' not found",
       "",
       "      hint: This usually indicates a problem with the package or the build",
       "      environment.",
       "DEBUG Released lock at `/w/packages/python/.lock`",
       "DEBUG Released lock at `/home/u/.cache/uv/.lock`"])


class _SrcInfo:
    def __init__(self, filename, lineno, linepos=1):
        self.filename = filename
        self.lineno = lineno
        self.linepos = linepos

    def __str__(self):
        return "%s:%d:%d" % (self.filename, self.lineno, self.linepos)

    def excerpt(self):
        return "    - name: pyboolector\n      ^"


class TestCulpritName(unittest.TestCase):
    """D1: the fast path must survive uv's pinned spelling."""

    def test_version_is_stripped_for_lookup(self):
        self.assertEqual(_installer_failed_dist(UV_OUTPUT), "pyboolector")

    def test_spec_keeps_the_version_for_the_report(self):
        self.assertEqual(_installer_failed_spec(UV_OUTPUT),
                         "pyboolector==3.2.4.19342042739")

    def test_pinned_name_resolves_to_its_origin(self):
        om = OriginMap()
        om.record("pyboolector", Package("pyboolector"), "g.txt",
                  enrolled_by=ENROLLED_SRC_TYPE)
        om.record("click", Package("click"), "g.txt",
                  enrolled_by=ENROLLED_SRC_TYPE)
        origin = om.resolve_dist(_installer_failed_dist(UV_OUTPUT), group="g.txt")
        self.assertIsNotNone(origin)
        self.assertEqual(origin.name, "pyboolector")

    def test_split_requirement(self):
        self.assertEqual(split_requirement("a-b==1.0"), ("a-b", "1.0"))
        self.assertEqual(split_requirement("a>=1"), ("a", None))
        self.assertEqual(split_requirement("a[x]==2"), ("a", "2"))
        self.assertEqual(split_requirement("a @ file:///x"), ("a", None))
        self.assertEqual(split_requirement("a"), ("a", None))


class TestExcerpt(unittest.TestCase):
    """D2: the installer's own summary leads the excerpt."""

    def test_headline_is_kept_ahead_of_the_tail(self):
        out = format_output_tail(UV_OUTPUT).splitlines()[1:]
        self.assertIn("Failed to build `pyboolector", out[0])
        self.assertIn("status: 1)", out[3])
        self.assertEqual(out[4].strip(), "...")
        self.assertIn("pyboolector_enums.pxd", "\n".join(out))
        self.assertNotIn("DEBUG", "\n".join(out))

    def test_headline_stops_at_the_blank_line(self):
        out = format_output_tail(UV_OUTPUT)
        self.assertNotIn("[stdout]", out)

    def test_short_output_is_not_duplicated(self):
        lines = ["  × Failed to build `x==1`", "  error: boom"]
        out = format_output_tail(lines).splitlines()[1:]
        self.assertEqual(out, lines)


class TestWheelHint(unittest.TestCase):
    """D6: explain the source build in terms the user can act on."""

    FILES = [
        "p-1-cp312-cp312-manylinux_2_28_x86_64.whl",
        "p-1-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "p-1-cp314-cp314-macosx_15_0_arm64.whl",
        "p-1-cp311-cp311-manylinux_2_28_aarch64.whl",
        "p-1.tar.gz",
    ]

    def test_wheel_pythons_for_this_platform(self):
        self.assertEqual(_pythons_from_filenames(self.FILES, "linux", "x86_64"),
                         ["3.12", "3.13"])
        self.assertEqual(_pythons_from_filenames(self.FILES, "macosx", "arm64"),
                         ["3.14"])

    def test_pure_wheel_means_no_hint(self):
        self.assertIsNone(_pythons_from_filenames(
            ["p-1-py3-none-any.whl"], "linux", "x86_64"))

    def test_ranges(self):
        self.assertEqual(_ranges(["3.8", "3.9", "3.10", "3.13"]), "3.8-3.10, 3.13")

    def test_explanation_names_the_interpreter_and_the_alternatives(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "pyvenv.cfg"), "w") as fp:
                fp.write("home = /x\nversion_info = 3.14.2\n")
            why, steps = explain_source_build(
                "p", "1", d, query=lambda n, v: ["3.12", "3.13"])
        text = "\n".join(why)
        self.assertIn("CPython 3.14", text)
        self.assertIn("3.12-3.13", text)
        self.assertTrue(any("3.12-3.13" in s for s in steps))

    def test_unreachable_index_still_explains(self):
        why, steps = explain_source_build("p", "1", None,
                                          query=lambda n, v: None)
        self.assertTrue(why)
        self.assertEqual(steps, [])


class TestReportLayout(unittest.TestCase):
    """D3/D4: compact when unattributed, log path named, re-run offered."""

    def _origin(self, name, line):
        p = PackagePyPi(name)
        p.srcinfo = _SrcInfo("/w/ivpm.yaml", line, 7)
        return ContentOrigin(pkg=p, spec=name, group="g.txt",
                             enrolled_by=ENROLLED_SRC_TYPE)

    def test_many_origins_are_one_line_each(self):
        origins = [self._origin("pkg%02d" % i, i) for i in range(1, 30)]
        text = format_content_failure("python", origins, {})
        self.assertNotIn("was treated as", text)
        self.assertEqual(text.count("pkg07"), 2)   # header list + one row
        self.assertLess(len(text.splitlines()), 40)

    def test_pypi_enrollment_reason_is_omitted(self):
        text = format_content_failure("python", [self._origin("a", 1)], {})
        self.assertNotIn("explicit source type", text)

    def test_why_steps_log_and_rerun_are_rendered(self):
        text = format_content_failure(
            "python", [self._origin("a", 1)], {},
            output="\n  x Failed to build `a==1`",
            why=["first", "second"], steps=["do this"],
            log_path="/w/packages/python_pkgs_4.log",
            rerun="uv pip install a==1")
        self.assertIn("  why: first\n       second", text)
        self.assertIn("    - do this", text)
        self.assertIn("full log: /w/packages/python_pkgs_4.log", text)
        self.assertTrue(text.rstrip().endswith(
            "re-run just this package: uv pip install a==1"))

    def test_rerun_narrows_and_targets_the_venv(self):
        cmd = ["/bin/uv", "pip", "install", "--verbose", "-r", "/w/p.txt"]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "bin"))
            open(os.path.join(d, "bin", "python"), "w").close()
            rerun = _rerun_one(cmd, "a==1", d)
        self.assertTrue(rerun.startswith("/bin/uv pip install --python %s"
                                         % os.path.join(d, "bin", "python")))
        self.assertTrue(rerun.endswith("--verbose a==1"))
        self.assertNotIn("p.txt", rerun)

    def test_rerun_without_a_venv_still_renders(self):
        cmd = ["/bin/uv", "pip", "install", "-r", "/w/p.txt"]
        self.assertEqual(_rerun_one(cmd, "a==1", "/nonexistent"),
                         "/bin/uv pip install a==1")

    def test_full_log_is_saved_beside_the_input(self):
        with tempfile.TemporaryDirectory() as d:
            req = os.path.join(d, "python_pkgs_4.txt")
            open(req, "w").close()
            path = _save_installer_log(
                req, InstallerResult(returncode=1, lines=UV_OUTPUT, cmd=["uv"]))
            self.assertEqual(path, os.path.join(d, "python_pkgs_4.log"))
            with open(path) as fp:
                self.assertIn("Traceback frame 30", fp.read())

    def test_no_log_without_a_real_input_file(self):
        self.assertIsNone(_save_installer_log(
            "python_pkgs_9.txt",
            InstallerResult(returncode=1, lines=["x"], cmd=["uv"])))


class TestPrintedOnce(unittest.TestCase):
    """D5: a rendered report is not repeated on the task's << line."""

    def test_reported_error_carries_its_first_line_only(self):
        e = SrcLoaderError("failed to install x\n\n  body", ["diag"])
        self.assertEqual(task_error_text(e), "failed to install x")

    def test_unreported_error_is_carried_whole(self):
        self.assertEqual(task_error_text(RuntimeError("a\nb")), "a\nb")

    def test_excerpt_sits_under_the_header(self):
        d = Diagnostic(Severity.FATAL, "head\n  body", _SrcInfo("f", 1))
        lines = d.format(excerpt=True).splitlines()
        self.assertEqual(lines[0], "f:1:1: fatal: head")
        self.assertEqual(lines[2].strip(), "^")
        self.assertEqual(lines[3], "  body")


if __name__ == "__main__":
    unittest.main()
