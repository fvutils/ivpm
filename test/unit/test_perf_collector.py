#****************************************************************************
#* test_perf_collector.py
#*
#* Unit tests for the performance-span collector (perf.py).  Pure: no
#* dependency on the update flow, the TUI, or the filesystem.
#****************************************************************************
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.perf import PerfCollector, Span


class TestPerfCollector(unittest.TestCase):

    def test_nested_spans_parent_link(self):
        """Nested span() on one thread links child.parent_id to parent.span_id
        and to_tree() reproduces the nesting."""
        perf = PerfCollector()
        with perf.span("parent") as p:
            with perf.span("child") as c:
                self.assertEqual(c.parent_id, p.span_id)
                with perf.span("grandchild") as g:
                    self.assertEqual(g.parent_id, c.span_id)

        roots = perf.to_tree()
        self.assertEqual(len(roots), 1)
        root = roots[0]
        self.assertEqual(root["span"].category, "parent")
        self.assertEqual(len(root["children"]), 1)
        child = root["children"][0]
        self.assertEqual(child["span"].category, "child")
        self.assertEqual(len(child["children"]), 1)
        self.assertEqual(child["children"][0]["span"].category, "grandchild")

    def test_siblings_ordered_by_start(self):
        perf = PerfCollector()
        with perf.span("root"):
            with perf.span("a"):
                pass
            with perf.span("b"):
                pass
        root = perf.to_tree()[0]
        cats = [n["span"].category for n in root["children"]]
        self.assertEqual(cats, ["a", "b"])

    def test_cross_thread_handoff(self):
        """A parent opened on thread A links to a child opened on thread B when
        the parent_id is handed across explicitly (the fetch->package case)."""
        perf = PerfCollector()
        parent = perf.open_span("fetch")
        parent_id = perf.current_parent_id()
        self.assertEqual(parent_id, parent.span_id)

        captured = {}

        def worker():
            # A fresh thread's stack is empty; without the explicit parent_id
            # the child would orphan.
            self.assertIsNone(perf.current_parent_id())
            with perf.span("git.clone", package="pkg",
                           parent_id=parent_id) as child:
                captured["child_parent"] = child.parent_id
                # A span nested *within* the worker inherits via the local stack.
                with perf.span("git.submodule", package="pkg") as sub:
                    captured["sub_parent"] = sub.parent_id
                    captured["child_id"] = child.span_id

        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(worker).result()
        perf.close_span(parent)

        self.assertEqual(captured["child_parent"], parent.span_id)
        self.assertEqual(captured["sub_parent"], captured["child_id"])

        # Whole tree reconstructs with no orphans.
        roots = perf.to_tree()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["span"].category, "fetch")

    def test_concurrent_durations_not_cross_contaminated(self):
        """The property the old scalar-slot timer violates: N threads each time
        a distinct interval and every recorded duration matches its own sleep,
        with no cross-package contamination."""
        perf = PerfCollector()
        sleeps = {"p0": 0.05, "p1": 0.15, "p2": 0.25, "p3": 0.10}

        def load(name, secs):
            with perf.span("fetch.pkg", package=name):
                time.sleep(secs)

        threads = [threading.Thread(target=load, args=(n, s))
                   for n, s in sleeps.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        by_pkg = {s.package: s.duration
                  for s in perf.spans if s.category == "fetch.pkg"}
        self.assertEqual(set(by_pkg.keys()), set(sleeps.keys()))
        for name, expected in sleeps.items():
            # Generous tolerance for scheduler jitter, but tight enough that a
            # cross-contaminated duration (e.g. p0 reporting p2's 0.25s) fails.
            self.assertAlmostEqual(by_pkg[name], expected, delta=0.05,
                                   msg="duration for %s mis-attributed" % name)

    def test_exception_still_closes_span(self):
        perf = PerfCollector()

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with perf.span("risky") as s:
                sid = s.span_id
                raise Boom()

        recorded = [sp for sp in perf.spans if sp.span_id == sid]
        self.assertEqual(len(recorded), 1)
        self.assertIsNotNone(recorded[0].end)
        self.assertIsNotNone(recorded[0].duration)
        # Stack unwound: a subsequent top-level span has no parent.
        with perf.span("after") as a:
            self.assertIsNone(a.parent_id)

    def test_disabled_is_noop(self):
        perf = PerfCollector(enabled=False)
        with perf.span("parent") as p:
            self.assertEqual(p.span_id, -1)
            with perf.span("child") as c:
                # Body still runs; meta assignment is harmless.
                c.meta["k"] = "v"
        self.assertEqual(perf.spans, [])
        self.assertEqual(perf.to_tree(), [])

    def test_meta_enrichment(self):
        perf = PerfCollector()
        with perf.span("cache.lookup", package="pkg") as s:
            s.meta["state"] = "hit"
        rec = perf.spans[0]
        self.assertEqual(rec.meta["state"], "hit")
        self.assertEqual(rec.package, "pkg")

    def test_orphan_promoted_to_root(self):
        """A span whose parent_id names no recorded span is promoted to a root
        rather than dropped."""
        perf = PerfCollector()
        s = perf.open_span("child", parent_id=9999)
        perf.close_span(s)
        roots = perf.to_tree()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["span"].category, "child")

    def test_to_json_round_trips(self):
        import json
        perf = PerfCollector()
        with perf.span("root"):
            with perf.span("leaf", package="p") as leaf:
                leaf.meta["n"] = 3
        data = perf.to_json(header={"runid": "test-run"})
        # Serializable and reloadable.
        reloaded = json.loads(json.dumps(data))
        self.assertEqual(reloaded["schema"], PerfCollector.SCHEMA)
        self.assertEqual(reloaded["header"]["runid"], "test-run")
        cats = sorted(s["category"] for s in reloaded["spans"])
        self.assertEqual(cats, ["leaf", "root"])
        leaf_dict = next(s for s in reloaded["spans"] if s["category"] == "leaf")
        self.assertEqual(leaf_dict["meta"]["n"], 3)
        self.assertEqual(leaf_dict["package"], "p")

    def test_open_span_duration_positive(self):
        perf = PerfCollector()
        s = perf.open_span("work")
        time.sleep(0.02)
        perf.close_span(s)
        self.assertGreaterEqual(s.duration, 0.02)


if __name__ == "__main__":
    unittest.main()
