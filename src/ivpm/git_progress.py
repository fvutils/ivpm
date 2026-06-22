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
        on_progress: Optional[Callable[[str], None]] = None) -> int:
    """Run a git command, reporting progress via ``on_progress``.

    ``cmd`` should already include ``--progress`` so git emits progress even
    though stderr is a pipe.  ``on_progress`` is called with a short message
    each time the reported percentage changes (consecutive duplicates are
    suppressed).  stdout is discarded.  Returns the process exit code.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)

    last_msg = None
    buf = ""
    try:
        while True:
            chunk = proc.stderr.read(256)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            # git terminates in-place updates with \r and final lines with \n;
            # split on either and keep any trailing partial segment in buf.
            segments = re.split(r'[\r\n]', buf)
            buf = segments.pop()
            if on_progress is None:
                continue
            for seg in segments:
                msg = parse_progress_line(seg)
                if msg is not None and msg != last_msg:
                    last_msg = msg
                    on_progress(msg)
    finally:
        proc.stderr.close()
    proc.wait()
    return proc.returncode
