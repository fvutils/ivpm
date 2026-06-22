#****************************************************************************
#* handler_order.py
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
"""Resolve handler root-phase execution order.

Builds one directed graph over two kinds of nodes -- *phase anchors* and
*handlers* -- and topologically sorts it. Named phases and relative
``run_before``/``run_after`` constraints are both just edges in that graph.

Phase anchors are chained (``prepare -> environment -> ... -> finalize -> end``)
and every handler is bracketed between its phase anchor and the following one
(``Pᵢ -> handler -> Pᵢ₊₁``). That bracketing is what makes phases *barriers*:
all of one phase's handlers sort ahead of any of the next phase's.

Within a topological level (mutually independent handlers) the order is a
deterministic tie-break: ``(phase ordinal, legacy int, name)``.
"""
import logging
from typing import List, Optional, Union

import toposort

from .handler_phases import HandlerPhase, phase_from_legacy_int

_logger = logging.getLogger("ivpm.handlers.handler_order")

_PHASE_PREFIX = "phase:"
_END = "__end__"


class HandlerOrderError(Exception):
    """Raised when handler ordering cannot be resolved (cycle or bad phase)."""
    pass


def normalize_phase(value: Union[str, int]) -> str:
    """Coerce a handler's ``phase`` attribute to a named phase.

    ``int`` values are mapped through the legacy band table for backward
    compatibility; ``str`` values must name a known phase.
    """
    # bool is an int subclass; reject it explicitly as a programming error.
    if isinstance(value, bool):
        raise HandlerOrderError("phase must be a str or int, got bool")
    if isinstance(value, int):
        return phase_from_legacy_int(value)
    if isinstance(value, str):
        if not HandlerPhase.is_valid(value):
            raise HandlerOrderError(
                "unknown handler phase %r (valid: %s)"
                % (value, ", ".join(HandlerPhase.ORDER)))
        return value
    raise HandlerOrderError(
        "phase must be a str or int, got %s" % type(value).__name__)


def _node_key(h) -> str:
    """Stable graph-node key for a handler instance."""
    return type(h).name or type(h).__name__


def _legacy_int(h) -> int:
    """The handler's integer phase if it still uses one, else 0 (tie-break)."""
    raw = type(h).phase
    if isinstance(raw, bool):
        return 0
    return raw if isinstance(raw, int) else 0


def _phase_anchor(name: str) -> str:
    return _PHASE_PREFIX + name


def _resolve_phase_ref(target: str) -> str:
    """Validate a ``phase:<name>`` target and return the bare phase name."""
    name = target[len(_PHASE_PREFIX):]
    if not HandlerPhase.is_valid(name):
        raise HandlerOrderError(
            "unknown phase %r in constraint %r (valid: %s)"
            % (name, target, ", ".join(HandlerPhase.ORDER)))
    return name


def _resolve_after_ref(target: str, by_name: dict) -> Optional[str]:
    """Resolve a ``run_after`` target to the node it must follow.

    A ``phase:<name>`` target means "after all handlers in that phase", which
    is the chain anchor immediately following the phase. A handler-name target
    that is not present returns ``None`` (caller warns and drops it).
    """
    if target.startswith(_PHASE_PREFIX):
        name = _resolve_phase_ref(target)
        # The anchor right after this phase's handlers.
        return _phase_anchor(_chain_after(name))
    if target in by_name:
        return target
    return None


def _resolve_before_ref(target: str, by_name: dict) -> Optional[str]:
    """Resolve a ``run_before`` target to the node that must follow this one.

    A ``phase:<name>`` target means "before any handler in that phase", which
    is that phase's start anchor.
    """
    if target.startswith(_PHASE_PREFIX):
        name = _resolve_phase_ref(target)
        return _phase_anchor(name)
    if target in by_name:
        return target
    return None


def _chain_after(phase: str) -> str:
    """Return the chain node that immediately follows ``phase`` (or the end)."""
    idx = HandlerPhase.ordinal(phase)
    if idx + 1 < len(HandlerPhase.ORDER):
        return HandlerPhase.ORDER[idx + 1]
    return _END


def _sort_key(node: str, meta: dict):
    """Deterministic tie-break key, defined for both handler and anchor nodes."""
    if node in meta:
        return meta[node]
    name = node[len(_PHASE_PREFIX):]
    if name == _END:
        ordn = len(HandlerPhase.ORDER)
    else:
        ordn = HandlerPhase.ordinal(name)
    return (ordn, -1, node)


def _describe_cycle(e: 'toposort.CircularDependencyError') -> str:
    data = getattr(e, "data", None)
    if data:
        nodes = sorted(str(n) for n in data.keys())
        return ("handler ordering has a cycle among: %s. "
                "Check run_before/run_after constraints and phase assignments."
                % ", ".join(nodes))
    return "handler ordering has a cyclic dependency: %s" % e


def resolve_order(handlers: List) -> List:
    """Return ``handlers`` ordered for root post-load execution.

    Raises ``HandlerOrderError`` on an ordering cycle or an unknown phase name.
    Dangling ``run_after``/``run_before`` targets (a handler that is not
    present) are logged at WARNING and skipped.
    """
    by_name = {_node_key(h): h for h in handlers}

    # Phase anchors, chained in canonical order plus a terminal sentinel so
    # handlers in the last real phase still have a "next" anchor to bracket
    # against (and so run_after "phase:finalize" has a target).
    chain = list(HandlerPhase.ORDER) + [_END]
    anchors = [_phase_anchor(n) for n in chain]
    deps = {a: set() for a in anchors}
    for prev, cur in zip(anchors, anchors[1:]):
        deps[cur].add(prev)

    # node_key -> (ordinal, legacy_int, name) for tie-break.
    meta = {}

    # Bracket each handler between its phase anchor and the following one.
    for h in handlers:
        node = _node_key(h)
        phase = normalize_phase(type(h).phase)
        ordn = HandlerPhase.ordinal(phase)
        meta[node] = (ordn, _legacy_int(h), node)
        deps.setdefault(node, set()).add(_phase_anchor(phase))
        deps[_phase_anchor(_chain_after(phase))].add(node)

    # Relative constraints.
    for h in handlers:
        node = _node_key(h)
        for target in (type(h).run_after or []):
            ref = _resolve_after_ref(target, by_name)
            if ref is None:
                _logger.warning(
                    "handler '%s': run_after target '%s' not found; ignoring",
                    node, target)
                continue
            deps[node].add(ref)
        for target in (type(h).run_before or []):
            ref = _resolve_before_ref(target, by_name)
            if ref is None:
                _logger.warning(
                    "handler '%s': run_before target '%s' not found; ignoring",
                    node, target)
                continue
            deps.setdefault(ref, set()).add(node)

    try:
        levels = list(toposort.toposort(deps))
    except toposort.CircularDependencyError as e:
        raise HandlerOrderError(_describe_cycle(e)) from e

    ordered = []
    for level in levels:
        for node in sorted(level, key=lambda n: _sort_key(n, meta)):
            if node.startswith(_PHASE_PREFIX):
                continue
            ordered.append(by_name[node])
    return ordered
