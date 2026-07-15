#****************************************************************************
#* git_progress.py
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
Run git commands while parsing their progress output.

git writes progress for long-running phases (Counting/Compressing/Receiving
objects, Resolving deltas) to stderr, updating a single line in place with
carriage returns.  When stderr is not a TTY, git only emits progress if it is
passed ``--progress`` explicitly.  This module runs a git command, splits its
stderr on ``\\r``/``\\n``, and invokes a callback with a short progress message
("Receiving objects 42%") whenever the reported percentage changes.
"""
import re
import subprocess
import sys
from collections import deque
from typing import Callable, List, Optional

# Matches git progress lines such as:
#   "Receiving objects:  42% (518/1234), 1.20 MiB | 2.40 MiB/s"
#   "remote: Counting objects:  50% (617/1234)"
#   "Resolving deltas: 100% (789/789), done."
_PROGRESS_RE = re.compile(r'^(?:remote:\s*)?([A-Za-z][A-Za-z ]+?):\s+(\d+)%')


def parse_progress_line(line: str) -> Optional[str]:
    """Return a "<phase> <pct>%" message for a git progress line, else None."""
    m = _PROGRESS_RE.match(line.strip())
    if m is None:
        return None
    return "%s %s%%" % (m.group(1).strip(), m.group(2))


def run_git_with_progress(
        cmd: List[str],
        cwd: Optional[str] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        stderr_sink: Optional[List[str]] = None,
        echo: bool = False,
        max_capture: int = 60) -> int:
    """Run a git command, reporting progress via ``on_progress``.

    ``cmd`` should already include ``--progress`` so git emits progress even
    though stderr is a pipe.  ``on_progress`` is called with a short message
    each time the reported percentage changes (consecutive duplicates are
    suppressed).  stdout is discarded.  Returns the process exit code.

    When ``stderr_sink`` is provided, the last ``max_capture`` non-progress
    stderr lines are appended to it so a failing command can be diagnosed
    (e.g. "remote: Repository not found").  When ``echo`` is set, git's raw
    stderr is streamed to ``sys.stderr`` as it arrives (preserving the ``\\r``
    in-place progress updates) -- used outside the Rich TUI so the user still
    sees git's native output while we also capture it.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)

    captured = deque(maxlen=max_capture) if stderr_sink is not None else None
    last_msg = None
    buf = ""
    try:
        while True:
            chunk = proc.stderr.read(256)
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            if echo:
                # Write raw so \r-based in-place progress renders as git intends.
                sys.stderr.write(text)
                sys.stderr.flush()
            buf += text
            # git terminates in-place updates with \r and final lines with \n;
            # split on either and keep any trailing partial segment in buf.
            segments = re.split(r'[\r\n]', buf)
            buf = segments.pop()
            for seg in segments:
                msg = parse_progress_line(seg)
                if msg is not None:
                    if on_progress is not None and msg != last_msg:
                        last_msg = msg
                        on_progress(msg)
                    continue  # don't retain transient progress lines
                if captured is not None and seg.strip():
                    captured.append(seg.rstrip())
    finally:
        proc.stderr.close()
    proc.wait()
    if captured is not None:
        if buf.strip():
            captured.append(buf.rstrip())
        stderr_sink.extend(captured)
    return proc.returncode
