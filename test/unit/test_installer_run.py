#****************************************************************************
#* test_installer_run.py
#*
#* The shared installer runner. The property under test throughout is the one
#* the module exists for: quiet suppresses *display*, never *collection*.
#* Every attribution feature built on top of it can only report what was
#* captured, so a regression here silently guts the ones above it.
#****************************************************************************
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.installer_run import InstallerResult, format_output_tail, run_installer


def _py(script):
    """An argv that runs *script* with this interpreter."""
    return [sys.executable, "-c", script]


class TestRunInstaller(unittest.TestCase):

    def test_output_is_captured_when_quiet(self):
        """The whole point: DEVNULL is never an option."""
        r = run_installer(_py("print('hello from the installer')"), quiet=True)
        self.assertTrue(r.ok)
        self.assertIn("hello from the installer", "\n".join(r.lines))

    def test_stderr_is_captured_and_interleaved(self):
        """A build failure's traceback is on stderr; the line naming the
        package being built is on stdout. Only together do they explain."""
        r = run_installer(_py(
            "import sys\n"
            "print('Building widget')\n"
            "sys.stdout.flush()\n"
            "print('error: no such header', file=sys.stderr)\n"), quiet=True)
        out = "\n".join(r.lines)
        self.assertIn("Building widget", out)
        self.assertIn("error: no such header", out)

    def test_nonzero_exit_is_returned_not_raised(self):
        """The caller owns severity and source location, so this never raises."""
        r = run_installer(_py("import sys; sys.exit(3)"), quiet=True)
        self.assertEqual(3, r.returncode)
        self.assertFalse(r.ok)

    def test_missing_executable_is_a_result(self):
        """A missing installer takes the same path as any other failure."""
        r = run_installer(["ivpm-no-such-installer-xyz"], quiet=True)
        self.assertEqual(127, r.returncode)
        self.assertTrue(r.lines, "the reason must still be reported")

    def test_cwd_and_env_are_honoured(self):
        here = os.path.dirname(os.path.abspath(__file__))
        r = run_installer(
            _py("import os; print(os.getcwd()); print(os.environ['IVPM_TEST_VAR'])"),
            env=dict(os.environ, IVPM_TEST_VAR="marker"),
            cwd=here,
            quiet=True)
        out = "\n".join(r.lines)
        self.assertIn(os.path.realpath(here), os.path.realpath(out.splitlines()[0]))
        self.assertIn("marker", out)

    def test_line_parser_drives_task_progress(self):
        class _Task:
            def __init__(self):
                self.seen = []
            def progress(self, m):
                self.seen.append(m)

        task = _Task()
        run_installer(_py("print('Building a')\nprint('noise')\nprint('Building b')"),
                      task=task,
                      quiet=True,
                      line_parser=lambda l: l if l.startswith("Building ") else None)
        self.assertEqual(["Building a", "Building b"], task.seen)

    def test_line_parser_without_task_is_inert(self):
        """No task handle means nothing to report progress to -- and the
        parser must not be called into a None."""
        r = run_installer(_py("print('Building a')"), quiet=True,
                          line_parser=lambda l: l)
        self.assertTrue(r.ok)

    def test_result_records_the_command(self):
        cmd = _py("pass")
        self.assertEqual(cmd, run_installer(cmd, quiet=True).cmd)


class TestFormatOutputTail(unittest.TestCase):

    def test_empty_input_yields_empty_string(self):
        self.assertEqual("", format_output_tail([]))
        self.assertEqual("", format_output_tail(["", "   "]),
                         "all-blank output has nothing to show")

    def test_blank_lines_do_not_consume_the_budget(self):
        lines = []
        for i in range(10):
            lines.extend(["line%d" % i, "", "  "])
        out = format_output_tail(lines, tail=3)
        self.assertEqual(["line7", "line8", "line9"], out.strip().splitlines())

    def test_shorter_than_tail_is_returned_whole(self):
        self.assertEqual(["a", "b"], format_output_tail(["a", "b"], tail=20).strip().splitlines())

    def test_output_is_newline_prefixed(self):
        """Callers concatenate it onto a message, so it must break the line."""
        self.assertTrue(format_output_tail(["a"]).startswith("\n"))


if __name__ == "__main__":
    unittest.main()
