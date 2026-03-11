#****************************************************************************
#* tui_utils.py
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
Shared TUI helpers for interactive subprocess prompt handling.
"""
import getpass
import re
import sys


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHF]')


def handle_subprocess_prompt_rich(live, console, context: str, label: str, secret: bool) -> str:
    """Pause a Rich Live display, prompt the user, and resume.

    Parameters
    ----------
    live : rich.live.Live
        The active Live instance (will be stopped then restarted).
    console : rich.console.Console
        Console for rendering the prompt.
    context : str
        Recent subprocess output shown as context before the prompt.
    label : str
        Human-readable prompt label.
    secret : bool
        If True, mask the user's input.
    """
    from rich.prompt import Prompt

    live.stop()
    try:
        clean = _ANSI_RE.sub('', context).strip()
        if clean:
            console.print(f"\n[dim]{clean}[/dim]")
        return Prompt.ask(
            f"[bold yellow]{label}[/bold yellow]",
            password=secret,
            console=console,
        )
    finally:
        live.start()


def handle_subprocess_prompt_plain(context: str, label: str, secret: bool) -> str:
    """Plain-text prompt for non-Rich / transcript mode.

    Parameters
    ----------
    context : str
        Recent subprocess output.
    label : str
        Prompt label.
    secret : bool
        If True, use ``getpass`` for masked input.
    """
    clean = _ANSI_RE.sub('', context).strip()
    if clean:
        print(clean, file=sys.stderr)
    if secret:
        return getpass.getpass(f"{label}: ")
    return input(f"{label}: ")
