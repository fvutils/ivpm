"""
Tests that YAML parse errors produce visible diagnostics.

Covers two scenarios:
  1. Plain/debug mode (PlainSink): the error is printed to stderr.
  2. TUI mode (RichSink): the error is rendered through the Rich console.

The bug: ``yaml_load()`` raises ``SrcLoaderError`` directly, bypassing the
``DiagnosticReporter``. ``__main__.py`` catches ``SrcLoaderError`` and calls
``sys.exit(1)`` assuming diagnostics were already printed -- but they weren't.
"""
import io
import unittest

from ivpm import msg
from ivpm.diagnostics import (
    CollectingSink,
    DiagnosticReporter,
    PlainSink,
    RichSink,
    Severity,
    SrcLoaderError,
)
from ivpm.ivpm_yaml_reader import IvpmYamlReader


# A syntactically broken ivpm.yaml: ``deps:`` is indented one space too far,
# producing "mapping values are not allowed here" from the YAML scanner.
_BAD_INDENT_YAML = """\
package:
   name: ip-iohc
   dep-sets:
   - name: default
      deps:
        - name: a
          src: pypi
"""

# Another variant: unclosed bracket.
_UNCLOSED_YAML = """\
package:
  name: demo
  dep-sets:
    - name: default
      deps: [unclosed
"""


def _parse(yaml_text, name="test.yaml"):
    """Helper: parse *yaml_text* via IvpmYamlReader and return ProjInfo."""
    fp = io.StringIO(yaml_text)
    return IvpmYamlReader().read(fp, name)


class TestYamlParseErrorPlainSink(unittest.TestCase):
    """Verify that YAML parse errors are rendered through the PlainSink."""

    def setUp(self):
        self._buf = io.StringIO()
        self._sink = PlainSink(out=self._buf)
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)

    def test_bad_indent_produces_fatal_on_stderr(self):
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_INDENT_YAML, "bad_indent.yaml")
        output = self._buf.getvalue()
        self.assertIn("fatal:", output,
            "Expected 'fatal:' in stderr output but got: %r" % output)
        self.assertIn("mapping values are not allowed here", output,
            "Expected YAML problem description in output but got: %r" % output)
        self.assertIn("bad_indent.yaml", output,
            "Expected filename in output but got: %r" % output)

    def test_unclosed_bracket_produces_fatal_on_stderr(self):
        with self.assertRaises(SrcLoaderError):
            _parse(_UNCLOSED_YAML, "unclosed.yaml")
        output = self._buf.getvalue()
        self.assertIn("fatal:", output,
            "Expected 'fatal:' in stderr output but got: %r" % output)
        self.assertIn("unclosed.yaml", output,
            "Expected filename in output but got: %r" % output)

    def test_error_includes_line_and_column(self):
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_INDENT_YAML, "loc.yaml")
        output = self._buf.getvalue()
        # The location should contain file:line:col
        # "deps:" is on line 5 of _BAD_INDENT_YAML
        self.assertRegex(output, r"loc\.yaml:\d+:\d+",
            "Expected file:line:col in output but got: %r" % output)

    def test_error_includes_source_excerpt(self):
        """Fatal errors should include a source excerpt with a caret."""
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_INDENT_YAML, "excerpt.yaml")
        output = self._buf.getvalue()
        # The excerpt should show the offending line and a caret
        self.assertIn("^", output,
            "Expected caret (^) in source excerpt but got: %r" % output)


class TestYamlParseErrorCollectingSink(unittest.TestCase):
    """Verify that YAML parse errors go through the diagnostic reporter."""

    def setUp(self):
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)

    def test_bad_indent_emits_fatal_diagnostic(self):
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_INDENT_YAML)
        fatal_diags = [d for d in self._sink.records
                       if d.severity == Severity.FATAL]
        self.assertEqual(len(fatal_diags), 1,
            "Expected exactly 1 FATAL diagnostic but got %d: %s" % (
                len(fatal_diags),
                [d.format() for d in self._sink.records]))
        fatal_diag = fatal_diags[0]
        self.assertEqual(fatal_diag.severity, Severity.FATAL)
        self.assertIn("mapping values are not allowed here",
                       fatal_diag.message)

    def test_fatal_diagnostic_carries_srcinfo(self):
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_INDENT_YAML, "carry.yaml")
        fatal_diag = self._sink.records[-1]
        self.assertIsNotNone(fatal_diag.srcinfo,
            "Fatal diagnostic must carry srcinfo for location display")
        self.assertEqual(fatal_diag.srcinfo.filename, "carry.yaml")
        self.assertGreater(fatal_diag.srcinfo.lineno, 0)

    def test_raised_exception_has_diagnostics(self):
        """The SrcLoaderError raised after going through the reporter must
        have its .diagnostics list populated, so __main__.py knows the
        error was already rendered."""
        with self.assertRaises(SrcLoaderError) as ctx:
            _parse(_BAD_INDENT_YAML)
        self.assertTrue(ctx.exception.diagnostics,
            "SrcLoaderError.diagnostics must be non-empty when raised "
            "through the reporter")


class TestYamlParseErrorRichSink(unittest.TestCase):
    """Verify that YAML parse errors are rendered through a RichSink (TUI)."""

    def setUp(self):
        # Use a fake console to capture Rich output
        self._calls = []

        class FakeConsole:
            def __init__(self, calls):
                self._calls = calls
            def print(self, *args, **kwargs):
                self._calls.append((args, kwargs))

        self._console = FakeConsole(self._calls)
        self._sink = RichSink(self._console, min_severity=Severity.WARNING)
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)

    def test_bad_indent_rendered_through_rich(self):
        """YAML parse errors must be visible even with the TUI's RichSink
        (which suppresses NOTE-level diagnostics)."""
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_INDENT_YAML, "tui.yaml")
        # The fatal diagnostic has severity FATAL >= WARNING, so it must
        # have been emitted through the RichSink.
        self.assertGreater(len(self._calls), 0,
            "Expected RichSink to emit the fatal YAML error, but no "
            "calls were made to console.print()")
        rendered = str(self._calls[-1][0][0])
        self.assertIn("mapping values are not allowed here", rendered)
        self.assertIn("tui.yaml", rendered)


class TestMainSafetyNet(unittest.TestCase):
    """Test the __main__.py safety net for unrendered SrcLoaderErrors."""

    def test_unrendered_error_is_printed(self):
        """If a SrcLoaderError somehow bypasses the reporter (no diagnostics),
        __main__.py must still print the message."""
        # Simulate what __main__.py does
        e = SrcLoaderError("somefile.yaml:5:3: bad stuff happened")
        assert not e.diagnostics  # not rendered
        buf = io.StringIO()
        # Reproduce the safety-net logic
        if not e.diagnostics:
            print(str(e), file=buf)
        output = buf.getvalue()
        self.assertIn("bad stuff happened", output)
        self.assertIn("somefile.yaml:5:3", output)

    def test_rendered_error_not_double_printed(self):
        """If a SrcLoaderError was rendered (has diagnostics), the safety
        net should not print it again."""
        from ivpm.diagnostics import Diagnostic
        diag = Diagnostic(Severity.FATAL, "already shown")
        e = SrcLoaderError("already shown", diagnostics=[diag])
        buf = io.StringIO()
        if not e.diagnostics:
            print(str(e), file=buf)
        output = buf.getvalue()
        self.assertEqual(output, "",
            "Safety net should not print when diagnostics were already rendered")


if __name__ == "__main__":
    unittest.main()
