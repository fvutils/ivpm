#****************************************************************************
#* dep_mode.py
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
"""The ``deps-mode`` declaration (see nested-deps-design.md §4.1).

A manifest, a dep-set, or a single dependency entry may declare how the
dependencies resolved *beneath* it are placed:

  * ``flatten`` (the default) -- every dependency lands in the one root
    deps-dir, deduplicated by name, first resolver wins.
  * ``nested``   -- the package becomes a *boundary*: its own dependencies
    resolve into ``<pkg>/<deps-dir>/`` and the mode propagates to them.

``hierarchical`` is accepted as a spelling of ``nested``.
"""
from typing import Optional

from .msg import fatal

#: The default mode: today's behavior, unchanged.
FLATTEN = "flatten"

#: A boundary: dependencies resolve into the package's own deps-dir, and the
#: mode propagates to them (design §4.2 -- nested is inherently sticky).
NESTED = "nested"

VALID_MODES = (FLATTEN, NESTED)

#: Accepted spellings, mapped onto the canonical mode.
_ALIASES = {
    FLATTEN: FLATTEN,
    NESTED: NESTED,
    "hierarchical": NESTED,
}


def parse_deps_mode(value, loc=None) -> Optional[str]:
    """Parse a ``deps-mode`` value into a canonical mode string.

    Returns None when *value* is None, so callers can distinguish "not
    declared" (inherit) from "declared flatten" (stop propagating) -- a
    distinction ``effective_mode`` depends on.

    Anything else is a located fatal naming the valid values.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        fatal("'deps-mode' must be a string, not %s. Valid values: %s" % (
            type(value).__name__, ", ".join(VALID_MODES)),
            loc if loc is not None else value)
    mode = _ALIASES.get(value.strip().lower())
    if mode is None:
        fatal("Invalid 'deps-mode' value '%s'. Valid values: %s"
              " ('hierarchical' is accepted as a spelling of 'nested')" % (
                  value, ", ".join(VALID_MODES)),
              loc if loc is not None else value)
    return mode
