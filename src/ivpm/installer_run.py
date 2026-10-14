#****************************************************************************
#* installer_run.py
#*
#* One way to run an external installer (uv, pip, npm, setup.py) so that its
#* output always survives the run.
#*
#* The rule this module exists to enforce: *quiet suppresses display, never
#* collection*. Every caller used to make its own choice here, and the node
#* handler's choice -- stdout=DEVNULL when quiet -- threw away the only
#* evidence of why an install failed at exactly the moment it was needed.
#* Attribution (see content_attrib.py) is built on top of this: it can only
#* report what was captured.
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
#****************************************************************************
import dataclasses as dc
import logging
import re
import subprocess
import sys
from typing import Callable, List, Optional

_logger = logging.getLogger("ivpm.installer_run")


@dc.dataclass
class InstallerResult:
    """The outcome of one installer invocation.

    ``lines`` is always populated, whatever the display mode. Callers that
    need to attribute a failure read it; callers that only care whether the
    run worked read ``returncode``.
    """
    returncode : int
    lines      : List[str] = dc.field(default_factory=list)
    cmd        : List[str] = dc.field(default_factory=list)

    #: The installer itself could not be started, as opposed to running and
    #: failing. Distinguished explicitly because the synthesised exit code for
    #: this case (127) is also what a *successfully started* installer returns
    #: when a command inside it is missing -- `npm install` reports 127 when a
    #: lifecycle script hits `tsc: not found`. Callers that read the code alone
    #: told the user to install npm when npm was working perfectly well.
    spawn_failed : bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_installer(cmd,
                  env=None,
                  cwd=None,
                  task=None,
                  quiet=False,
                  line_parser: Optional[Callable[[str], Optional[str]]] = None
                  ) -> InstallerResult:
    """Run an installer command, capturing all of its output.

    stderr is folded into stdout so the transcript reads in the order the
    installer produced it -- a build failure's traceback is on stderr while
    the "Building <pkg>" line that identifies it is on stdout, and the two are
    only useful together.

    *quiet* suppresses echoing to this process's stdout. It does not suppress
    capture. *task*, when given, receives short progress strings produced by
    *line_parser* from each line.

    Never raises for a non-zero exit: the caller owns the decision about
    severity and about which source location the failure belongs to. A missing
    executable is likewise returned as a result (returncode 127) rather than
    raised, so that callers have a uniform path.
    """
    cmd = list(cmd)
    lines : List[str] = []

    try:
        # Popen as a context manager so the stdout pipe is closed and the
        # process reaped on exit; a lingering pipe raises ResourceWarning when
        # the Popen object is finally collected.
        with subprocess.Popen(cmd,
                              env=env,
                              cwd=cwd,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              text=True,
                              errors="replace") as proc:
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                lines.append(line)

                if not quiet:
                    print(line, flush=True)

                if line_parser is not None and task is not None:
                    status = line_parser(line)
                    if status:
                        task.progress(status)
        returncode = proc.returncode
    except FileNotFoundError as e:
        # The installer itself is missing. Reported as a result so the caller
        # can locate the failure the same way it locates any other.
        return InstallerResult(
            returncode=127,
            lines=["%s: command not found (%s)" % (cmd[0], e)],
            cmd=cmd,
            spawn_failed=True)

    return InstallerResult(returncode=returncode, lines=lines, cmd=cmd)


# Installer trace lines. ``uv --verbose`` interleaves these with the build's
# own output and, on a failed build, ends with a run of lock-release lines, so
# a plain tail is mostly trace and loses the error that explains the failure.
_TRACE_RE = re.compile(r"^(DEBUG|TRACE) ")

# The first line of a build's explanation of why it failed. Used only to choose
# which lines to show -- never to decide who is at fault (see content_attrib).
# Case-sensitive on purpose: ``error:`` is how gcc/clang/ld/rustc/uv/npm
# introduce an error, and ``RuntimeError:`` at the end of a backend traceback
# is a consequence of the error, not the error.
_ERROR_RE = re.compile(
    r"(CMake Error|\berror:|\bfatal error\b|^\s*ERROR[: ]|npm ERR!)")

# Lines shown from the first error onward, when it falls outside the tail.
_ERROR_CONTEXT = 12

# The installer's own one-paragraph summary of the failure. uv and pip both
# open it with ``×`` and continue it with tree glyphs (``├─▶``, ``│``,
# ``╰─▶`` / ``╰─>``) up to the first blank line. It is the best summary the
# output contains -- uv names the distribution and the failing step in it --
# and it comes *before* the build log, so a tail never reaches it.
_HEADLINE_RE = re.compile(r"^\s*\u00d7 ")

# Longest headline kept. uv's is 3-4 lines; the cap stops a headline with no
# blank line after it from swallowing the build log.
_HEADLINE_MAX = 8

# Tail kept alongside a headline.
_HEADLINE_TAIL = 10


def _headline_span(lines):
    """``(first, last)`` raw indices of the installer's summary, or None."""
    for i, l in enumerate(lines):
        if _HEADLINE_RE.match(l):
            j = i
            while (j + 1 < len(lines) and j + 1 - i < _HEADLINE_MAX
                   and lines[j + 1].strip()):
                j += 1
            return (i, j)
    return None


def format_output_tail(lines, tail: int = 20) -> str:
    """Return an excerpt of installer output, newline-prefixed, or "".

    The excerpt is, in output order:

    - the installer's own failure summary (uv's/pip's ``×`` block), which
      names what failed and comes before the build log;
    - up to ``_ERROR_CONTEXT`` lines from the first error line (a CMake or
      compiler error), which a native build reports and then unwinds through
      a traceback long enough to push it out of any fixed-size tail;
    - the last *tail* lines (at most ``_HEADLINE_TAIL`` when there is a
      summary).

    Gaps between the pieces are marked ``...``. Blank lines and installer
    trace lines (``DEBUG``/``TRACE``) are dropped: installers pad their output
    generously, and a budget spent on padding or lock-release chatter is one
    the user cannot read.
    """
    if not lines:
        return ""
    raw = list(lines)
    keep = [i for i, l in enumerate(raw)
            if l.strip() and not _TRACE_RE.match(l)]
    if not keep:
        return ""
    pos = {ri: k for k, ri in enumerate(keep)}
    relevant = [raw[i] for i in keep]

    span = _headline_span(raw)
    if span is not None:
        # The installer has already said what failed; the tail only has to
        # show how the build ended, and the full log is a path away.
        tail = min(tail, _HEADLINE_TAIL)

    # Each piece is a half-open [start, end) range over *relevant*.
    pieces = [(max(0, len(relevant) - tail), len(relevant))]

    if span is not None:
        idx = [pos[i] for i in range(span[0], span[1] + 1) if i in pos]
        if idx:
            pieces.append((idx[0], idx[-1] + 1))

    first_err = next((i for i, l in enumerate(relevant) if _ERROR_RE.search(l)),
                     None)
    if first_err is not None:
        pieces.append((first_err, min(first_err + _ERROR_CONTEXT, len(relevant))))

    pieces.sort()
    merged = []
    for s, e in pieces:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    snippet = []
    for k, (s, e) in enumerate(merged):
        if k > 0:
            # Indented like the line it precedes, so a caller that dedents the
            # excerpt as a block is not stopped by an unindented marker.
            nxt = relevant[s]
            snippet.append(nxt[:len(nxt) - len(nxt.lstrip())] + "...")
        snippet.extend(relevant[s:e])
    return "\n" + "\n".join(snippet)
