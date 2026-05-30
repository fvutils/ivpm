"""
Unit tests for ivpm.diagnostics and the ivpm.msg facade.

Covers: always-notify (independent of logging level), structured formatting,
error accumulation + abort, fatal raising, and sink swapping.
"""
import io
import unittest

from ivpm import msg
from ivpm.diagnostics import (
    Severity, Diagnostic, DiagnosticReporter, PlainSink, CollectingSink,
    SrcLoaderError,
)
from ivpm.yamlsrc import SrcInfo, SrcText


def _si(line=8, col=7, filename="ivpm.yaml", text=None):
    return SrcInfo(filename, line, col, srctext=SrcText(filename, text))


class TestFormatting(unittest.TestCase):

    def test_format_with_location(self):
        d = Diagnostic(Severity.ERROR, "boom", _si(8, 7))
        self.assertEqual(d.format(), "ivpm.yaml:8:7: error: boom")

    def test_format_without_location(self):
        d = Diagnostic(Severity.ERROR, "boom")
        self.assertEqual(d.format(), "error: boom")

    def test_loc_message_backcompat(self):
        # No location -> exception text equals the raw message (back-compat).
        self.assertEqual(Diagnostic(Severity.FATAL, "x").loc_message(), "x")
        # With location -> file:line:col prefix, but no severity word.
        self.assertEqual(
            Diagnostic(Severity.FATAL, "x", _si(2, 3)).loc_message(),
            "ivpm.yaml:2:3: x")

    def test_excerpt_rendered_for_errors(self):
        text = "package:\n  name: demo\n"
        d = Diagnostic(Severity.ERROR, "bad", _si(2, 9, text=text))
        out = d.format(excerpt=True)
        self.assertIn("name: demo", out)
        self.assertIn("^", out)

    def test_notes_rendered(self):
        note = Diagnostic(Severity.NOTE, "declared here", _si(3, 3))
        d = Diagnostic(Severity.ERROR, "dup", _si(8, 7), notes=[note])
        out = d.format()
        self.assertIn("error: dup", out)
        self.assertIn("note: declared here", out)

    def test_loc_accepts_object_with_srcinfo(self):
        class Carrier:
            srcinfo = _si(5, 1)
        d = Diagnostic(Severity.ERROR, "m", Carrier())
        self.assertEqual(d.format(), "ivpm.yaml:5:1: error: m")


class TestReporter(unittest.TestCase):

    def test_plain_sink_writes_to_stream(self):
        buf = io.StringIO()
        r = DiagnosticReporter(PlainSink(out=buf))
        r.warning("careful")
        self.assertIn("warning: careful", buf.getvalue())

    def test_error_accumulates_no_raise(self):
        r = DiagnosticReporter(CollectingSink())
        r.error("one")
        r.error("two")          # must not raise
        self.assertEqual(r.error_count, 2)

    def test_warning_counted_not_in_errors(self):
        r = DiagnosticReporter(CollectingSink())
        r.warning("w")
        self.assertEqual(r.warning_count, 1)
        self.assertEqual(r.error_count, 0)

    def test_abort_if_errors_raises_summary(self):
        r = DiagnosticReporter(CollectingSink())
        r.error("one")
        r.error("two")
        with self.assertRaises(SrcLoaderError) as ctx:
            r.abort_if_errors()
        self.assertEqual(str(ctx.exception), "2 error(s), 0 warning(s)")
        self.assertEqual(len(ctx.exception.diagnostics), 2)

    def test_abort_if_errors_noop_when_clean(self):
        r = DiagnosticReporter(CollectingSink())
        r.note("ok")
        r.abort_if_errors()      # must not raise

    def test_fatal_raises_immediately(self):
        r = DiagnosticReporter(CollectingSink())
        with self.assertRaises(SrcLoaderError) as ctx:
            r.fatal("stop")
        self.assertEqual(str(ctx.exception), "stop")


class TestMsgFacade(unittest.TestCase):

    def setUp(self):
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)

    def test_always_notify_regardless_of_log_level(self):
        # Logging defaults to NONE; warning/error must still reach the sink.
        msg.setup_logging("NONE")
        msg.warning("w")
        msg.error("e")
        out = "\n".join(self._sink.messages())
        self.assertIn("warning: w", out)
        self.assertIn("error: e", out)

    def test_fatal_via_facade_raises_and_records(self):
        with self.assertRaises(SrcLoaderError):
            msg.fatal("dead")
        self.assertEqual(msg.get_reporter().error_count, 1)

    def test_use_sink_swap_and_restore(self):
        other = CollectingSink()
        prev = msg.use_sink(other)
        self.assertIs(prev, self._sink)
        msg.warning("to-other")
        self.assertEqual(len(other.records), 1)
        self.assertEqual(len(self._sink.records), 0)
        msg.use_sink(prev)

    def test_fatal_with_location_prefixes_message(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            msg.fatal("missing url", _si(8, 7))
        self.assertEqual(str(ctx.exception), "ivpm.yaml:8:7: missing url")


class TestRichSink(unittest.TestCase):

    def test_rich_sink_prints_through_console(self):
        from ivpm.diagnostics import RichSink, Diagnostic, Severity

        class FakeConsole:
            def __init__(self):
                self.calls = []
            def print(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        fc = FakeConsole()
        RichSink(fc).emit(Diagnostic(Severity.ERROR, "boom", _si(8, 7)))
        self.assertEqual(len(fc.calls), 1)
        # rendered content carries the location and message
        rendered = str(fc.calls[0][0][0])
        self.assertIn("ivpm.yaml:8:7", rendered)
        self.assertIn("boom", rendered)


if __name__ == "__main__":
    unittest.main()
