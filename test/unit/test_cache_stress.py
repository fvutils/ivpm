"""The multi-host stress harness itself: schedule, analysis, and a local run.

The harness is only worth running if ``analyze`` really flags violations, so
these tests feed it fabricated event logs with known defects, then do one
short single-host run end to end.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOTDIR, "src"))

_spec = importlib.util.spec_from_file_location(
    "cache_stress", os.path.join(ROOTDIR, "test", "stress", "cache_stress.py"))
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


class _RunDir(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.run_id = "t"
        self.rd = os.path.join(self.root, self.run_id)
        os.makedirs(os.path.join(self.rd, "events"))
        os.makedirs(os.path.join(self.rd, "cache"))
        with open(os.path.join(self.rd, cs.SENTINEL), "w") as f:
            f.write("{}")

    def tearDown(self):
        cs.make_writable_and_remove(self.root)

    def events(self, pid, *evs):
        with open(os.path.join(self.rd, "events", "h-%d.jsonl" % pid), "a") as f:
            f.write(json.dumps({"kind": "start", "id": "h-%d" % pid, "host": "h",
                                "pid": pid, "clock_offset": 0.0}) + "\n")
            for e in evs:
                e = dict({"kind": "result", "id": "h-%d" % pid, "host": "h",
                          "pid": pid}, **e)
                f.write(json.dumps(e) + "\n")
            f.write(json.dumps({"kind": "end", "id": "h-%d" % pid}) + "\n")

    def analyze(self):
        cfg = SimpleNamespace(root=self.root, run_id=self.run_id, json=None,
                              no_verify=True, ivpm_src=cs.REPO_SRC)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cs.cmd_analyze(cfg)
        return rc, out.getvalue()


class TestAnalyze(_RunDir):
    def test_clean_run_passes(self):
        self.events(1, {"scenario": "S1", "round": 0, "key": "r0", "outcome": "won",
                        "content_ok": True})
        self.events(2, {"scenario": "S1", "round": 0, "key": "r0",
                        "outcome": "adopted", "content_ok": True,
                        "waited_s": 0.0, "budget": {"seconds": 70}})
        rc, out = self.analyze()
        self.assertEqual(rc, 0, out)

    def test_each_violation_is_flagged(self):
        alt = os.path.join(self.rd, "cache", "pkg", "r1~alt~abc")
        os.makedirs(alt)            # a divergent copy with no record -> I6
        os.makedirs(os.path.join(self.rd, "cache", "pkg", "x.staging.1"))  # I5
        self.events(
            1,
            # two winners -> I1
            {"scenario": "S1", "round": 0, "key": "r0", "outcome": "won"},
            # an error -> I2; wrong content -> I3
            {"scenario": "S1", "round": 1, "key": "r1", "outcome": "error",
             "error": "CacheStoreError(...)"},
            {"scenario": "S3", "round": 0, "outcome": "ok", "content_ok": False},
            # half-published -> I4
            {"scenario": "S2", "round": 0, "role": "reader", "method": "none",
             "outcome": "visible", "half_published": True},
            # waited past the budget -> I8
            {"scenario": "S1", "round": 2, "key": "r2", "outcome":
             "adopted_after_wait", "waited_s": 90.0, "budget": {"seconds": 70}},
            # partial tree under churn -> I9
            {"scenario": "S4", "round": 0, "role": "updater", "outcome": "ok",
             "partial": 1, "partial_trees": ["x"]})
        self.events(2, {"scenario": "S1", "round": 0, "key": "r0", "outcome": "won"},
                    {"scenario": "S1", "round": 2, "key": "r2", "outcome": "won"},
                    {"scenario": "S1", "round": 1, "key": "r1", "outcome": "won"})
        rc, out = self.analyze()
        self.assertEqual(rc, 1)
        for inv in ("I1", "I2", "I3", "I4", "I5", "I6", "I8", "I9"):
            line = [l for l in out.splitlines() if l.strip().startswith(inv + " ")]
            self.assertTrue(line and "FAIL" in line[0], "%s not flagged:\n%s"
                            % (inv, out))

    def test_clean_requires_sentinel(self):
        other = tempfile.mkdtemp()
        try:
            cfg = SimpleNamespace(root=os.path.dirname(other),
                                  run_id=os.path.basename(other))
            with self.assertRaises(SystemExit):
                cs.cmd_clean(cfg)
            self.assertTrue(os.path.isdir(other))
        finally:
            shutil.rmtree(other, ignore_errors=True)
        cfg = SimpleNamespace(root=self.root, run_id=self.run_id)
        _quiet(cs.cmd_clean, cfg)
        self.assertFalse(os.path.exists(self.rd))


class TestSchedule(unittest.TestCase):
    def test_identical_from_identical_arguments(self):
        def cfg():
            return SimpleNamespace(start_at=1000.0, scenarios=["S1", "S3"],
                                   rounds=2, s1_period=90, s2_period=145,
                                   s3_period=180, s5_period=60)
        a, b = cs.schedule(cfg()), cs.schedule(cfg())
        self.assertEqual(a, b)
        self.assertEqual([(s.scenario, s.round, s.start) for s in a],
                         [("S1", 0, 1000.0), ("S1", 1, 1090.0),
                          ("S3", 0, 1180.0), ("S3", 1, 1360.0)])

    def test_content_digest_round_trips(self):
        d = tempfile.mkdtemp()
        try:
            cs.write_tree(d, "seed", 12, 100)
            self.assertEqual(cs.tree_digest(d), cs.expected_digest("seed", 12, 100))
            self.assertNotEqual(cs.tree_digest(d),
                                cs.expected_digest("other", 12, 100))
        finally:
            shutil.rmtree(d)


class TestLocalRun(unittest.TestCase):
    """One host, two workers, one short S1 and S5 round each: end to end."""

    def test_run_then_analyze(self):
        root = tempfile.mkdtemp()
        try:
            argv = ["run", "--root", root, "--run-id", "local",
                    "--host-index", "0", "--hosts", "1", "--workers", "2",
                    "--start-at", str(time.time() + 2), "--scenario", "S1,S5",
                    "--rounds", "1", "--s1-period", "4", "--lead", "1.5",
                    "--prime", "0.5", "--s5-period", "4", "--files", "5"]
            self.assertEqual(_quiet(cs.main, argv), 0)
            rc = _quiet(cs.main, ["analyze", "--root", root, "--run-id", "local"])
            self.assertEqual(rc, 0)
            evs = cs.load_events(os.path.join(root, "local"))
            s1 = [e for e in evs if e.get("scenario") == "S1"
                  and e.get("kind") == "result"]
            self.assertEqual(sorted(e["outcome"] for e in s1)[-1], "won")
            self.assertEqual(len(s1), 2)
        finally:
            cs.make_writable_and_remove(root)


if __name__ == "__main__":
    unittest.main()
