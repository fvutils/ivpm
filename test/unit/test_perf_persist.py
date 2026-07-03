#****************************************************************************
#* test_perf_persist.py
#*
#* Phase-5 tests: perf record persistence, retention/pruning, the --timing
#* display hook, and crash-time persistence. The persistence/retention units
#* are hermetic; the update-driven cases reuse local dir packages.
#****************************************************************************
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.perf import (PerfCollector, write_record, prune_perf_dir,
                       list_record_paths, load_record, perf_dir)
from ivpm import perf_report

from .test_base import TestBase
from ivpm.project_ops import ProjectOps


class TestPerfPersistUnit(unittest.TestCase):
    """Pure persistence/retention units (no update flow)."""

    def setUp(self):
        import tempfile
        self.deps = tempfile.mkdtemp(prefix="ivpm-perf-persist-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.deps, ignore_errors=True)

    def _mk_record(self, runid):
        perf = PerfCollector()
        with perf.span("update"):
            with perf.span("fetch"):
                pass
        return perf.to_json(header={"runid": runid})

    def test_write_and_load(self):
        rec = self._mk_record("r1")
        path = write_record(self.deps, rec, "r1")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.basename(path), "perf-r1.json")
        loaded = load_record(path)
        self.assertEqual(loaded["header"]["runid"], "r1")
        self.assertTrue(any(s["category"] == "fetch" for s in loaded["spans"]))

    def test_retention_keeps_newest_n(self):
        import time
        for i in range(6):
            write_record(self.deps, self._mk_record("r%d" % i), "r%d" % i)
            # Ensure distinct mtimes for a deterministic newest-N.
            time.sleep(0.01)
        removed = prune_perf_dir(self.deps, keep=3)
        self.assertEqual(removed, 3)
        remaining = [os.path.basename(p) for p in list_record_paths(self.deps)]
        self.assertEqual(len(remaining), 3)
        # Newest three (r5,r4,r3) survive.
        self.assertEqual(set(remaining),
                         {"perf-r5.json", "perf-r4.json", "perf-r3.json"})

    def test_retention_zero_disables(self):
        for i in range(3):
            write_record(self.deps, self._mk_record("r%d" % i), "r%d" % i)
        self.assertEqual(prune_perf_dir(self.deps, keep=0), 0)
        self.assertEqual(len(list_record_paths(self.deps)), 3)

    def test_render_smoke(self):
        rec = self._mk_record("r1")
        out = perf_report.render(rec)
        self.assertIn("LONG POLE", out)
        self.assertIn("HOT SPOTS", out)
        self.assertIn("run r1", out)


class TestPerfPersistUpdate(TestBase):
    """Update-driven persistence via local dir packages (hermetic)."""

    def _mk_dir_pkg(self, name):
        p = os.path.join(self.testdir, "srcpkgs", name)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "f.txt"), "w") as fp:
            fp.write(name)
        return "file://" + p

    def _write_root(self, names):
        deps = "\n".join(
            "                    - name: %s\n"
            "                      url: %s\n"
            "                      src: dir" % (n, self._mk_dir_pkg(n))
            for n in names)
        self.mkFile("ivpm.yaml",
            "package:\n    name: perf_persist_root\n    dep-sets:\n"
            "        - name: default-dev\n          deps:\n%s\n" % deps)

    def _deps_dir(self):
        return os.path.join(self.testdir, "packages")

    def test_update_persists_record(self):
        self._write_root(["alpha", "beta"])

        class Args:
            anonymous_git = None
        ProjectOps(self.testdir, Args()).update(
            dep_set="default-dev", skip_venv=True, args=Args())

        paths = list_record_paths(self._deps_dir())
        self.assertEqual(len(paths), 1)
        rec = load_record(paths[0])
        self.assertIn("header", rec)
        self.assertIn("runid", rec["header"])
        cats = {s["category"] for s in rec["spans"]}
        self.assertIn("fetch", cats)
        self.assertIn("update", cats)

    def test_timing_flag_prints_report(self):
        self._write_root(["alpha"])

        class Args:
            anonymous_git = None
            timing = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            ProjectOps(self.testdir, Args()).update(
                dep_set="default-dev", skip_venv=True, args=Args(),
                timing=True)
        out = buf.getvalue()
        self.assertIn("LONG POLE", out)
        self.assertIn("PACKAGES", out)

    def test_crash_still_persists(self):
        """A failing update still writes a record from the finally block, so a
        crash mid-run is diagnosable (open spans flushed with end=None)."""
        # A dir package pointing at a nonexistent source fails the update.
        self.mkFile("ivpm.yaml",
            "package:\n    name: perf_crash_root\n    dep-sets:\n"
            "        - name: default-dev\n          deps:\n"
            "                    - name: missing\n"
            "                      url: file:///nonexistent/path/xyz\n"
            "                      src: dir\n")

        class Args:
            anonymous_git = None
        with self.assertRaises(BaseException):
            ProjectOps(self.testdir, Args()).update(
                dep_set="default-dev", skip_venv=True, args=Args())

        # Record persisted despite the failure.
        paths = list_record_paths(self._deps_dir())
        self.assertEqual(len(paths), 1)
        rec = load_record(paths[0])
        self.assertTrue(any(s["category"] == "update" for s in rec["spans"]))

    def test_no_timing_prints_no_report(self):
        self._write_root(["alpha"])

        class Args:
            anonymous_git = None
        buf = io.StringIO()
        with redirect_stdout(buf):
            ProjectOps(self.testdir, Args()).update(
                dep_set="default-dev", skip_venv=True, args=Args(),
                timing=False)
        self.assertNotIn("LONG POLE", buf.getvalue())
        # But the record is still persisted (collection is unconditional).
        self.assertEqual(len(list_record_paths(self._deps_dir())), 1)


if __name__ == "__main__":
    unittest.main()
