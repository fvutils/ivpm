#****************************************************************************
#* test_perf_report.py
#*
#* Phase-6 tests: the perf_report analysis/rendering over hand-authored
#* records (flat profile, long pole, per-package, Chrome export, diff) and the
#* `ivpm perf` CLI help surface. Pure -- no update run required.
#****************************************************************************
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm import perf_report


def _span(span_id, category, start, end, parent_id=None, package=None, **meta):
    return {
        "span_id": span_id, "parent_id": parent_id, "category": category,
        "package": package, "start": start, "end": end,
        "duration": (end - start) if end is not None else None,
        "thread": 1, "meta": meta,
    }


def _record():
    """A small parallel-fetch record: update -> {init, fetch}; fetch has two
    packages (apkg slow with a clone, bpkg fast) running concurrently."""
    spans = [
        _span(1, "update", 0.0, 10.0),
        _span(2, "init", 0.0, 1.0, parent_id=1),
        _span(3, "fetch", 1.0, 9.0, parent_id=1),
        # apkg: clone-heavy, finishes at 9.0 (the long pole through fetch)
        _span(4, "fetch.pkg", 1.0, 9.0, parent_id=3, package="apkg",
              cache_hit=False),
        _span(6, "git.clone", 1.5, 8.5, parent_id=4, package="apkg"),
        # bpkg: fast cache hit, runs concurrently, finishes early
        _span(5, "fetch.pkg", 1.0, 2.0, parent_id=3, package="bpkg",
              cache_hit=True),
        _span(7, "cache.materialize", 1.1, 1.9, parent_id=5, package="bpkg"),
        # bpkg waited briefly for a slot
        _span(8, "pkg.queue_wait", 1.0, 1.2, parent_id=3, package="bpkg"),
    ]
    return {"schema": 1, "wall_start": 1000.0, "spans": spans,
            "header": {"runid": "testrun", "max_parallel": 2}}


class TestPerfReport(unittest.TestCase):

    def test_flat_profile_self_vs_total(self):
        rows = perf_report.flat_profile(_record()["spans"])
        by_cat = {r["category"]: r for r in rows}
        # git.clone is the biggest self-time hot spot.
        self.assertEqual(rows[0]["category"], "git.clone")
        # fetch's self-time excludes its children (packages run inside it).
        self.assertLess(by_cat["fetch"]["self"], by_cat["fetch"]["total"])
        # update self-time = 10 - (init 1 + fetch 8) = 1.0
        self.assertAlmostEqual(by_cat["update"]["self"], 1.0, places=3)

    def test_long_pole_follows_last_finisher(self):
        pole = perf_report.long_pole(_record()["spans"])
        cats = [s["category"] for _, s in pole]
        # Path: update -> init, fetch -> apkg (finishes last) -> git.clone.
        self.assertIn("fetch", cats)
        self.assertIn("git.clone", cats)
        # The slow package (apkg), not the fast bpkg, is on the pole.
        pkgs = [s.get("package") for _, s in pole if s["category"] == "fetch.pkg"]
        self.assertEqual(pkgs, ["apkg"])

    def test_by_package(self):
        rows = perf_report.by_package(_record()["spans"])
        by = {r["package"]: r for r in rows}
        self.assertEqual(set(by), {"apkg", "bpkg"})
        # Sorted by fetch total desc -> apkg first.
        self.assertEqual(rows[0]["package"], "apkg")
        self.assertEqual(by["apkg"]["dominant"][0], "git.clone")
        self.assertEqual(by["apkg"]["cache_hit"], False)
        self.assertEqual(by["bpkg"]["cache_hit"], True)
        self.assertAlmostEqual(by["bpkg"]["queue_wait"], 0.2, places=3)

    def test_render_has_all_panels(self):
        out = perf_report.render(_record())
        for panel in ("LONG POLE", "HOT SPOTS", "PACKAGES", "WATERFALL"):
            self.assertIn(panel, out)
        self.assertIn("run testrun", out)

    def test_chrome_trace_schema(self):
        trace = perf_report.to_chrome_trace(_record())
        events = trace["traceEvents"]
        self.assertTrue(events)
        for e in events:
            for k in ("name", "ph", "ts", "dur", "pid", "tid"):
                self.assertIn(k, e)
            self.assertEqual(e["ph"], "X")
        # Must be JSON-serializable.
        json.loads(json.dumps(trace))
        # Zero-based timestamps.
        self.assertEqual(min(e["ts"] for e in events), 0)

    def test_diff_reports_regression(self):
        a = _record()
        b = _record()
        # Make git.clone slower in B.
        for s in b["spans"]:
            if s["category"] == "git.clone":
                s["end"] = s["start"] + 12.0
                s["duration"] = 12.0
        d = perf_report.diff(a, b)
        clone = next(r for r in d["rows"] if r["category"] == "git.clone")
        self.assertGreater(clone["self_delta"], 0)
        self.assertEqual(clone["status"], "changed")
        text = perf_report.render_diff(d, "A", "B")
        self.assertIn("git.clone", text)

    def test_diff_new_and_gone(self):
        a = {"schema": 1, "spans": [_span(1, "update", 0, 5),
                                    _span(2, "git.clone", 0, 4, parent_id=1)]}
        b = {"schema": 1, "spans": [_span(1, "update", 0, 5),
                                    _span(2, "cache.materialize", 0, 1, parent_id=1)]}
        d = perf_report.diff(a, b)
        status = {r["category"]: r["status"] for r in d["rows"]}
        self.assertEqual(status["git.clone"], "gone")
        self.assertEqual(status["cache.materialize"], "new")

    def test_empty_record_renders(self):
        out = perf_report.render({"schema": 1, "spans": [], "header": {}})
        self.assertIn("LONG POLE", out)

    def test_waterfall_lanes_by_package_leaves_only(self):
        wf = perf_report.waterfall(_record()["spans"])
        text = "\n".join(wf)
        # Lanes are labeled by package (+ main), not raw thread ids.
        self.assertTrue(any(line.strip().startswith("main") for line in wf))
        self.assertTrue(any("apkg" in line for line in wf))
        self.assertTrue(any("bpkg" in line for line in wf))
        self.assertNotIn("T1", text)
        # Container spans (update/fetch/fetch.pkg) are never drawn as bars:
        # the glyph for 'fetch'/'update' ('F'/'U') must not tile a lane. The
        # apkg lane should show the clone glyph 'C', not a wall of one letter.
        apkg_line = next(l for l in wf if "apkg" in l)
        self.assertIn("C", apkg_line)          # git.clone leaf is drawn
        # A legend is present mapping glyphs to categories.
        self.assertTrue(any("legend:" in l for l in wf))
        self.assertIn("git.clone", text)

    def test_waterfall_queue_wait_glyph(self):
        wf = perf_report.waterfall(_record()["spans"])
        # bpkg had a queue-wait; it renders as the '·' glyph on bpkg's lane.
        bpkg_line = next(l for l in wf if "bpkg" in l)
        self.assertIn("·", bpkg_line)

    def test_render_bold_headings(self):
        rec = _record()
        bold = perf_report.render(rec, bold=True)
        plain = perf_report.render(rec, bold=False)
        self.assertIn("\033[1m", bold)
        self.assertNotIn("\033[1m", plain)
        # Content is identical modulo the ANSI codes.
        self.assertIn("LONG POLE", plain)
        self.assertIn("LONG POLE", bold.replace("\033[1m", "").replace("\033[0m", ""))

    def test_column_headers_underlined_distinct_from_panel_title(self):
        """Panel titles are bold; column-header rows are underlined -- so the
        header ('phase ... count') stands out from the data rows beneath it."""
        bold = perf_report.render(_record(), bold=True)
        # The 'phase' column header carries the underline code, not bold.
        hdr_line = next(l for l in bold.splitlines() if "phase" in l and "own" in l)
        self.assertIn("\033[4m", hdr_line)
        # Its leading indent is outside the underline (no code at col 0).
        self.assertTrue(hdr_line.startswith("  \033[4m"))
        # Plain output leaves the header unstyled but present.
        plain = perf_report.render(_record(), bold=False)
        self.assertIn("phase", plain)
        self.assertNotIn("\033[4m", plain)


class TestPerfCliHelp(unittest.TestCase):

    def test_perf_subcommands_in_help(self):
        from ivpm.__main__ import get_parser
        parser = get_parser()
        # argparse exits on --help; capture the subcommand help text instead.
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                parser.parse_args(["perf", "--help"])
        except SystemExit:
            pass
        help_txt = buf.getvalue()
        for sub in ("list", "show", "export", "diff"):
            self.assertIn(sub, help_txt)


if __name__ == "__main__":
    unittest.main()
