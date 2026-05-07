"""
Tests for TranscriptUpdateTUI and TranscriptSyncTUI.

Covers thread safety (Phase 2), package tagging (Phase 3),
verbosity gating and percentage throttling (Phase 4),
p4_mkwa output suppression (Phase 5), and sync TUI verbosity (Phase 6).
"""
import io
import sys
import threading
import unittest
from contextlib import redirect_stdout

from ivpm.update_event import UpdateEvent, UpdateEventType
from ivpm.update_tui import TranscriptUpdateTUI


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update_tui(verbose=0):
    """Create a TranscriptUpdateTUI and a StringIO buffer capturing stdout."""
    tui = TranscriptUpdateTUI(verbose=verbose)
    buf = io.StringIO()
    return tui, buf


def _capture(tui, event):
    """Fire a single event on *tui* and return captured stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        tui.on_event(event)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Phase 2: Thread safety
# ---------------------------------------------------------------------------

class TestTranscriptUpdateTUIThreadSafety(unittest.TestCase):
    """Verify that concurrent on_event calls produce complete lines."""

    def test_concurrent_events_no_interleave(self):
        """Fire 50 PACKAGE_START events from 10 threads; every output
        line must start with '>>' and contain a package name."""
        tui = TranscriptUpdateTUI(verbose=0)
        buf = io.StringIO()
        barrier = threading.Barrier(10)

        def _fire(idx):
            barrier.wait()
            for i in range(5):
                name = "pkg_%d_%d" % (idx, i)
                tui.on_event(UpdateEvent(
                    event_type=UpdateEventType.PACKAGE_START,
                    package_name=name,
                    package_type="git",
                ))

        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            threads = [threading.Thread(target=_fire, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sys.stdout = old_stdout

        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 50)
        for line in lines:
            self.assertTrue(line.startswith(">>"), "Line does not start with '>>': %r" % line)
            self.assertIn("pkg_", line)


# ---------------------------------------------------------------------------
# Phase 3: Package tagging
# ---------------------------------------------------------------------------

class TestTranscriptUpdateTUITagging(unittest.TestCase):

    def test_package_start_includes_type(self):
        """PACKAGE_START line includes the package type in parens."""
        tui, _ = _make_update_tui()
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.PACKAGE_START,
            package_name="nbio_rtl", package_type="p4_mkwa"))
        self.assertIn(">> nbio_rtl (p4_mkwa)", output)

    def test_package_start_no_type(self):
        """PACKAGE_START with no type omits the parens."""
        tui, _ = _make_update_tui()
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.PACKAGE_START,
            package_name="nbio_rtl"))
        self.assertIn(">> nbio_rtl", output)
        self.assertNotIn("(", output)

    def test_package_complete_includes_duration(self):
        """PACKAGE_COMPLETE line includes duration."""
        tui, _ = _make_update_tui()
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.PACKAGE_COMPLETE,
            package_name="nbio_rtl", duration=4.23))
        self.assertIn("<< nbio_rtl (4.2s)", output)

    def test_handler_progress_includes_package_name(self):
        """HANDLER_TASK_PROGRESS lines include [pkg] prefix."""
        tui, _ = _make_update_tui(verbose=1)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
            package_name="soc_model", task_id="p4",
            task_name="p4_mkwa", task_message="Syncing files 50%"))
        self.assertIn("[soc_model]", output)
        self.assertIn("Syncing files 50%", output)

    def test_handler_progress_without_package_uses_task_name(self):
        """Non-package tasks (e.g. Python) use task_name only."""
        tui, _ = _make_update_tui(verbose=1)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
            task_id="python-install", task_name="Python",
            task_message="Collecting requests"))
        self.assertIn("Collecting requests", output)
        self.assertNotIn("[None]", output)

    def test_handler_task_end_suppressed_for_package_scoped(self):
        """Package-scoped HANDLER_TASK_END does not print (the << pkg
        line is sufficient)."""
        tui, _ = _make_update_tui(verbose=1)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_END,
            package_name="nbio_rtl", task_id="p4",
            task_name="p4_mkwa", duration=127.3))
        self.assertEqual(output.strip(), "")

    def test_handler_task_error_includes_package_name(self):
        """HANDLER_TASK_ERROR lines include [pkg] prefix."""
        tui, _ = _make_update_tui()
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_ERROR,
            package_name="nbio_rtl", task_id="p4",
            task_name="p4_mkwa", task_message="login failed"))
        self.assertIn("[nbio_rtl]", output)
        self.assertIn("ERROR", output)
        self.assertIn("login failed", output)


# ---------------------------------------------------------------------------
# Phase 4: Verbosity gating and throttling
# ---------------------------------------------------------------------------

class TestTranscriptUpdateTUIVerbosity(unittest.TestCase):

    def test_verbose_0_suppresses_handler_progress(self):
        """At verbose=0, HANDLER_TASK_PROGRESS produces no output."""
        tui, _ = _make_update_tui(verbose=0)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
            package_name="pkg", task_id="t",
            task_message="doing stuff"))
        self.assertEqual(output, "")

    def test_verbose_0_suppresses_package_scoped_task_start(self):
        """At verbose=0, package-scoped HANDLER_TASK_START is silent."""
        tui, _ = _make_update_tui(verbose=0)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_START,
            package_name="pkg", task_id="t", task_name="p4_mkwa"))
        self.assertEqual(output.strip(), "")

    def test_verbose_0_shows_root_task_start(self):
        """At verbose=0, root-level tasks (no package) still print."""
        tui, _ = _make_update_tui(verbose=0)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_START,
            task_id="python", task_name="Python"))
        self.assertIn(">> [Python]", output)

    def test_verbose_1_shows_handler_progress(self):
        """At verbose=1, HANDLER_TASK_PROGRESS is shown."""
        tui, _ = _make_update_tui(verbose=1)
        output = _capture(tui, UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
            package_name="pkg", task_id="t",
            task_message="doing stuff"))
        self.assertIn("doing stuff", output)

    def test_pct_throttle_skips_intermediate(self):
        """Progress at 1%, 2%, 3% ... only prints at ~10% boundaries."""
        tui, _ = _make_update_tui(verbose=1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            for pct in range(1, 100):
                tui.on_event(UpdateEvent(
                    event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
                    package_name="pkg", task_id="t", task_name="sync",
                    task_message="Syncing %d%%" % pct,
                    task_step=pct, task_total=100))
        output = buf.getvalue()
        lines = [l for l in output.strip().splitlines() if l.strip()]
        # Should have ~10 lines (at 1, 11, 21, ..., 91) not 99
        self.assertLessEqual(len(lines), 12)
        self.assertGreaterEqual(len(lines), 8)

    def test_pct_100_always_prints(self):
        """The final 100% progress always prints regardless of throttle."""
        tui, _ = _make_update_tui(verbose=1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            tui.on_event(UpdateEvent(
                event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
                package_name="pkg", task_id="t",
                task_message="5%", task_step=5, task_total=100))
            tui.on_event(UpdateEvent(
                event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
                package_name="pkg", task_id="t",
                task_message="100%", task_step=100, task_total=100))
        self.assertIn("100%", buf.getvalue())


# ---------------------------------------------------------------------------
# Phase 5: p4_mkwa output suppression (source-level guard)
# ---------------------------------------------------------------------------

class TestP4MkwaOutputSuppression(unittest.TestCase):

    def test_no_stdout_write_in_exec(self):
        """Verify the _exec_p4_mkwa code has no sys.stdout.write call.

        This is a source-level assertion to guard against regressions.
        """
        import inspect
        from ivpm_amd_ext.package_p4_mkwa import PackageP4Mkwa
        source = inspect.getsource(PackageP4Mkwa._exec_p4_mkwa)
        self.assertNotIn("sys.stdout.write", source)
        self.assertNotIn("stdout.write(line)", source)


# ---------------------------------------------------------------------------
# Phase 6: Sync TUI verbosity
# ---------------------------------------------------------------------------

class TestTranscriptSyncTUIVerbosity(unittest.TestCase):

    def test_verbose_0_omits_attention_detail(self):
        """At verbose=0, render() prints only the summary line."""
        from ivpm.pkg_sync import PkgSyncResult, SyncOutcome
        from ivpm.sync_tui import TranscriptSyncTUI
        tui = TranscriptSyncTUI(verbose=0)
        results = [PkgSyncResult(
            name="pkg", src_type="git", path="/tmp/pkg",
            outcome=SyncOutcome.CONFLICT,
            conflict_files=["foo.v"],
            next_steps=["cd pkg && git merge --abort"])]
        buf = io.StringIO()
        with redirect_stdout(buf):
            tui.render(results)
        output = buf.getvalue()
        self.assertIn("1 conflict", output)   # summary
        self.assertNotIn("foo.v", output)      # no detail

    def test_verbose_1_includes_attention_detail(self):
        """At verbose=1, render() includes conflict files."""
        from ivpm.pkg_sync import PkgSyncResult, SyncOutcome
        from ivpm.sync_tui import TranscriptSyncTUI
        tui = TranscriptSyncTUI(verbose=1)
        results = [PkgSyncResult(
            name="pkg", src_type="git", path="/tmp/pkg",
            outcome=SyncOutcome.CONFLICT,
            conflict_files=["foo.v"],
            next_steps=["cd pkg && git merge --abort"])]
        buf = io.StringIO()
        with redirect_stdout(buf):
            tui.render(results)
        output = buf.getvalue()
        self.assertIn("foo.v", output)
        self.assertIn("1 conflict", output)


if __name__ == "__main__":
    unittest.main()
