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
from .tui_theme import make_console
import sys
from typing import List
from .pkg_status import PkgVcsStatus
from .tui_theme import S_PLACEHOLDER, S_DETAIL, S_SECONDARY


def _branch_label(s: PkgVcsStatus) -> str:
    """Return the branch/tag/detached string for display."""
    if s.vcs != "git":
        return "—"
    # A declared commit pin outranks everything the working tree can say: it is
    # why this package sits where it does and why sync will not move it. In
    # particular it outranks a tag that happens to point at the same commit --
    # that is a coincidence of the remote's tagging, not what the manifest
    # asked for, and it would go on being displayed even after the tag moved.
    # (A `tag:` pin leaves pinned_commit None and still renders as tag: below.)
    if s.pinned_commit:
        return "pinned:%s" % s.pinned_commit[:7]
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
    # A pinned checkout is detached, so it has no upstream to be ahead of or
    # behind. That is 'not applicable', not 'unknown' -- '?' next to a pin
    # reads as a failed lookup and invites the user to go investigate nothing.
    if s.pinned_commit or s.tag:
        return "—"
    if s.ahead is None or s.behind is None:
        return "?"
    parts = []
    if s.ahead:
        parts.append("↑%d" % s.ahead)
    if s.behind:
        parts.append("↓%d" % s.behind)
    return " ".join(parts) if parts else "="


def _provenance_label(s: PkgVcsStatus) -> str:
    """Return a deps-source provenance suffix for the package name, or ''."""
    if not getattr(s, "from_deps_source", None):
        return ""
    if getattr(s, "deps_source_auto", False):
        return "(auto: worktree)"
    return "(deps-source)"


def _state_label(s: PkgVcsStatus):
    """Return (marker, state_text) for a git status row."""
    if s.error:
        return "!", s.error
    if s.is_dirty:
        return "✎", "modified"
    if s.untracked:
        return "✎", "untracked"
    return "✓", "clean"


# ---------------------------------------------------------------------------
# Rich TUI
# ---------------------------------------------------------------------------

class RichStatusTUI:
    """Render status results as a Rich table."""

    def render(self, results: List[PkgVcsStatus], verbose: int = 0,
               root_status: PkgVcsStatus = None):
        from rich.table import Table
        from rich.text import Text
        from rich.panel import Panel

        console = make_console()

        # Root-project header (omitted when the root type is unknown).
        if root_status is not None:
            self._render_root(console, root_status, verbose)

        # Underline the header from the "Package" column to the end of the line
        # (the leading indent column stays plain), so the table reads as an
        # indented block separated from the root-project content above.  The
        # per-row status marker is merged into the Package cell so it aligns
        # under the "P" of the header rather than sitting further left.
        hdr = "bold underline"
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("", width=2, no_wrap=True)   # indent spacer (no header)
        table.add_column("Package", style="bold", no_wrap=True, header_style=hdr)
        table.add_column("Branch / Tag", no_wrap=True, header_style=hdr)
        table.add_column("Commit", no_wrap=True, header_style=hdr)
        table.add_column("State", no_wrap=True, header_style=hdr)
        table.add_column("Upstream", no_wrap=True, header_style=hdr)

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
                    marker = Text("✎", style="bold cyan")
                    state = Text("modified", style="cyan")
                elif s.untracked:
                    dirty_count += 1
                    marker = Text("✎", style="bold cyan")
                    state = Text("untracked", style="cyan")
                else:
                    marker = Text("✓", style="bold green")
                    state = Text("clean", style="green")

                branch_text = Text(_branch_label(s))
                commit_text = Text(s.commit or "?")

                upstream = _upstream_label(s)
                if upstream == "?":
                    up_text = Text("?", style=S_PLACEHOLDER)
                elif "↑" in upstream or "↓" in upstream:
                    up_text = Text(upstream, style="yellow")
                else:
                    up_text = Text(upstream, style=S_PLACEHOLDER)

                if s.error:
                    marker = Text("!", style="bold yellow")
                    state = Text(s.error, style="yellow")

            else:
                non_vcs_count += 1
                marker = Text("~", style=S_PLACEHOLDER)
                branch_text = Text("—", style=S_PLACEHOLDER)
                commit_text = Text("—", style=S_PLACEHOLDER)
                state = Text(s.src_type, style=S_SECONDARY)
                up_text = Text("—", style=S_PLACEHOLDER)

            # Marker is merged into the Package cell (aligned under "P").
            name_text = Text()
            name_text.append_text(marker)
            name_text.append(" ")
            name_text.append(s.name)
            prov = _provenance_label(s)
            if prov:
                name_text.append(" " + prov, style=S_SECONDARY)
            table.add_row(Text(""), name_text, branch_text, commit_text, state, up_text)

            # Dirty file details — only with -v
            if verbose >= 1 and s.vcs == "git":
                for line in s.modified:
                    table.add_row(
                        Text(""), Text(""),
                        Text("  " + line, style=S_DETAIL), Text(""), Text(""), Text(""),
                    )
                for line in s.untracked:
                    table.add_row(
                        Text(""), Text(""),
                        Text("  " + line, style=S_DETAIL), Text(""), Text(""), Text(""),
                    )

        console.print(table)

        clean_count = git_total - dirty_count
        summary = "%d package(s)" % (len(results) - pypi_count if verbose < 2 else len(results))
        if git_total:
            summary += " · %d git (%d clean, %d modified)" % (git_total, clean_count, dirty_count)
        if non_vcs_count - pypi_count > 0:
            summary += " · %d non-VCS" % (non_vcs_count - pypi_count)
        if pypi_count:
            if verbose < 2:
                summary += " · %d pypi (hidden, use -vv to show)" % pypi_count
            else:
                summary += " · %d pypi" % pypi_count

        border = "green" if dirty_count == 0 else "cyan"
        console.print(Panel(summary, border_style=border, title="Status"))

    def _render_root(self, console, s: PkgVcsStatus, verbose: int = 0):
        from rich.text import Text

        marker, state = _state_label(s)
        line = Text()
        line.append("Root", style="bold")
        prov = getattr(s, "provider", None)
        if prov:
            line.append(" [%s]" % prov, style=S_SECONDARY)
        line.append("  ")
        if s.vcs == "git":
            line.append(_branch_label(s))
            if s.commit:
                line.append("  " + s.commit, style=S_SECONDARY)
            style = "yellow" if s.error else ("cyan" if state != "clean" else "green")
            line.append("  %s %s" % (marker, state), style=style)
            upstream = _upstream_label(s)
            if upstream not in ("=", "?", "—"):
                line.append("  " + upstream, style="yellow")
        else:
            line.append(s.src_type or "unknown", style=S_SECONDARY)
        console.print(line)

        # Modified/untracked file details — only with -v, mirroring packages.
        if verbose >= 1 and s.vcs == "git":
            for fline in s.modified:
                console.print(Text("    " + fline, style=S_DETAIL))
            for fline in s.untracked:
                console.print(Text("    " + fline, style=S_DETAIL))


# ---------------------------------------------------------------------------
# Transcript (plain-text) TUI
# ---------------------------------------------------------------------------

class TranscriptStatusTUI:
    """Render status results as plain text."""

    def render(self, results: List[PkgVcsStatus], verbose: int = 0,
               root_status: PkgVcsStatus = None):
        # Root-project header (omitted when the root type is unknown).
        if root_status is not None:
            self._render_root(root_status, verbose)

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
                    marker = "✎"
                    state = "modified"
                elif s.untracked:
                    dirty_count += 1
                    marker = "✎"
                    state = "untracked"
                else:
                    marker = "✓"
                    state = "clean"

                upstream = _upstream_label(s)
                branch = _branch_label(s)
                commit = s.commit or "?"
                prov = _provenance_label(s)
                name = s.name + (" " + prov if prov else "")
                print("  %s  %-30s  %-25s  %s  %-9s  upstream:%s" % (
                    marker, name, branch, commit, state, upstream))

                if verbose >= 1:
                    for line in s.modified:
                        print("       %s" % line)
                    for line in s.untracked:
                        print("       %s" % line)

                if s.error:
                    print("       ! %s" % s.error)
            else:
                non_vcs_count += 1
                prov = _provenance_label(s)
                suffix = ("  " + prov) if prov else ""
                print("  ~  %-30s  (%s)%s" % (s.name, s.src_type, suffix))

        clean_count = git_total - dirty_count
        print("")
        shown = len(results) - (pypi_count if verbose < 2 else 0)
        print("%d package(s)" % shown, end="")
        if git_total:
            print(" · %d git (%d clean, %d modified)" % (git_total, clean_count, dirty_count), end="")
        if non_vcs_count - pypi_count > 0:
            print(" · %d non-VCS" % (non_vcs_count - pypi_count), end="")
        if pypi_count:
            if verbose < 2:
                print(" · %d pypi (hidden, use -vv to show)" % pypi_count, end="")
            else:
                print(" · %d pypi" % pypi_count, end="")
        print("")

    def _render_root(self, s: PkgVcsStatus, verbose: int = 0):
        prov = getattr(s, "provider", None)
        head = "Root" + (" [%s]" % prov if prov else "")
        if s.vcs == "git":
            marker, state = _state_label(s)
            upstream = _upstream_label(s)
            print("%s  %s  %s  %s  %s  upstream:%s" % (
                head, marker, _branch_label(s), s.commit or "?", state, upstream))
            # Modified/untracked file details — only with -v, mirroring packages.
            if verbose >= 1:
                for fline in s.modified:
                    print("       %s" % fline)
                for fline in s.untracked:
                    print("       %s" % fline)
        else:
            print("%s  (%s)" % (head, s.src_type or "unknown"))
        print("")


def create_status_tui(args) -> object:
    """Return the appropriate TUI based on terminal and args."""
    no_rich = getattr(args, "no_rich", False)
    use_rich = not no_rich and sys.stdout.isatty()
    if use_rich:
        return RichStatusTUI()
    return TranscriptStatusTUI()
