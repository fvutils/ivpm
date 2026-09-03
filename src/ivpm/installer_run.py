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
            cmd=cmd)

    return InstallerResult(returncode=returncode, lines=lines, cmd=cmd)


def format_output_tail(lines, tail: int = 20) -> str:
    """Return the last *tail* non-blank lines, newline-prefixed, or "".

    Blank lines are dropped before the tail is taken: installers pad their
    output generously, and a tail of mostly blanks wastes the budget the user
    actually reads.
    """
    if not lines:
        return ""
    relevant = [l for l in lines if l.strip()]
    if not relevant:
        return ""
    snippet = relevant[-tail:] if len(relevant) > tail else relevant
    return "\n" + "\n".join(snippet)
