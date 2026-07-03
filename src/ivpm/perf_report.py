#****************************************************************************
#* perf_report.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software
#* distributed under the License is distributed on an "AS IS" BASIS,
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#* See the License for the specific language governing permissions and
#* limitations under the License.
#*
#****************************************************************************
"""
Profiler-like analysis and rendering over persisted perf records (perf.py).

Pure post-processing: operates on the record's span dicts, with no dependency
on a live run, the TUI, or the event dispatcher. The same ``render`` drives
both the after-run ``--timing`` display and ``ivpm perf show``.
"""
from typing import Dict, List, Optional


# --- indexing --------------------------------------------------------------

def _dur(span: dict) -> float:
    d = span.get("duration")
    return d if d else 0.0


def _children_map(spans: List[dict]) -> Dict[int, List[dict]]:
    m: Dict[int, List[dict]] = {}
    for s in spans:
        m.setdefault(s.get("parent_id"), []).append(s)
    for kids in m.values():
        kids.sort(key=lambda s: s.get("start", 0.0))
    return m


def _roots(spans: List[dict]) -> List[dict]:
    ids = {s["span_id"] for s in spans}
    roots = [s for s in spans if s.get("parent_id") not in ids]
    roots.sort(key=lambda s: _dur(s), reverse=True)
    return roots


def _root(spans: List[dict]) -> Optional[dict]:
    """The operation root -- prefer the ``update`` span, else the longest."""
    for s in spans:
        if s.get("category") == "update":
            return s
    roots = _roots(spans)
    return roots[0] if roots else None


def _self_time(span: dict, cmap: Dict[int, List[dict]]) -> float:
    kids = cmap.get(span["span_id"], [])
    return max(0.0, _dur(span) - sum(_dur(k) for k in kids))


# --- projections -----------------------------------------------------------

def flat_profile(spans: List[dict]) -> List[dict]:
    """Aggregate by category: total (cumulative duration), self (excluding
    child spans), and count. Sorted by self-time descending -- the analogue of
    cProfile's tottime."""
    cmap = _children_map(spans)
    agg: Dict[str, dict] = {}
    for s in spans:
        cat = s.get("category", "?")
        e = agg.setdefault(cat, {"category": cat, "total": 0.0,
                                 "self": 0.0, "count": 0})
        e["total"] += _dur(s)
        e["self"] += _self_time(s, cmap)
        e["count"] += 1
    rows = list(agg.values())
    rows.sort(key=lambda r: r["self"], reverse=True)
    return rows


def _is_parallel(children: List[dict]) -> bool:
    """True if any two children overlap in time (concurrent execution)."""
    ordered = sorted(children, key=lambda s: s.get("start", 0.0))
    max_end = None
    for c in ordered:
        start = c.get("start", 0.0)
        end = c.get("end") or start
        if max_end is not None and start < max_end - 1e-6:
            return True
        max_end = max(max_end, end) if max_end is not None else end
    return False


def long_pole(spans: List[dict]) -> List[tuple]:
    """The critical path (long pole) as a list of ``(depth, span)``.

    Serial children (disjoint in time -- the global phases) are all on the
    path; parallel children (the fetch phase's packages) contribute only their
    single slowest subtree, since the others finish within its shadow.
    """
    cmap = _children_map(spans)
    root = _root(spans)
    out: List[tuple] = []
    if root is None:
        return out

    def walk(span, depth):
        out.append((depth, span))
        kids = cmap.get(span["span_id"], [])
        if not kids:
            return
        if _is_parallel(kids):
            # Among concurrent children the critical path runs through the one
            # that *finishes last* (max end), not merely the longest -- a
            # package's queue-wait ends before its fetch work, so this correctly
            # follows the fetch work rather than the wait.
            walk(max(kids, key=lambda s: (s.get("end") or 0.0, _dur(s))),
                 depth + 1)
        else:
            for c in sorted(kids, key=lambda s: s.get("start", 0.0)):
                walk(c, depth + 1)

    walk(root, 0)
    return out


def by_package(spans: List[dict]) -> List[dict]:
    """Per-package fetch summary: total, dominant sub-phase, cache-hit,
    queue-wait. Sorted by fetch total descending."""
    cmap = _children_map(spans)
    qwait = {s.get("package"): _dur(s)
             for s in spans if s.get("category") == "pkg.queue_wait"}
    rows = []
    for s in spans:
        if s.get("category") != "fetch.pkg":
            continue
        kids = cmap.get(s["span_id"], [])
        dom = max(kids, key=_dur) if kids else None
        rows.append({
            "package": s.get("package"),
            "total": _dur(s),
            "dominant": (dom.get("category"), _dur(dom)) if dom else (None, 0.0),
            "cache_hit": s.get("meta", {}).get("cache_hit"),
            "queue_wait": qwait.get(s.get("package"), 0.0),
            "error": s.get("meta", {}).get("error", False),
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


# Stable, collision-free glyphs for the waterfall. Categories not listed fall
# back to the upper-cased first letter of their last segment.
_WF_SYMBOLS = {
    "pkg.queue_wait": "·",     # waiting for a worker slot (not work)
    "git.resolve_hash": "h",
    "git.deps_source": "g",
    "git.clone": "C",
    "git.checkout": "K",
    "git.submodule": "S",
    "cache.lookup": "l",
    "cache.store": "T",
    "cache.materialize": "M",
    "patch.apply": "A",
    "pkg.fast_path": "=",      # already-loaded fast path
    "init": "I",
    "depset.resolve": "D",
    "lock.detect_changes": "d",
    "handler.pre_load": "H",
    "venv.create": "V",
    "reqs.assemble": "R",
    "pip.install": "P",
    "envrc.write": "W",
    "lock.write": "w",
}


def _wf_symbol(category: str) -> str:
    if category in _WF_SYMBOLS:
        return _WF_SYMBOLS[category]
    return (category.split(".")[-1][:1] or "?").upper()


def waterfall(spans: List[dict], width: int = 54) -> List[str]:
    """A Gantt-style timeline: one lane per package (plus a ``main`` lane for
    global phases), time on the X axis.

    Only *leaf* spans are drawn -- the actual work -- never the container phases
    (``update``/``fetch``/``handler.*``) that would otherwise paint a solid wall
    across their whole extent. Each package runs on one worker thread, so a
    per-package lane reads far more clearly than a raw thread id. Queue-wait is
    drawn as ``·`` so a package's leading wait is visible before its work.
    """
    timed = [s for s in spans if s.get("start") is not None and s.get("end")]
    if not timed:
        return []
    # Leaves = spans that are nobody's parent (the real operations).
    parent_ids = {s.get("parent_id") for s in spans}
    leaves = [s for s in timed if s["span_id"] not in parent_ids] or timed

    t0 = min(s["start"] for s in leaves)
    t1 = max(s["end"] for s in leaves)
    span_secs = (t1 - t0) or 1.0
    scale = width / span_secs

    def col(t):
        return max(0, min(width - 1, int((t - t0) * scale)))

    lanes: Dict[str, List[dict]] = {}
    for s in leaves:
        lanes.setdefault(s.get("package") or "main", []).append(s)
    # main first, then packages ordered by when they start.
    labels = sorted(lanes, key=lambda L: (
        L != "main", min(x["start"] for x in lanes[L])))

    used = {}
    lines = ["  %-14s %s" % ("", _axis(t1 - t0, width))]
    for L in labels:
        row = [" "] * width
        for s in sorted(lanes[L], key=_dur, reverse=True):
            cat = s.get("category", "?")
            sym = _wf_symbol(cat)
            used[cat] = sym
            a, b = col(s["start"]), col(s["end"])
            for c in range(a, max(a + 1, b)):
                row[c] = sym
        lines.append("  %-14s %s" % (L[:14], "".join(row)))

    # Legend for the glyphs actually used (skip the obvious dot).
    legend = "  ".join(
        "%s=%s" % (sym, cat) for cat, sym in sorted(used.items(),
                                                    key=lambda kv: kv[1])
        if cat != "pkg.queue_wait")
    if legend:
        lines.append("  legend: · = queue-wait   " + legend)
    return lines


def _axis(total_secs: float, width: int) -> str:
    """A minimal time axis: 0 on the left, the total on the right."""
    right = "%.1fs" % total_secs
    left = "0"
    fill = max(0, width - len(left) - len(right))
    return left + ("─" * fill) + right


# --- rendering -------------------------------------------------------------

def _fmt_s(secs: float) -> str:
    return "%.2fs" % (secs or 0.0)


def _bar(frac: float, width: int = 12) -> str:
    n = int(round(frac * width))
    return "█" * n


_ANSI_BOLD = "\033[1m"
_ANSI_UNDERLINE = "\033[4m"
_ANSI_DIM = "\033[2m"
_ANSI_RESET = "\033[0m"


def _auto_bold() -> bool:
    """Bold headings only for an interactive terminal that hasn't opted out."""
    import os
    import sys
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def render(record: dict, full: bool = True, bold: Optional[bool] = None) -> str:
    """Render the four-panel report from a persisted record dict (or the dict
    produced by ``PerfCollector.to_json``).

    ``bold`` controls ANSI-bold headings; ``None`` auto-detects a TTY (so piped
    or captured output stays plain). Bold needs only ANSI, not Rich, and thus
    works wherever the terminal does.
    """
    if bold is None:
        bold = _auto_bold()

    def H(text):
        return (_ANSI_BOLD + text + _ANSI_RESET) if bold else text

    def HDR(text, indent="  "):
        # Column-header rows: underlined so they read as a header and don't
        # blend into the data rows beneath them. The leading indent stays
        # outside the underline.
        if bold:
            return indent + _ANSI_UNDERLINE + text + _ANSI_RESET
        return indent + text

    spans = record.get("spans", [])
    header = record.get("header", {}) or {}
    root = _root(spans)
    total = _dur(root) if root else 0.0

    lines = []
    runid = header.get("runid", "?")
    lines.append(H("ivpm perf  —  run %s" % runid))
    lines.append("─" * 78)
    npkg = sum(1 for s in spans if s.get("category") == "fetch.pkg")
    hits = sum(1 for s in spans if s.get("category") == "fetch.pkg"
               and s.get("meta", {}).get("cache_hit") is True)
    misses = sum(1 for s in spans if s.get("category") == "fetch.pkg"
                 and s.get("meta", {}).get("cache_hit") is False)
    summ = "wall-clock %s   ·   %d packages   ·   cache %d hit / %d miss" % (
        _fmt_s(total), npkg, hits, misses)
    if header.get("max_parallel"):
        summ += "   ·   max_parallel=%s" % header["max_parallel"]
    lines.append(summ)
    lines.append("")

    # LONG POLE
    lines.append(H("LONG POLE  (critical path)"))
    for depth, s in long_pole(spans):
        if s is root:
            continue
        indent = "  " * depth
        frac = (_dur(s) / total) if total else 0.0
        label = s.get("category", "?")
        if s.get("package"):
            label += " [%s]" % s["package"]
        lines.append("  %-38s %7s %4.0f%%  %s" % (
            (indent + label)[:38], _fmt_s(_dur(s)), frac * 100, _bar(frac)))
    lines.append("")

    # HOT SPOTS
    lines.append(H("HOT SPOTS  (time per phase, summed across packages)"))
    lines.append(HDR("%-20s %8s %8s %6s" % (
        "phase", "total", "own", "count")))
    profile = [r for r in flat_profile(spans) if r["category"] != "update"]
    busy = sum(r["self"] for r in profile) or 1.0
    for r in profile:
        lines.append("  %-20s %8s %8s %6d   %s" % (
            r["category"][:20], _fmt_s(r["total"]), _fmt_s(r["self"]),
            r["count"], _bar(r["self"] / busy)))
    lines.append("")

    # PACKAGES
    pkgs = by_package(spans)
    if pkgs:
        lines.append(H("PACKAGES"))
        lines.append(HDR("%-18s %8s  %-22s %-7s %s" % (
            "package", "fetch", "dominant sub-phase", "cache", "queue-wait")))
        for r in pkgs:
            dom_cat, dom_dur = r["dominant"]
            cache = ("HIT" if r["cache_hit"] is True
                     else "MISS" if r["cache_hit"] is False else "—")
            if r["error"]:
                cache = "ERR"
            dom = "%s  %s" % (dom_cat, _fmt_s(dom_dur)) if dom_cat else "—"
            lines.append("  %-18s %8s  %-22s %-7s %s" % (
                (r["package"] or "?")[:18], _fmt_s(r["total"]),
                dom[:22], cache, _fmt_s(r["queue_wait"])))
        lines.append("")

    # WATERFALL
    if full:
        wf = waterfall(spans)
        if wf:
            lines.append(H("WATERFALL  (leaf work; lanes = packages, "
                           "main = global)"))
            lines.extend(wf)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --- Chrome Trace export ---------------------------------------------------

def to_chrome_trace(record: dict) -> dict:
    """Convert a record to Chrome Trace Event Format.

    Each closed span becomes a complete (``ph:"X"``) event; the result loads
    directly in https://ui.perfetto.dev or chrome://tracing for a zoomable
    flamegraph + timeline. Timestamps are microseconds, zero-based at the
    earliest span start.
    """
    spans = [s for s in record.get("spans", [])
             if s.get("start") is not None and s.get("end") is not None]
    events = []
    if spans:
        t0 = min(s["start"] for s in spans)
        for s in spans:
            args = dict(s.get("meta", {}))
            if s.get("package"):
                args["package"] = s["package"]
            events.append({
                "name": s.get("category", "?"),
                "cat": (s.get("category", "").split(".")[0] or "misc"),
                "ph": "X",
                "ts": int((s["start"] - t0) * 1e6),
                "dur": int((_dur(s)) * 1e6),
                "pid": 1,
                "tid": s.get("thread") or 0,
                "args": args,
            })
    return {"traceEvents": events,
            "displayTimeUnit": "ms",
            "otherData": record.get("header", {})}


# --- diff ------------------------------------------------------------------

def diff(rec_a: dict, rec_b: dict) -> dict:
    """Per-category self/total deltas between two records (B relative to A),
    plus new/vanished categories and the total wall-clock change."""
    pa = {r["category"]: r for r in flat_profile(rec_a.get("spans", []))}
    pb = {r["category"]: r for r in flat_profile(rec_b.get("spans", []))}
    cats = sorted(set(pa) | set(pb))
    rows = []
    for c in cats:
        a = pa.get(c)
        b = pb.get(c)
        rows.append({
            "category": c,
            "self_a": a["self"] if a else 0.0,
            "self_b": b["self"] if b else 0.0,
            "self_delta": (b["self"] if b else 0.0) - (a["self"] if a else 0.0),
            "status": ("new" if not a else "gone" if not b else "changed"),
        })
    rows.sort(key=lambda r: abs(r["self_delta"]), reverse=True)
    ra, rb = _root(rec_a.get("spans", [])), _root(rec_b.get("spans", []))
    wall_a = _dur(ra) if ra else 0.0
    wall_b = _dur(rb) if rb else 0.0
    return {"rows": rows, "wall_a": wall_a, "wall_b": wall_b,
            "wall_delta": wall_b - wall_a}


def render_diff(d: dict, runid_a="A", runid_b="B") -> str:
    lines = ["ivpm perf diff  —  %s → %s" % (runid_a, runid_b), "─" * 78]
    lines.append("wall-clock %s → %s   (Δ %+.2fs)" % (
        _fmt_s(d["wall_a"]), _fmt_s(d["wall_b"]), d["wall_delta"]))
    lines.append("")
    lines.append("  %-20s %9s %9s %9s  %s" % (
        "phase", "own A", "own B", "Δ", "status"))
    for r in d["rows"]:
        if abs(r["self_delta"]) < 0.0005 and r["status"] == "changed":
            continue
        lines.append("  %-20s %9s %9s %+8.2fs  %s" % (
            r["category"][:20], _fmt_s(r["self_a"]), _fmt_s(r["self_b"]),
            r["self_delta"], r["status"]))
    return "\n".join(lines).rstrip() + "\n"


def render_list(deps_dir: str) -> str:
    """A newest-first table of available records under deps/.ivpm/."""
    from .perf import list_record_paths, load_record
    paths = list_record_paths(deps_dir)
    if not paths:
        return "No perf records under %s\n" % perf_dir_of(deps_dir)
    lines = ["  %-24s %10s %9s" % ("runid", "wall", "packages")]
    for p in paths:
        try:
            rec = load_record(p)
        except (OSError, ValueError):
            continue
        hdr = rec.get("header", {})
        root = _root(rec.get("spans", []))
        npkg = sum(1 for s in rec.get("spans", [])
                   if s.get("category") == "fetch.pkg")
        lines.append("  %-24s %10s %9d" % (
            hdr.get("runid", "?"), _fmt_s(_dur(root) if root else 0.0), npkg))
    return "\n".join(lines) + "\n"


def perf_dir_of(deps_dir: str) -> str:
    from .perf import perf_dir
    return perf_dir(deps_dir)
