#****************************************************************************
#* handler_phases.py
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
"""Canonical handler-phase taxonomy.

Handlers declare the named phase their *root* post-load work belongs to. The
five phases form a fixed, ordered, barrier-separated pipeline: every handler in
one phase completes before any handler in the next begins (see
``handler_order.py`` for how that guarantee is enforced).

The set is closed -- out-of-tree handlers target these phases via ``phase`` and
``run_before``/``run_after``; they do not register new phases.
"""
from typing import Optional


class HandlerPhase:
    """The canonical, ordered set of handler phases."""

    PREPARE     = "prepare"      # scaffolding other steps consume (dirs, etc.)
    ENVIRONMENT = "environment"  # generate env-activation files (direnv, modules)
    INSTALL     = "install"      # install managed packages (python, node)
    INTEGRATE   = "integrate"    # generate tool-integration artifacts (fusesoc, agents, dv-flow)
    FINALIZE    = "finalize"     # late cleanup / summary

    # Canonical order; index = ordinal.
    ORDER = [PREPARE, ENVIRONMENT, INSTALL, INTEGRATE, FINALIZE]

    @classmethod
    def is_valid(cls, name: str) -> bool:
        return name in cls.ORDER

    @classmethod
    def ordinal(cls, name: str) -> int:
        """Return the index of ``name`` in the canonical order.

        Raises ``ValueError`` if ``name`` is not a known phase.
        """
        return cls.ORDER.index(name)

    @classmethod
    def next(cls, name: str) -> Optional[str]:
        """Return the phase that follows ``name``, or ``None`` if it is last."""
        idx = cls.ORDER.index(name)
        if idx + 1 < len(cls.ORDER):
            return cls.ORDER[idx + 1]
        return None


def phase_from_legacy_int(n: int) -> str:
    """Map a legacy integer ``phase`` onto a named phase.

    Reproduces the pre-redesign ordering of the built-in handlers
    (``0,1,5,6,10``) so handlers that have not yet migrated to named phases
    keep their historical position. The integer itself is retained separately
    as a secondary tie-break key (see ``handler_order.resolve_order``).
    """
    if n < 2:
        return HandlerPhase.ENVIRONMENT
    if n <= 5:
        return HandlerPhase.INSTALL
    if n <= 9:
        return HandlerPhase.INTEGRATE
    return HandlerPhase.FINALIZE
