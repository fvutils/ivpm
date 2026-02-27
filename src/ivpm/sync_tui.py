#****************************************************************************
#* sync_tui.py
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
TUI renderers for `ivpm sync`.

RichSyncTUI       — coloured live display + final table using Rich (TTY).
TranscriptSyncTUI — plain-text fallback (non-TTY / --no-rich).

Both implement SyncProgressListener so they can receive per-package
notifications during a parallel sync, then render the final results table.
"""
import sys
import time
from typing import Dict, List, Optional

from .pkg_sync import PkgSyncResult, SyncOutcome, SyncProgressListener

# (icon, rich-style) per outcome
_ICONS = {
    SyncOutcome.SYNCED:              ("↑",  "bold green"),
    SyncOutcome.UP_TO_DATE:          ("=",  "dim"),
    SyncOutcome.CONFLICT:            ("✗",  "bold red"),
    SyncOutcome.DIRTY:               ("✎",  "bold yellow"),
    SyncOutcome.AHEAD:               ("↑!", "bold yellow"),
    SyncOutcome.ERROR:               ("!",  "bold red"),
    SyncOutcome.SKIPPED:             ("—",  "dim"),
    SyncOutcome.DRY_WOULD_SYNC:      ("→",  "cyan"),
    SyncOutcome.DRY_WOULD_CONFLICT:  ("?",  "yellow"),
    SyncOutcome.DRY_DIRTY:           ("?",  "yellow"),
}

_DRY_OUTCOMES = {
    SyncOutcome.DRY_WOULD_SYNC,
    SyncOutcome.DRY_WOULD_CONFLICT,
    SyncOutcome.DRY_DIRTY,
}

_ATTENTION_OUTCOMES = {
    SyncOutcome.CONFLICT,
    SyncOutcome.DIRTY,
    SyncOutcome.AHEAD,
    SyncOutcome.ERROR,
    SyncOutcome.DRY_WOULD_CONFLICT,
    SyncOutcome.DRY_DIRTY,
}


def _dur(state: Optional[dict]) -> str:
    """Format duration from a progress state dict, or return empty string."""
    if state and "duration" in state:
        return "%.1fs" % state["duration"]
    return ""


def _row_status(r: PkgSyncResult):
    """Return (delta_Text, status_Text) for a completed result row."""
    from rich.text import Text
    if r.outcome == SyncOutcome.SYNCED:
        status = Text("%s→%s" % (r.old_commit or "?", r.new_commit or "?"), style="green")
        delta  = Text("↓%d" % r.commits_behind, style="green") if r.commits_behind else Text("")
    elif r.outcome == SyncOutcome.UP_TO_DATE:
        status = Text("up-to-date  %s" % (r.old_commit or ""), style="dim")
        delta  = Text("=", style="dim")
    elif r.outcome == SyncOutcome.CONFLICT:
        status = Text("conflict  %s" % (r.old_commit or ""), style="bold red")
        delta  = Text("↓%d" % r.commits_behind, style="red") if r.commits_behind else Text("")
    elif r.outcome == SyncOutcome.DIRTY:
        status = Text("dirty  %s" % (r.old_commit or ""), style="bold yellow")
        delta  = Text("")
    elif r.outcome == SyncOutcome.AHEAD:
        ahead_str = "↑%d" % r.commits_ahead if r.commits_ahead else "ahead"
        status = Text("ahead  %s" % (r.old_commit or ""), style="bold yellow")
        delta  = Text(ahead_str, style="bold yellow")
    elif r.outcome == SyncOutcome.ERROR:
        status = Text(r.error or "error", style="bold red")
        delta  = Text("")
    elif r.outcome in _DRY_OUTCOMES:
        status = Text("%s  %s" % (r.outcome.value, r.old_commit or ""), style="cyan")
        delta  = Text("↓%d" % r.commits_behind, style="cyan") if r.commits_behind else Text("")
    else:  # SKIPPED
        status = Text(r.skipped_reason or "skipped", style="dim")
        delta  = Text("")
    return delta, status


# ---------------------------------------------------------------------------
# Rich TUI
# ---------------------------------------------------------------------------

class RichSyncTUI(SyncProgressListener):
    """Rich-based TUI: single live table that becomes the final output."""

    def __init__(self):
        from rich.console import Console
        self.console = Console()
        self._live = None
        self._pkg_states: Dict[str, dict] = {}   # name → {start, done, result}
        self._order: List[str] = []

    # ── SyncProgressListener ─────────────────────────────────────────────

    def on_pkg_start(self, name: str) -> None:
        self._pkg_states[name] = {"start": time.time(), "done": False, "result": None}
        self._order.append(name)
        self._refresh()

    def on_pkg_result(self, result: PkgSyncResult) -> None:
        state = self._pkg_states.get(result.name)
        if state:
            state["done"] = True
            state["result"] = result
            state["duration"] = time.time() - state["start"]
        self._refresh()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        from rich.live import Live
        from rich.table import Table
        tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        self._live = Live(tbl, console=self.console, refresh_per_second=10)
        self._live.start()

    def stop(self):
        if self._live:
            self._live.stop()
            self._live = None

    def _build_table(self, spinner=True):
        """Build the unified package table (used for both live and final display)."""
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text

        tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        tbl.add_column("",        width=3,  no_wrap=True)
        tbl.add_column("Package", style="bold", no_wrap=True)
        tbl.add_column("Branch",  no_wrap=True)
        tbl.add_column("Status",  no_wrap=True)
        tbl.add_column("Δ",       no_wrap=True)
        tbl.add_column("Time",    no_wrap=True)

        for name in self._order:
            state = self._pkg_states[name]
            if not state["done"]:
                if spinner:
                    marker = Spinner("dots", style="bold cyan")
                else:
                    marker = Text("…", style="dim")
                tbl.add_row(marker, Text(name), Text(""), Text("", style="dim"),
                            Text(""), Text(""))
            else:
                r = state["result"]
                # Skip pypi in the live/final table
                if r.src_type == "pypi":
                    continue
                icon, style = _ICONS.get(r.outcome, ("?", "dim"))
                marker  = Text(icon, style=style)
                branch  = Text(r.branch or "—", style="" if r.branch else "dim")
                dur     = Text(_dur(state), style="dim")
                delta, status = _row_status(r)
                tbl.add_row(marker, Text(name), branch, status, delta, dur)

        return tbl

    def _refresh(self):
        if self._live:
            self._live.update(self._build_table(spinner=True))

    # ── Final render ──────────────────────────────────────────────────────

    def render(self, results: List[PkgSyncResult], dry_run: bool = False):
        from rich.text import Text
        from rich.panel import Panel

        # Final refresh with complete data, then stop the live display.
        if self._live:
            self._live.update(self._build_table(spinner=False))
            self._live.stop()
            self._live = None

        counts = {o: 0 for o in SyncOutcome}
        pypi_count = 0
        attention_items = []
        for r in results:
            counts[r.outcome] += 1
            if r.src_type == "pypi":
                pypi_count += 1
                continue
            if r.outcome in _ATTENTION_OUTCOMES:
                attention_items.append(r)

        # ── Attention panel ───────────────────────────────────────────────
        if attention_items:
            lines = []
            for r in attention_items:
                lines.append("  %s  [%s]" % (r.name, r.outcome.value))

                if r.outcome == SyncOutcome.CONFLICT:
                    if r.conflict_files:
                        lines.append("    Conflicting files:")
                        for f in r.conflict_files:
                            lines.append("      %s" % f)
                    for step in r.next_steps:
                        lines.append("    %s" % step)

                elif r.outcome in (SyncOutcome.DIRTY, SyncOutcome.DRY_DIRTY):
                    if r.dirty_files:
                        lines.append("    Modified files:")
                        for f in r.dirty_files:
                            lines.append("      %s" % f)

                elif r.outcome == SyncOutcome.AHEAD:
                    behind_note = (
                        " (also ↓%d behind — diverged)" % r.commits_behind
                        if r.commits_behind else ""
                    )
                    lines.append("    ↑%d local commit(s) not on origin%s"
                                 % (r.commits_ahead or 0, behind_note))

                elif r.outcome == SyncOutcome.ERROR:
                    lines.append("    %s" % (r.error or ""))

                elif r.outcome == SyncOutcome.DRY_WOULD_CONFLICT:
                    lines.append("    diverged — git diff HEAD..origin/%s"
                                 % (r.branch or ""))

                lines.append("")

            has_error = any(r.outcome in (SyncOutcome.CONFLICT, SyncOutcome.ERROR)
                            for r in attention_items)
            border = "red" if has_error else "yellow"
            self.console.print(Panel(
                "\n".join(lines).rstrip(),
                title="Attention", border_style=border,
            ))

        # ── Summary panel ─────────────────────────────────────────────────
        parts = []
        if dry_run:
            if counts[SyncOutcome.DRY_WOULD_SYNC]:
                parts.append("%d would sync" % counts[SyncOutcome.DRY_WOULD_SYNC])
            if counts[SyncOutcome.DRY_WOULD_CONFLICT]:
                parts.append("%d would conflict" % counts[SyncOutcome.DRY_WOULD_CONFLICT])
            if counts[SyncOutcome.DRY_DIRTY]:
                parts.append("%d dirty" % counts[SyncOutcome.DRY_DIRTY])
            if counts[SyncOutcome.UP_TO_DATE]:
                parts.append("%d up-to-date" % counts[SyncOutcome.UP_TO_DATE])
        else:
            if counts[SyncOutcome.SYNCED]:
                parts.append("%d synced" % counts[SyncOutcome.SYNCED])
            if counts[SyncOutcome.UP_TO_DATE]:
                parts.append("%d up-to-date" % counts[SyncOutcome.UP_TO_DATE])
            if counts[SyncOutcome.CONFLICT]:
                parts.append("%d conflict" % counts[SyncOutcome.CONFLICT])
            if counts[SyncOutcome.DIRTY]:
                parts.append("%d dirty" % counts[SyncOutcome.DIRTY])
            if counts[SyncOutcome.AHEAD]:
                parts.append("%d ahead" % counts[SyncOutcome.AHEAD])
            if counts[SyncOutcome.ERROR]:
                parts.append("%d error" % counts[SyncOutcome.ERROR])
        if counts[SyncOutcome.SKIPPED]:
            parts.append("%d skipped" % counts[SyncOutcome.SKIPPED])
        if pypi_count:
            parts.append("%d pypi (hidden)" % pypi_count)

        summary = " · ".join(parts) if parts else "nothing to do"
        if dry_run:
            summary += "\n[dry-run mode — no changes were made]"

        if counts[SyncOutcome.ERROR] or counts[SyncOutcome.CONFLICT]:
            border = "red"
        elif (counts[SyncOutcome.DIRTY] or counts[SyncOutcome.AHEAD]
              or counts[SyncOutcome.DRY_WOULD_CONFLICT]
              or counts[SyncOutcome.DRY_DIRTY]):
            border = "yellow"
        else:
            border = "green"

        self.console.print(Panel(summary, title="Sync", border_style=border))


# ---------------------------------------------------------------------------
# Transcript (plain-text) TUI
# ---------------------------------------------------------------------------

class TranscriptSyncTUI(SyncProgressListener):
    """Plain-text TUI: >> / << lines during sync, full table after."""

    # ── SyncProgressListener ─────────────────────────────────────────────

    def on_pkg_start(self, name: str) -> None:
        print(">> %s" % name)
        sys.stdout.flush()

    def on_pkg_result(self, result: PkgSyncResult) -> None:
        icon, _ = _ICONS.get(result.outcome, ("?", ""))
        print("<< %s  %s  %s" % (result.name, icon, result.outcome.value))
        sys.stdout.flush()

    # ── Lifecycle (no-ops for transcript) ────────────────────────────────

    def start(self):
        pass

    def stop(self):
        pass

    # ── Final render ──────────────────────────────────────────────────────

    def render(self, results: List[PkgSyncResult], dry_run: bool = False):
        # Progress lines (>> / <<) already showed each package during sync.
        # Only print details for packages needing attention, then a summary.
        pypi_count = 0
        counts = {o: 0 for o in SyncOutcome}
        for r in results:
            counts[r.outcome] += 1
            if r.src_type == "pypi":
                pypi_count += 1
                continue
            if r.outcome not in _ATTENTION_OUTCOMES:
                continue

            icon, _ = _ICONS.get(r.outcome, ("?", ""))
            print("  %s  %-30s  %-15s  %s" % (icon, r.name, r.branch or "—",
                                                r.outcome.value.upper()))
            if r.outcome == SyncOutcome.CONFLICT:
                if r.conflict_files:
                    print("     Conflicting files:")
                    for f in r.conflict_files:
                        print("       %s" % f)
                for step in r.next_steps:
                    print("     %s" % step)
            elif r.outcome in (SyncOutcome.DIRTY, SyncOutcome.DRY_DIRTY):
                if r.dirty_files:
                    print("     Modified files:")
                    for f in r.dirty_files:
                        print("       %s" % f)
            elif r.outcome == SyncOutcome.ERROR:
                print("     %s" % (r.error or ""))
            elif r.outcome == SyncOutcome.DRY_WOULD_CONFLICT:
                print("     diverged — git diff HEAD..origin/%s" % (r.branch or ""))

        # ── Summary line ─────────────────────────────────────────────────
        parts = []
        if dry_run:
            if counts[SyncOutcome.DRY_WOULD_SYNC]:
                parts.append("%d would sync" % counts[SyncOutcome.DRY_WOULD_SYNC])
            if counts[SyncOutcome.DRY_WOULD_CONFLICT]:
                parts.append("%d would conflict" % counts[SyncOutcome.DRY_WOULD_CONFLICT])
            if counts[SyncOutcome.DRY_DIRTY]:
                parts.append("%d dirty" % counts[SyncOutcome.DRY_DIRTY])
            if counts[SyncOutcome.UP_TO_DATE]:
                parts.append("%d up-to-date" % counts[SyncOutcome.UP_TO_DATE])
        else:
            if counts[SyncOutcome.SYNCED]:
                parts.append("%d synced" % counts[SyncOutcome.SYNCED])
            if counts[SyncOutcome.UP_TO_DATE]:
                parts.append("%d up-to-date" % counts[SyncOutcome.UP_TO_DATE])
            if counts[SyncOutcome.CONFLICT]:
                parts.append("%d conflict" % counts[SyncOutcome.CONFLICT])
            if counts[SyncOutcome.DIRTY]:
                parts.append("%d dirty" % counts[SyncOutcome.DIRTY])
            if counts[SyncOutcome.AHEAD]:
                parts.append("%d ahead" % counts[SyncOutcome.AHEAD])
            if counts[SyncOutcome.ERROR]:
                parts.append("%d error" % counts[SyncOutcome.ERROR])
        if counts[SyncOutcome.SKIPPED]:
            parts.append("%d skipped" % counts[SyncOutcome.SKIPPED])
        if pypi_count:
            parts.append("%d pypi (hidden)" % pypi_count)

        print("")
        print("Sync: " + (" · ".join(parts) if parts else "nothing to do"))
        if dry_run:
            print("[dry-run — no changes made]")


def create_sync_tui(args) -> object:
    """Return the appropriate TUI based on terminal and args."""
    no_rich = getattr(args, "no_rich", False)
    use_rich = not no_rich and sys.stdout.isatty()
    if use_rich:
        return RichSyncTUI()
    return TranscriptSyncTUI()
