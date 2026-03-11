#****************************************************************************
#* prompt_patterns.py
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
Catalogue of regex patterns for detecting interactive subprocess prompts.

Each entry carries a compiled regex, a human-readable label for the TUI,
and a flag indicating whether the expected input is secret (password mode).

**Order matters:** more specific patterns must appear before general ones
so that e.g. ``[sudo] password`` is not swallowed by the generic
``User:`` pattern.
"""
import re
from typing import List, NamedTuple, Optional


class PromptPattern(NamedTuple):
    pattern: re.Pattern
    label: str       # display label shown to the user
    secret: bool     # True -> mask input (password mode)


PROMPT_PATTERNS: List[PromptPattern] = [
    # --- sudo (must precede generic User/Password) ---
    PromptPattern(re.compile(r'\[sudo\] password'),             'sudo password', True),
    # --- Passwords / passphrases ---
    PromptPattern(re.compile(r'[Pp]assword\s*:\s*$'),           'Password',      True),
    PromptPattern(re.compile(r'[Pp]assphrase\s*:\s*$'),         'Passphrase',    True),
    PromptPattern(re.compile(r'Enter passphrase for key'),      'Passphrase',    True),
    PromptPattern(re.compile(r'Enter password\s*:\s*$'),        'Password',      True),
    PromptPattern(re.compile(r'Enter PIN'),                     'PIN',           True),
    PromptPattern(re.compile(r'Token\s*:\s*$'),                 'Token',         True),
    # --- Usernames / identity ---
    PromptPattern(re.compile(r'[Uu]sername\s*:\s*$'),           'Username',      False),
    PromptPattern(re.compile(r'[Uu]ser\s*:\s*$'),               'User',          False),
    # --- SSH host verification ---
    PromptPattern(re.compile(
        r'Are you sure you want to continue connecting.*\(yes/no', re.DOTALL),
                                                                'yes/no',        False),
    # --- Specific yes/no / confirmation (before generic y/n) ---
    PromptPattern(re.compile(r'Proceed\?.*\[y/N\]', re.I),     'Proceed y/N',   False),
    PromptPattern(re.compile(r'\(yes/no(/\[fingerprint\])?\)'), 'yes/no',        False),
    PromptPattern(re.compile(r'\[Y/n\]\s*:?\s*$'),              'Y/n',           False),
    PromptPattern(re.compile(r'\[n/Y\]\s*:?\s*$'),              'n/Y',           False),
    # --- Generic y/n (case-insensitive, last resort) ---
    PromptPattern(re.compile(r'\[y/n\]\s*:?\s*$', re.I),       'y/n',           False),
    # --- pip / uv credential prompts ---
    PromptPattern(re.compile(r'Please provide credentials'),    'Credentials',   False),
    # --- Perforce ---
    PromptPattern(re.compile(r'Enter password:\s*$'),           'P4 Password',   True),
]


def match_prompt(text: str) -> Optional[PromptPattern]:
    """Test *text* against the prompt catalogue.

    Returns the first matching ``PromptPattern``, or ``None``.
    """
    for pp in PROMPT_PATTERNS:
        if pp.pattern.search(text):
            return pp
    return None
