#****************************************************************************
#* test_build_progress.py
#*
#* Signs of life during a long install: the build steps read from relayed
#* build output (installer_run.build_step / BuildProgress), and the transcript
#* output's root-task progress, step throttling and "still running" lines.
#* Before these, a one-minute compile printed nothing at all when stdout was
#* not a terminal.
#****************************************************************************
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.handlers.package_handler_python import PackageHandlerPython
from ivpm.installer_run import BuildProgress, build_step
from ivpm.update_event import UpdateEvent, UpdateEventType
from ivpm.update_tui import TranscriptUpdateTUI


class TestBuildStep(unittest.TestCase):

    def test_steps(self):
        cases = {
            "DEBUG [1/1] Cythonizing /w/pkg/python/ast.pyx": "cythonizing ast.pyx",
            "DEBUG building 'pssparser.ast' extension": "building extension pssparser.ast",
            "DEBUG x86_64-linux-gnu-g++ -fno-strict-overflow -O2 -fPIC -I/w/inc "
            "-c /w/pkg/python/ast.cpp -o /tmp/b/ast.o -std=c++17": "compiling ast.cpp",
            "DEBUG gcc -c foo.c -o foo.o": "compiling foo.c",
            "DEBUG x86_64-linux-gnu-g++ -shared -Wl,-O1 /tmp/b/ast.o "
            "-o /tmp/l/pssparser/ast.cpython-312-x86_64-linux-gnu.so":
                "linking ast.cpython-312-x86_64-linux-gnu.so",
            "DEBUG [12/80] Building CXX object src/CMakeFiles/x.dir/Lexer.cpp.o":
                "building Lexer.cpp (12/80)",
            "DEBUG [1/2] Linking CXX shared library src/libpssparser_c.so":
                "linking libpssparser_c.so (1/2)",
            "DEBUG [ 42%] Building CXX object src/CMakeFiles/x.dir/Parser.cpp.o":
                "building Parser.cpp (42%)",
            "DEBUG cmake config command: ['cmake', '/w/pkg', '-GNinja']": "configuring (cmake)",
        }
        for line, want in cases.items():
            self.assertEqual(build_step(line), want, line)

    def test_not_steps(self):
        for line in ("DEBUG [0/2] Re-checking globbed directories...",
                     "DEBUG Searching for a compatible version of foo",
                     "DEBUG /w/pkg/ast.cpp:569589: note: adding '-flarge-source-files'",
                     "g++ -c foo.cpp -o foo.o",            # not relayed build output
                     "DEBUG gcc", ""):
            self.assertIsNone(build_step(line), line)

    def test_under_the_one_package_building(self):
        p = BuildProgress(PackageHandlerPython._uv_line_parser)
        self.assertEqual(p("   Building pssparser @ file:///w/pssparser"), "Building pssparser")
        self.assertEqual(p("DEBUG g++ -c ast.cpp -o ast.o"), "Building pssparser: compiling ast.cpp")
        self.assertEqual(p("   Building other @ file:///w/other"), "Building other")
        self.assertEqual(p("DEBUG g++ -c x.cpp -o x.o"), "compiling x.cpp")   # which one? not said
        self.assertEqual(p("      Built other @ file:///w/other"), "Built other")
        self.assertEqual(p("DEBUG g++ -c y.cpp -o y.o"), "Building pssparser: compiling y.cpp")
        self.assertIsNone(p("DEBUG Released lock at /tmp/x"))


def _ev(kind, **kw):
    return UpdateEvent(event_type=kind, **kw)


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _run(tui, *events):
    buf = io.StringIO()
    with redirect_stdout(buf):
        for e in events:
            tui.on_event(e) if isinstance(e, UpdateEvent) else e()
    return buf.getvalue()


class TestTranscriptLife(unittest.TestCase):

    def _tui(self):
        clock = _Clock()
        return TranscriptUpdateTUI(verbose=0, heartbeat=15, clock=clock), clock

    def _start(self):
        return _ev(UpdateEventType.HANDLER_TASK_START, task_id="py",
                   task_name="Installing Python packages")

    def _say(self, msg, **kw):
        return _ev(UpdateEventType.HANDLER_TASK_PROGRESS, task_id="py",
                   task_name="Installing Python packages", task_message=msg, **kw)

    def test_root_task_progress_shows_at_verbose_0(self):
        tui, _ = self._tui()
        out = _run(tui, self._start(), self._say("Building pssparser"))
        self.assertIn("   Building pssparser\n", out)

    def test_small_totals_show_every_step(self):
        tui, _ = self._tui()
        out = _run(tui, self._start(),
                   *[self._say("Installing package set %d/5" % i, task_step=i, task_total=5)
                     for i in range(1, 6)])
        for i in range(1, 6):
            self.assertIn("(%d/5)" % i, out)

    def test_repeat_is_said_once(self):
        tui, _ = self._tui()
        out = _run(tui, self._start(), self._say("Building a"), self._say("Building a"))
        self.assertEqual(out.count("Building a"), 1)

    def test_one_line_per_rate_then_the_latest(self):
        tui, clock = self._tui()
        out = _run(tui, self._start(), *[self._say("Building p: building f%d.cpp (%d/232)" % (i, i))
                                         for i in range(1, 50)])
        self.assertEqual(out.count("Building p:"), 1)                # the first only
        clock.t += 6
        out = _run(tui, self._say("Building p: building f50.cpp (50/232)"))
        self.assertIn("(50/232)", out)                               # 5s on: one more
        _run(tui, self._say("Building p: building f51.cpp (51/232)"))
        clock.t += 20
        self.assertEqual(_run(tui, tui.tick),                        # quiet: the latest
                         "   Building p: building f51.cpp (51/232) (still running, 20s)\n")

    def test_verbose_1_prints_every_line(self):
        tui = TranscriptUpdateTUI(verbose=1, heartbeat=15, clock=_Clock())
        out = _run(tui, self._start(), *[self._say("Selecting p%d==1" % i) for i in range(20)])
        self.assertEqual(out.count("Selecting"), 20)

    def test_still_running_after_silence(self):
        tui, clock = self._tui()
        out = _run(tui, self._start(), self._say("Building pssparser: compiling ast.cpp"))
        clock.t += 10
        self.assertEqual(_run(tui, tui.tick), "")                    # not yet
        clock.t += 70
        out = _run(tui, tui.tick)
        self.assertEqual(out, "   Building pssparser: compiling ast.cpp (still running, 1m 20s)\n")
        clock.t += 5
        self.assertEqual(_run(tui, tui.tick), "")                    # just said
        out = _run(tui, _ev(UpdateEventType.HANDLER_TASK_END, task_id="py",
                            task_name="Installing Python packages", duration=95.0))
        clock.t += 60
        self.assertEqual(_run(tui, tui.tick), "")                    # ended: quiet

    def test_still_running_names_the_task_before_any_progress(self):
        tui, clock = self._tui()
        _run(tui, self._start())
        clock.t += 20
        self.assertEqual(_run(tui, tui.tick),
                         "   Installing Python packages (still running, 20s)\n")

    def test_parent_quiet_while_child_runs(self):
        tui, clock = self._tui()
        _run(tui, self._start(), _ev(UpdateEventType.HANDLER_TASK_START, task_id="c",
                                     task_name="phase", parent_task_id="py"))
        clock.t += 20
        out = _run(tui, tui.tick)
        self.assertEqual(out, "     phase (still running, 20s)\n")

    def test_background_thread_beats(self):
        tui = TranscriptUpdateTUI(verbose=0, heartbeat=0.6)
        import time
        buf = io.StringIO()
        with redirect_stdout(buf):
            tui.on_event(self._start())
            time.sleep(1.5)
            tui.on_event(_ev(UpdateEventType.HANDLER_TASK_END, task_id="py",
                             task_name="Installing Python packages", duration=1.5))
            time.sleep(0.7)
        self.assertIn("(still running,", buf.getvalue())
        self.assertIsNone(tui._beater)                              # it stopped


if __name__ == "__main__":
    unittest.main()
