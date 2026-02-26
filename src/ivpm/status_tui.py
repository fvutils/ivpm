#****************************************************************************
#* status_tui.py
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
TUI renderers for `ivpm status`.

RichStatusTUI  — coloured table using the Rich library (when stdout is a TTY).
TranscriptStatusTUI — plain-text output (no ANSI).
"""
import sys
from typing import List
from .pkg_status import PkgVcsStatus


def _branch_label(s: PkgVcsStatus) -> str:
    """Return the branch/tag/detached string for display."""
    if s.vcs != "git":
        return "—"
    if s.tag:
        return "tag:%s" % s.tag
    if s.branch:
        return s.branch
    if s.commit:
        return "(detached @ %s)" % s.commit
    return "(unknown)"


def _upstream_label(s: PkgVcsStatus) -> str:
    """Return ahead/behind annotation, or '?' if unknown."""
    if s.vcs != "git":
        return "—"
    if s.ahead is None or s.behind is None:
        return "?"
    parts = []
    if s.ahead:
        parts.append("↑%d" % s.ahead)
    if s.behind:
        parts.append("↓%d" % s.behind)
    return " ".join(parts) if parts else "="


# ---------------------------------------------------------------------------
# Rich TUI
# ---------------------------------------------------------------------------

class RichStatusTUI:
    """Render status results as a Rich table."""

    def render(self, results: List[PkgVcsStatus], verbose: int = 0):
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        from rich.panel import Panel

        console = Console()
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("", width=2, no_wrap=True)
        table.add_column("Package", style="bold", no_wrap=True)
        table.add_column("Branch / Tag", no_wrap=True)
        table.add_column("Commit", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Upstream", no_wrap=True)

        git_total = dirty_count = non_vcs_count = pypi_count = 0

        for s in results:
            if s.src_type == "pypi":
                pypi_count += 1
                if verbose < 2:
                    continue

            if s.vcs == "git":
                git_total += 1
                if s.is_dirty:
                    dirty_count += 1
                    marker = Text("✗", style="bold red")
                    state = Text("dirty", style="red")
                else:
                    marker = Text("✓", style="bold green")
                    state = Text("clean", style="green")

                branch_text = Text(_branch_label(s))
                commit_text = Text(s.commit or "?")

                upstream = _upstream_label(s)
                if upstream == "?":
                    up_text = Text("?", style="dim")
                elif "↑" in upstream or "↓" in upstream:
                    up_text = Text(upstream, style="yellow")
                else:
                    up_text = Text(upstream, style="dim")

                if s.error:
                    marker = Text("!", style="bold yellow")
                    state = Text(s.error, style="yellow")

            else:
                non_vcs_count += 1
                marker = Text("~", style="dim")
                branch_text = Text("—", style="dim")
                commit_text = Text("—", style="dim")
                state = Text(s.src_type, style="dim")
                up_text = Text("—", style="dim")

            table.add_row(marker, Text(s.name), branch_text, commit_text, state, up_text)

            # Dirty file details — only with -v
            if verbose >= 1 and s.vcs == "git" and s.modified:
                for line in s.modified:
                    table.add_row(
                        Text(""), Text(""),
                        Text("  " + line, style="dim"), Text(""), Text(""), Text(""),
                    )

        console.print(table)

        clean_count = git_total - dirty_count
        summary = "%d package(s)" % (len(results) - pypi_count if verbose < 2 else len(results))
        if git_total:
            summary += " · %d git (%d clean, %d dirty)" % (git_total, clean_count, dirty_count)
        if non_vcs_count - pypi_count > 0:
            summary += " · %d non-VCS" % (non_vcs_count - pypi_count)
        if pypi_count:
            if verbose < 2:
                summary += " · %d pypi (hidden, use -vv to show)" % pypi_count
            else:
                summary += " · %d pypi" % pypi_count

        border = "green" if dirty_count == 0 else "red"
        console.print(Panel(summary, border_style=border, title="Status"))


# ---------------------------------------------------------------------------
# Transcript (plain-text) TUI
# ---------------------------------------------------------------------------

class TranscriptStatusTUI:
    """Render status results as plain text."""

    def render(self, results: List[PkgVcsStatus], verbose: int = 0):
        git_total = dirty_count = non_vcs_count = pypi_count = 0

        for s in results:
            if s.src_type == "pypi":
                pypi_count += 1
                if verbose < 2:
                    continue

            if s.vcs == "git":
                git_total += 1
                if s.is_dirty:
                    dirty_count += 1
                    marker = "✗"
                    state = "dirty"
                else:
                    marker = "✓"
                    state = "clean"

                upstream = _upstream_label(s)
                branch = _branch_label(s)
                commit = s.commit or "?"
                print("  %s  %-30s  %-25s  %s  %-6s  upstream:%s" % (
                    marker, s.name, branch, commit, state, upstream))

                if verbose >= 1 and s.modified:
                    for line in s.modified:
                        print("       %s" % line)

                if s.error:
                    print("       ! %s" % s.error)
            else:
                non_vcs_count += 1
                print("  ~  %-30s  (%s)" % (s.name, s.src_type))

        clean_count = git_total - dirty_count
        print("")
        shown = len(results) - (pypi_count if verbose < 2 else 0)
        print("%d package(s)" % shown, end="")
        if git_total:
            print(" · %d git (%d clean, %d dirty)" % (git_total, clean_count, dirty_count), end="")
        if non_vcs_count - pypi_count > 0:
            print(" · %d non-VCS" % (non_vcs_count - pypi_count), end="")
        if pypi_count:
            if verbose < 2:
                print(" · %d pypi (hidden, use -vv to show)" % pypi_count, end="")
            else:
                print(" · %d pypi" % pypi_count, end="")
        print("")


def create_status_tui(args) -> object:
    """Return the appropriate TUI based on terminal and args."""
    no_rich = getattr(args, "no_rich", False)
    use_rich = not no_rich and sys.stdout.isatty()
    if use_rich:
        return RichStatusTUI()
    return TranscriptStatusTUI()
