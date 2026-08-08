#****************************************************************************
#* tui_theme.py
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
Shared Rich styles for ivpm's TUI output.

Rich's ``dim`` emits SGR 2, which terminals render by blending the foreground
toward the background.  On a dark theme that yields readable grey-on-black; on
a light theme it yields pale-grey-on-white, which is effectively invisible.
The blend is a property of the attribute, not of a colour choice, so no amount
of light/dark detection fixes it -- the answer is to stop using ``dim`` for
anything the user has to read.

The rule this module encodes:

  * ``dim`` is reserved for marks that carry no information.  If the terminal
    swallows them entirely, nothing is lost -- that is precisely the signal
    ("nothing to see here").
  * Text the user must read uses either the default foreground or a named ANSI
    colour.  Named colours are resolved from the terminal's own palette, so
    they track the user's light/dark theme automatically.

Use the semantic names below rather than raw style strings, so the light-mode
contrast decision stays in one place.
"""

# Marks that are safe to lose: em-dashes, ellipses, "?", the "=" in-sync
# marker.  These stay dim on purpose -- they should recede.
S_PLACEHOLDER = "dim"

# Subordinate lines the user still reads: modified/untracked file paths listed
# under a package.  Cyan ties them to the "modified"/"untracked" state colour
# already used on the parent row.
S_DETAIL = "cyan"

# Inline field labels ("Origin:", "Provider:"), parenthetical hints, and
# legend/footnote lines.  Cyan separates label from value without relying on
# contrast against the background.
S_LABEL = "cyan"

# Readable-but-subordinate values that were only dim to de-emphasise a table
# column.  Column position and the header already supply that hierarchy, so
# the data itself renders in the default foreground.
S_SECONDARY = ""


# Same vocabulary, exposed as Rich markup tags so console.print("[label]...")
# call sites share the definitions above rather than hard-coding "dim".
IVPM_THEME = {
    "placeholder": S_PLACEHOLDER,
    "detail":      S_DETAIL,
    "label":       S_LABEL,
    "secondary":   S_SECONDARY or "none",
}


def make_console(**kwargs):
    """Return a Rich Console with ivpm's theme registered.

    Rich is imported lazily (as everywhere else in ivpm) so that importing
    this module stays cheap and does not hard-require the dependency.
    """
    from rich.console import Console
    from rich.theme import Theme
    return Console(theme=Theme(IVPM_THEME), **kwargs)
