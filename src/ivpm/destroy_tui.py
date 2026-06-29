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
from .pkg_remove import SafetyLevel, RemoveOutcome


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
