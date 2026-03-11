#****************************************************************************
#* pty_runner.py
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
PTY-based subprocess runner with interactive prompt detection.

Spawns a child process inside a pseudo-terminal so that tools which call
``isatty()`` (git, ssh, p4, sudo, ...) emit their prompts where we can
intercept them.  Detection uses two complementary signals:

* **Pattern matching** against a catalogue of known prompt regexes
  (see ``prompt_patterns.py``).  Fires immediately on match.
* **Quiescence timeout** -- if the process stops producing output for
  ``QUIESCENCE_TIMEOUT`` seconds after having recently emitted bytes, it
  *may* be waiting for input.  The accumulated output is shown to the user
  as context for a free-form text prompt.

Falls back to ``subprocess.Popen`` with pipes when no TTY is available
or when ``ptyprocess`` is not installed.

Carriage-return (``\\r``) handling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Progress bars (``Term::ProgressBar``, ``tqdm``, ``rich``, ...) overwrite
the current line by emitting ``\\r`` followed by new text.  When a ``\\r``
is present in a chunk the *entire* accumulator -- including the trailing
text that follows the last ``\\r`` -- is flushed to the output callback.
This prevents the quiescence heuristic from misidentifying progress-bar
output as an interactive prompt.
"""
import logging
import os
import platform
import select
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Tuple

from .prompt_patterns import match_prompt

_logger = logging.getLogger("ivpm.pty_runner")

QUIESCENCE_TIMEOUT = 1.5  # seconds

# Guard ptyprocess import -- Unix only
_pty_available = False
if platform.system() != "Windows":
    try:
        from ptyprocess import PtyProcessUnicode  # type: ignore
        _pty_available = True
    except ImportError:
        _logger.debug("ptyprocess not available; PTY mode disabled")


class PtyRunner:
    """Run a command in a PTY with prompt detection and callback support.

    Parameters
    ----------
    cmd : list[str]
        Command and arguments.
    cwd : str, optional
        Working directory.
    env : dict, optional
        Environment variables.  Defaults to ``os.environ``.
    output_callback : callable(str) -> None, optional
        Called with each chunk of normal (non-prompt) output.
    prompt_callback : callable(context, label, secret) -> str, optional
        Called when a prompt is detected.  *context* is the recent output
        text, *label* is a human-readable prompt label, *secret* is True
        when input should be masked.  Must return the user's response
        (including any trailing newline is handled by the runner).
    dimensions : (rows, cols), optional
        PTY dimensions.
    use_pty : bool, optional
        ``True`` (default) to use a PTY when available; ``False`` to force
        pipe-based fallback.
    """

    def __init__(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        output_callback: Optional[Callable[[str], None]] = None,
        prompt_callback: Optional[Callable[[str, str, bool], str]] = None,
        dimensions: Tuple[int, int] = (24, 200),
        use_pty: bool = True,
    ):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.output_callback = output_callback
        self.prompt_callback = prompt_callback
        self.dimensions = dimensions
        self.use_pty = use_pty and _pty_available
        # Accumulated output for error reporting
        self._all_output: List[str] = []

    @property
    def captured_output(self) -> str:
        """All output captured during the run, concatenated."""
        return "".join(self._all_output)

    def run(self) -> int:
        """Spawn the process, drive the I/O loop, and return the exit code."""
        if self.use_pty:
            return self._run_pty()
        else:
            return self._run_pipe()

    # ------------------------------------------------------------------
    # PTY path
    # ------------------------------------------------------------------

    def _run_pty(self) -> int:
        env = self.env if self.env is not None else os.environ.copy()

        proc = PtyProcessUnicode.spawn(
            self.cmd,
            cwd=self.cwd,
            env=env,
            dimensions=self.dimensions,
        )

        master_fd = proc.fd
        accumulator = ""  # text since last output_callback / prompt
        received_any = False  # have we received any output in this "unit"?

        try:
            while proc.isalive():
                ready, _, _ = select.select([master_fd], [], [], QUIESCENCE_TIMEOUT)

                if ready:
                    try:
                        chunk = proc.read(4096)
                    except EOFError:
                        break

                    if not chunk:
                        break

                    accumulator += chunk
                    received_any = True
                    self._all_output.append(chunk)

                    # Check for a known prompt pattern
                    pp = match_prompt(accumulator)
                    if pp is not None:
                        response = self._handle_prompt(
                            accumulator, pp.label, pp.secret
                        )
                        if response is not None:
                            if not response.endswith("\n"):
                                response += "\n"
                            proc.write(response)
                        accumulator = ""
                        received_any = False
                        continue

                    # Emit completed output to the output callback and
                    # keep only a trailing partial line in the
                    # accumulator for the next prompt/quiescence check.
                    #
                    # A \r (carriage return) signals that the subprocess
                    # is overwriting the current line -- this is how
                    # progress bars work (Term::ProgressBar, tqdm, ...).
                    # When \r is present, we flush the *entire*
                    # accumulator to the output callback so that:
                    #  1. The downstream parser can see progress updates.
                    #  2. The accumulator is empty, preventing the
                    #     quiescence heuristic from misidentifying
                    #     progress output as an interactive prompt.
                    #
                    # For \n-only output (the common case), we keep the
                    # trailing partial line in the accumulator as before.
                    if "\r" in accumulator:
                        if self.output_callback:
                            self.output_callback(accumulator)
                        accumulator = ""
                        received_any = False
                    else:
                        last_nl = accumulator.rfind("\n")
                        if last_nl >= 0:
                            complete = accumulator[:last_nl + 1]
                            accumulator = accumulator[last_nl + 1:]
                            if self.output_callback:
                                self.output_callback(complete)

                else:
                    # select() timed out -- quiescence heuristic
                    if received_any and accumulator.strip():
                        pp = match_prompt(accumulator)
                        label = pp.label if pp else "Input"
                        secret = pp.secret if pp else False
                        response = self._handle_prompt(
                            accumulator, label, secret
                        )
                        if response is not None:
                            if not response.endswith("\n"):
                                response += "\n"
                            proc.write(response)
                        accumulator = ""
                        received_any = False

        except OSError:
            # PTY closed
            pass

        # Flush any remaining output
        if accumulator and self.output_callback:
            self.output_callback(accumulator)

        proc.wait()
        return proc.exitstatus if proc.exitstatus is not None else -1

    def _handle_prompt(self, context: str, label: str, secret: bool) -> Optional[str]:
        """Invoke the prompt callback, or return None if not available."""
        if self.prompt_callback:
            return self.prompt_callback(context, label, secret)
        _logger.warning(
            "Subprocess appears to be waiting for input (%s) but no "
            "prompt_callback is configured.  Context: %s",
            label, context.strip()[-200:],
        )
        return None

    # ------------------------------------------------------------------
    # Pipe fallback
    # ------------------------------------------------------------------

    def _run_pipe(self) -> int:
        """Fallback: run with pipes (no prompt detection)."""
        env = self.env if self.env is not None else os.environ.copy()
        proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        for line in proc.stdout:
            self._all_output.append(line)
            if self.output_callback:
                self.output_callback(line)
        proc.wait()
        return proc.returncode
