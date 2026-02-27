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


# ---------------------------------------------------------------------------
# Rich TUI
# ---------------------------------------------------------------------------

class RichSyncTUI(SyncProgressListener):
    """Rich-based TUI: live spinner display during sync, final table after."""

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
        """Begin the live display (call before triggering sync)."""
        from rich.live import Live
        from rich.table import Table
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        self._live = Live(tbl, console=self.console, refresh_per_second=10)
        self._live.start()

    def stop(self):
        """End the live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def _refresh(self):
        if not self._live:
            return
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text

        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column("s",    width=3)
        tbl.add_column("name", style="bold")
        tbl.add_column("info")

        for name in self._order:
            state = self._pkg_states[name]
            if not state["done"]:
                marker = Spinner("dots", style="bold cyan")
                info   = Text(name, style="dim")
            else:
                r = state["result"]
                icon, style = _ICONS.get(r.outcome, ("?", "dim"))
                marker = Text(icon, style=style)
                dur    = "%.1fs" % state.get("duration", 0)
                label  = r.outcome.value
                if r.outcome == SyncOutcome.SYNCED and r.old_commit and r.new_commit:
                    label = "%s→%s" % (r.old_commit, r.new_commit)
                elif r.outcome == SyncOutcome.SKIPPED:
                    label = r.skipped_reason or "skipped"
                elif r.outcome == SyncOutcome.ERROR:
                    label = r.error or "error"
                info = Text("%s  %s" % (label, dur))

            tbl.add_row(marker, Text(name), info)

        self._live.update(tbl)

    # ── Final render ──────────────────────────────────────────────────────

    def render(self, results: List[PkgSyncResult], dry_run: bool = False):
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        from rich.panel import Panel

        console = Console()

        # ── Main table ────────────────────────────────────────────────────
        table = Table(show_header=True, header_style="bold", box=None,
                      padding=(0, 1))
        table.add_column("",        width=3, no_wrap=True)
        table.add_column("Package", style="bold", no_wrap=True)
        table.add_column("Branch",  no_wrap=True)
        table.add_column("Commits", no_wrap=True)
        table.add_column("Status",  no_wrap=True)
        table.add_column("Δ",       no_wrap=True)

        counts = {o: 0 for o in SyncOutcome}
        attention_items = []

        for r in results:
            counts[r.outcome] += 1
            icon, style = _ICONS.get(r.outcome, ("?", "dim"))
            marker = Text(icon, style=style)
            branch_text = Text(r.branch or "—", style="" if r.branch else "dim")

            if r.outcome == SyncOutcome.SYNCED:
                commits = Text("%s→%s" % (r.old_commit or "?", r.new_commit or "?"),
                               style="green")
                delta  = Text("↓%d" % r.commits_behind, style="green") if r.commits_behind else Text("")
                status = Text("synced", style="green")

            elif r.outcome == SyncOutcome.UP_TO_DATE:
                commits = Text(r.old_commit or "—", style="dim")
                delta   = Text("=", style="dim")
                status  = Text("up-to-date", style="dim")

            elif r.outcome == SyncOutcome.CONFLICT:
                commits = Text(r.old_commit or "—", style="red")
                delta   = Text("↓%d" % r.commits_behind, style="red") if r.commits_behind else Text("")
                status  = Text("conflict", style="bold red")
                attention_items.append(r)

            elif r.outcome == SyncOutcome.DIRTY:
                commits = Text(r.old_commit or "—", style="yellow")
                delta   = Text("")
                status  = Text("dirty", style="bold yellow")
                attention_items.append(r)

            elif r.outcome == SyncOutcome.AHEAD:
                ahead_str = "↑%d" % r.commits_ahead if r.commits_ahead else "ahead"
                commits = Text(r.old_commit or "—", style="yellow")
                delta   = Text(ahead_str, style="bold yellow")
                status  = Text("ahead of origin", style="bold yellow")
                attention_items.append(r)

            elif r.outcome == SyncOutcome.ERROR:
                commits = Text("—", style="red")
                delta   = Text("")
                status  = Text(r.error or "error", style="bold red")
                attention_items.append(r)

            elif r.outcome in _DRY_OUTCOMES:
                commits = Text(r.old_commit or "—", style="cyan")
                delta   = Text("↓%d" % r.commits_behind, style="cyan") if r.commits_behind else Text("")
                status  = Text(r.outcome.value, style="cyan")
                if r.outcome in (SyncOutcome.DRY_DIRTY, SyncOutcome.DRY_WOULD_CONFLICT):
                    attention_items.append(r)

            else:  # SKIPPED
                commits = Text("—", style="dim")
                delta   = Text("")
                status  = Text(r.skipped_reason or "skipped", style="dim")

            table.add_row(marker, Text(r.name), branch_text, commits, status, delta)

        console.print(table)

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
            console.print(Panel(
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

        console.print(Panel(summary, title="Sync", border_style=border))


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
        for r in results:
            icon, _ = _ICONS.get(r.outcome, ("?", "dim"))

            if r.outcome == SyncOutcome.SYNCED:
                print("  %s  %-30s  %-15s  %s→%s"
                      % (icon, r.name, r.branch or "—",
                         r.old_commit or "?", r.new_commit or "?"))

            elif r.outcome == SyncOutcome.UP_TO_DATE:
                print("  %s  %-30s  %-15s  (up-to-date)"
                      % (icon, r.name, r.branch or "—"))

            elif r.outcome == SyncOutcome.SKIPPED:
                print("  %s  %-30s  (%s)"
                      % (icon, r.name, r.skipped_reason or "skipped"))

            elif r.outcome == SyncOutcome.CONFLICT:
                print("  %s  %-30s  %-15s  CONFLICT"
                      % (icon, r.name, r.branch or "—"))
                if r.conflict_files:
                    print("     Conflicting files:")
                    for f in r.conflict_files:
                        print("       %s" % f)
                for step in r.next_steps:
                    print("     %s" % step)

            elif r.outcome == SyncOutcome.DIRTY:
                print("  %s  %-30s  %-15s  dirty (cannot sync)"
                      % (icon, r.name, r.branch or "—"))
                if r.dirty_files:
                    print("     Modified files:")
                    for f in r.dirty_files:
                        print("       %s" % f)

            elif r.outcome == SyncOutcome.AHEAD:
                print("  %s  %-30s  %-15s  ↑%s local commit(s) not on origin"
                      % (icon, r.name, r.branch or "—", r.commits_ahead or "?"))

            elif r.outcome == SyncOutcome.ERROR:
                print("  %s  %-30s  ERROR: %s"
                      % (icon, r.name, r.error or ""))

            elif r.outcome in _DRY_OUTCOMES:
                print("  %s  %-30s  %-15s  %s"
                      % (icon, r.name, r.branch or "—", r.outcome.value))
                if r.outcome == SyncOutcome.DRY_DIRTY and r.dirty_files:
                    print("     Modified files:")
                    for f in r.dirty_files:
                        print("       %s" % f)

            else:
                print("  %s  %-30s  %s" % (icon, r.name, r.outcome.value))

        print("")
        if dry_run:
            print("[dry-run — no changes made]")


def create_sync_tui(args) -> object:
    """Return the appropriate TUI based on terminal and args."""
    no_rich = getattr(args, "no_rich", False)
    use_rich = not no_rich and sys.stdout.isatty()
    if use_rich:
        return RichSyncTUI()
    return TranscriptSyncTUI()
