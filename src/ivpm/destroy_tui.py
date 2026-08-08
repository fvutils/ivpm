#****************************************************************************
#* destroy_tui.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
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
destroy_tui.py — presentation for `ivpm destroy`.

The FRONT-END owns all formatting. Sources return structured RemovalSafety /
SafetyReason DATA; this module maps each reason ``kind`` to a human label and
renders it at the active verbosity. An unrecognized ``kind`` (from an extension
source) falls back to its ``label`` or the raw ``kind``, so it still presents
sensibly with no change here.
"""
from .tui_theme import make_console
import sys
import threading
import time
from typing import Dict, List

from .pkg_remove import SafetyLevel, RemoveOutcome, RemoveProgressListener
from .tui_theme import S_PLACEHOLDER, S_SECONDARY


# Stable kind -> human label. Extension kinds fall back to reason.label / kind.
_KIND_LABEL = {
    "modified":     "modified",
    "untracked":    "untracked",
    "unpushed":     "unpushed",
    "stash":        "stashed",
    "local-branch": "local-only branch",
    "patch-drift":  "patched-tree drift",
    "unverifiable": "could not verify",
    "symlink":      "symlink",
}

# kind -> noun used when reporting a count.
_KIND_NOUN = {
    "unpushed": "commit",
    "stash":    "stash entry",
}


def _label(reason):
    return _KIND_LABEL.get(reason.kind, reason.label or reason.kind)


def _reason_summary(reason):
    """One-line summary of a single reason at normal verbosity (no items)."""
    label = _label(reason)
    if reason.kind == "local-branch":
        br = reason.data.get("branch")
        return "%s%s" % (label, " (%s)" % br if br else "")
    if reason.kind in ("patch-drift", "unverifiable"):
        if reason.label and reason.kind == "unverifiable":
            return "%s — %s" % (label, reason.label)
        return label
    n = reason.count if reason.count is not None else len(reason.items)
    noun = _KIND_NOUN.get(reason.kind, "file")
    extra = ""
    if reason.kind == "unpushed" and reason.data.get("upstream"):
        extra = " ahead of %s" % reason.data["upstream"]
    if n:
        plural = "" if n == 1 else "s"
        return "%s: %d %s%s%s" % (label, n, noun, plural, extra)
    return label


def _emit_items(reason, out, indent="                 "):
    for it in reason.items:
        out.append("%s%s" % (indent, it))


def render_blocking(report, verbose=0, out=None):
    """Render the blocking report. Returns the text (and appends to *out* if
    a list is given)."""
    lines = []
    blocked = [(n, v) for n, v in report.gate.items()
               if v.level == SafetyLevel.BLOCKED]
    unverifiable = [(n, v) for n, v in report.gate.items()
                    if v.level == SafetyLevel.UNVERIFIABLE]

    # Stable, readable ordering: root last, otherwise by name.
    blocked.sort(key=lambda nv: (nv[0] == "<root>", nv[0]))

    lines.append(
        "ivpm destroy: refusing to remove — %d package(s) hold local work:"
        % len(blocked))
    lines.append("")
    width = max((len(n) for n, _ in blocked), default=0)
    for name, verdict in blocked:
        summary = "; ".join(_reason_summary(r) for r in verdict.reasons)
        lines.append("  %-*s  %s" % (width, name, summary))
        if verbose:
            multi = len([r for r in verdict.reasons if r.items]) > 1
            for r in verdict.reasons:
                if not r.items:
                    continue
                # With several reasons, label each group; the summary line
                # already names a single reason, so don't repeat it.
                if multi:
                    lines.append("  %-*s    %s:" % (width, "", _label(r)))
                    _emit_items(r, lines, indent="  " + " " * (width + 6))
                else:
                    _emit_items(r, lines, indent="  " + " " * (width + 4))

    if unverifiable:
        lines.append("")
        lines.append("Could not verify (would be removed unless --paranoid):")
        for name, verdict in sorted(unverifiable):
            why = verdict.reasons[0].label if verdict.reasons else ""
            lines.append("  %s  %s" % (name, why or "cleanliness unknown"))

    lines.append("")
    lines.append("No files were removed. Re-run with --force to delete anyway, "
                 "or push/commit the")
    lines.append("work above first. Use 'ivpm status' to inspect details.")

    text = "\n".join(lines)
    if out is not None:
        out.append(text)
    return text


def render_dry_run(report, verbose=0):
    """Render the planned removals for --dry-run."""
    lines = []
    lines.append("ivpm destroy (--dry-run): would remove %d import(s) under %s"
                 % (len(report.results), report.deps_dir))
    if report.mode == "full":
        lines.append("  ...and the root project tree at %s" % report.target)
    lines.append("")
    for r in sorted(report.results, key=lambda r: r.name):
        lines.append("  %-20s %-8s %s" % (r.name, r.removal, r.path))
    lines.append("")
    note = ("the venv and lock/state would also be removed; "
            "the root project is kept" if report.mode == "deps-only"
            else "the venv, lock/state, deps dir, and root tree would be removed")
    lines.append("Note: %s." % note)
    return "\n".join(lines)


def render_summary(report):
    """Render the post-teardown summary."""
    removed = [r for r in report.results if r.outcome == RemoveOutcome.REMOVED]
    manual  = [r for r in report.results if r.outcome == RemoveOutcome.MANUAL]
    skipped = [r for r in report.results
               if r.outcome == RemoveOutcome.SKIPPED]
    lines = []
    scope = ("workspace %s" % report.target if report.mode == "full"
             else "imports under %s" % report.deps_dir)
    lines.append("ivpm destroy: removed %s" % scope)
    lines.append("  %d removed, %d skipped, %d need manual follow-up"
                 % (len(removed), len(skipped), len(manual)))
    for r in manual:
        if r.error:
            lines.append("  ! %s: %s" % (r.name, r.error))
        for step in r.next_steps:
            lines.append("      %s" % step)
    return "\n".join(lines)


# Verdict / outcome icons for the live tables.
_GATE_ICON = {
    SafetyLevel.SAFE:         ("✓",  "green",      "safe"),
    SafetyLevel.UNVERIFIABLE: ("?",  "yellow",     "unverifiable"),
    SafetyLevel.BLOCKED:      ("✗",  "bold red",   "blocked"),
}

_REMOVE_ICON = {
    RemoveOutcome.REMOVED: ("✓",  "green",    "removed"),
    RemoveOutcome.SKIPPED: ("—",  S_SECONDARY, "skipped"),
    RemoveOutcome.MANUAL:  ("!",  "bold red", "manual"),
    RemoveOutcome.BLOCKED: ("✗",  "bold red", "blocked"),
}


# ---------------------------------------------------------------------------
# Transcript (plain-text / non-TTY) TUI
# ---------------------------------------------------------------------------

class TranscriptDestroyTUI(RemoveProgressListener):
    """Plain-text progress: concise per-package lines during the parallel gate
    and teardown, then the textual reports. Used for non-TTY / --no-rich."""

    def __init__(self, verbose: int = 0):
        self.verbose = verbose
        self._lock = threading.Lock()

    # ── lifecycle (phase banners) ────────────────────────────────────────
    def gate_begin(self):
        if self.verbose:
            print("Checking packages for local work...")

    def gate_end(self):
        pass

    def teardown_begin(self):
        print("Removing packages...")

    def teardown_end(self):
        pass

    # ── RemoveProgressListener ───────────────────────────────────────────
    def on_gate_start(self, name):
        pass

    def on_gate_result(self, name, safety):
        if not self.verbose:
            return
        icon, _, word = _GATE_ICON.get(safety.level, ("?", "", "?"))
        with self._lock:
            print("  %s %-28s %s" % (icon, name, word))
            sys.stdout.flush()

    def on_remove_start(self, name):
        pass

    def on_remove_result(self, result):
        icon, _, word = _REMOVE_ICON.get(result.outcome, ("?", "", "?"))
        with self._lock:
            print("  %s %-28s %s" % (icon, result.name, word))
            sys.stdout.flush()

    # ── textual reports (front-end presentation) ─────────────────────────
    def show_blocking(self, report, verbose=0):
        print(render_blocking(report, verbose=verbose), file=sys.stderr)

    def show_dry_run(self, report, verbose=0):
        print(render_dry_run(report, verbose=verbose))

    def show_summary(self, report):
        print(render_summary(report))


# ---------------------------------------------------------------------------
# Rich TUI
# ---------------------------------------------------------------------------

class RichDestroyTUI(RemoveProgressListener):
    """Rich live display: a spinner table for the gate phase and one for the
    teardown phase, updating as parallel work completes."""

    def __init__(self, verbose: int = 0):
        self.console = make_console()
        self.verbose = verbose
        self._live = None
        self._lock = threading.Lock()
        self._phase = None                       # "gate" | "teardown"
        self._states: Dict[str, dict] = {}
        self._order: List[str] = []

    # ── lifecycle ────────────────────────────────────────────────────────
    def _start(self, phase):
        from rich.live import Live
        self._phase = phase
        self._states = {}
        self._order = []
        self._live = Live(self._build_table(), console=self.console,
                          refresh_per_second=12)
        self._live.start()

    def _stop(self):
        if self._live:
            with self._lock:
                self._live.update(self._build_table(spinner=False))
            self._live.stop()
            self._live = None

    def gate_begin(self):
        self._start("gate")

    def gate_end(self):
        self._stop()

    def teardown_begin(self):
        self._start("teardown")

    def teardown_end(self):
        self._stop()

    # ── RemoveProgressListener ───────────────────────────────────────────
    def _begin(self, name):
        with self._lock:
            self._states[name] = {"start": time.time(), "done": False, "info": None}
            self._order.append(name)
            self._refresh()

    def _finish(self, name, info):
        with self._lock:
            st = self._states.get(name)
            if st:
                st["done"] = True
                st["info"] = info
                st["dur"] = time.time() - st["start"]
            self._refresh()

    def on_gate_start(self, name):
        self._begin(name)

    def on_gate_result(self, name, safety):
        self._finish(name, safety)

    def on_remove_start(self, name):
        self._begin(name)

    def on_remove_result(self, result):
        self._finish(result.name, result)

    # ── table building ───────────────────────────────────────────────────
    def _build_table(self, spinner=True):
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text

        verb = "Checking" if self._phase == "gate" else "Removing"
        tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        tbl.add_column("", width=2, no_wrap=True)
        tbl.add_column(verb, style="bold", no_wrap=True)
        tbl.add_column("Status", no_wrap=True)
        tbl.add_column("Time", no_wrap=True, style=S_SECONDARY)

        for name in self._order:
            st = self._states[name]
            if not st["done"]:
                marker = (Spinner("dots", style="cyan") if spinner
                          else Text("…", style=S_PLACEHOLDER))
                tbl.add_row(marker, Text(name), Text(""), Text(""))
                continue
            dur = "%.1fs" % st["dur"] if "dur" in st else ""
            if self._phase == "gate":
                icon, style, word = _GATE_ICON.get(
                    st["info"].level, ("?", S_SECONDARY, "?"))
                status = word
            else:
                icon, style, word = _REMOVE_ICON.get(
                    st["info"].outcome, ("?", S_SECONDARY, "?"))
                status = "%s (%s)" % (word, st["info"].removal)
            tbl.add_row(Text(icon, style=style), Text(name),
                        Text(status, style=style), Text(dur))
        return tbl

    def _refresh(self):
        if self._live:
            self._live.update(self._build_table(spinner=True))

    # ── textual reports ──────────────────────────────────────────────────
    def show_blocking(self, report, verbose=0):
        from rich.panel import Panel
        self.console.print(Panel(
            render_blocking(report, verbose=verbose),
            title="destroy — blocked", border_style="red"))

    def show_dry_run(self, report, verbose=0):
        self.console.print(render_dry_run(report, verbose=verbose))

    def show_summary(self, report):
        from rich.panel import Panel
        has_manual = any(r.outcome == RemoveOutcome.MANUAL for r in report.results)
        self.console.print(Panel(
            render_summary(report), title="destroy",
            border_style="red" if has_manual else "green"))


def create_destroy_tui(args) -> object:
    """Return the appropriate destroy TUI based on terminal and args."""
    no_rich = getattr(args, "no_rich", False)
    verbose = getattr(args, "verbose", 0)
    use_rich = not no_rich and sys.stdout.isatty()
    if use_rich:
        return RichDestroyTUI(verbose=verbose)
    return TranscriptDestroyTUI(verbose=verbose)
