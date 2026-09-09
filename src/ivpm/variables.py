"""IVPM variable declaration, resolution, and CLI parsing.

Variables are declared in the ``vars:`` block of ``ivpm.yaml`` and
referenced as ``${{name}}`` in scalar values throughout the file.
This module provides the resolution engine that expands those
references before the rest of the IVPM pipeline sees the data.

Two additions sit on top of that base:

* **Platform builtins** (``ivpm_os``, ``ivpm_arch``, ...) are seeded as
  ordinary declared variables whose default is the probed value, so they
  inherit the existing four-tier precedence with no new rules and can be
  overridden with ``-D`` / ``IVPM_VAR_*`` to resolve for another platform.
* **``match``** selects a value by exact string equality on a variable. It is
  value-producing, so it composes anywhere a value can appear.

A variable whose value came from a ``match``, and every builtin, is *derived*:
a function of the environment rather than a pinned choice. Derived names are
reported through ``derived_out`` so the caller can keep them out of persisted
state -- see project_ops and ``docs/source/variables.rst``.
"""
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import platform_info
from .platform_info import RESERVED_PREFIX
from .utils import fatal

# Matches either the escape sequence ``$${{`` or a variable reference
# ``${{name}}`` where *name* is a C-style identifier.
_VAR_RE = re.compile(r'\$\$\{\{|\$\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}')

_ENV_PREFIX = "IVPM_VAR_"

# Keys accepted inside a ``match:`` mapping.
_MATCH_KEYS = ("on", "cases", "default")

# YAML 1.1 -- which PyYAML implements -- resolves the *unquoted* plain scalars
# below to booleans. That is why ``on:`` arrives as the key ``True`` rather
# than "on", and why a case key written ``off:`` arrives as ``False``. Neither
# is something a manifest author should have to know, so both are mapped back
# here (see _match_body and _case_matches).
_YAML_TRUE = frozenset(("on", "yes", "true", "y"))
_YAML_FALSE = frozenset(("off", "no", "false", "n"))

# Attribute under which the substitution walk records the variable names it
# expanded within a node's subtree. An attribute rather than a dict key so the
# record never reaches key validation or the persisted document.
_USED_ATTR = "_ivpm_used_vars"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def resolve_variables(
    pkg_data: dict,
    cli_overrides: Optional[Dict[str, str]] = None,
    persisted_vars: Optional[Dict[str, str]] = None,
    derived_out: Optional[Set[str]] = None,
) -> Tuple[dict, Dict[str, str]]:
    """Resolve ``${{var}}`` references in *pkg_data* (mutated in place).

    1. Probe the platform and seed the ``ivpm_*`` builtins as declared
       variables whose default is the probed value.
    2. Extract and remove the ``vars:`` key from *pkg_data*, rejecting any
       user name under the reserved ``ivpm_`` prefix.
    3. Evaluate ``match`` nodes in ``vars:`` against the resolved builtins.
    4. Merge defaults with *cli_overrides*, env-var fallbacks, and
       *persisted_vars* using the four-tier precedence:
       CLI > ``IVPM_VAR_<NAME>`` > persisted > default.
    5. Walk the entire dict tree, evaluating ``match`` nodes and replacing
       ``${{var}}`` in all strings.
    6. Return ``(pkg_data, resolved_map)`` so the caller can persist
       the final values.

    *derived_out*, when supplied, is filled with the names whose value is a
    function of the environment (every builtin, plus every ``match``-produced
    variable). The return arity is deliberately unchanged: the two-tuple is
    the documented contract.

    Raises (via ``fatal()``) on:
    - ``${{name}}`` referencing an undeclared variable.
    - A CLI override naming a variable that is neither declared nor a builtin.
    - A user ``vars:`` name under the reserved ``ivpm_`` prefix.
    - A malformed or unsatisfied ``match``.
    """
    if cli_overrides is None:
        cli_overrides = {}
    if persisted_vars is None:
        persisted_vars = {}

    raw_vars = pkg_data.pop("vars", None)
    if raw_vars is None:
        raw_vars = {}

    builtins = platform_info.as_variables(platform_info.probe())
    derived: Set[str] = set(builtins.keys())

    # User-declared variables. Values stay un-coerced for now: a match node is
    # a mapping until it has been evaluated.
    user_declared: Dict[str, Any] = {}
    for k, v in raw_vars.items():
        name = str(k)
        if name.startswith(RESERVED_PREFIX):
            fatal("variable '%s' uses the reserved '%s' prefix" % (
                name, RESERVED_PREFIX), raw_vars)
        user_declared[name] = v

    # Validate that every CLI override names a variable we know about.
    # Builtins are declared names now, so ``-D ivpm_os=windows`` is accepted.
    for name in cli_overrides:
        if name not in user_declared and name not in builtins:
            fatal("Variable '%s' specified with -D but not declared "
                  "in vars: block of ivpm.yaml" % name)

    # Resolve the builtins first: a ``match`` in vars: reads them, and it must
    # see the overridden value so that -D ivpm_os=windows really does
    # cross-resolve.
    builtin_resolved = _merge_values(builtins, cli_overrides, persisted_vars)
    _recompute_platform(builtin_resolved, cli_overrides)

    # Evaluate match nodes in vars:. In v1 an ``on:`` may reference builtins
    # only, which makes this a single ordered pass with no cycle possible.
    for name, val in list(user_declared.items()):
        if _is_match_node(val):
            result = _eval_match(val, builtin_resolved, name, builtins_only=True)
            if isinstance(result, (dict, list)):
                fatal("match for variable '%s' produced a %s; a variable's "
                      "value must be a scalar" % (
                          name, type(result).__name__), val)
            user_declared[name] = result
            derived.add(name)

    declared: Dict[str, str] = dict(builtins)
    for name, val in user_declared.items():
        declared[name] = str(val)

    # Build final resolved map using four-tier precedence
    resolved = _merge_values(declared, cli_overrides, persisted_vars)
    _recompute_platform(resolved, cli_overrides)

    # Walk and substitute, evaluating any document-level match node against
    # the full variable map.
    _substitute_dict(pkg_data, resolved)

    if derived_out is not None:
        derived_out.update(derived)

    return (pkg_data, resolved)


def get_used_vars(node) -> Set[str]:
    """Variable names expanded anywhere within *node*'s subtree.

    Empty for a node the substitution walk never saw, or one that cannot
    carry attributes (a plain ``dict`` from a synthesized manifest).
    """
    return set(getattr(node, _USED_ATTR, ()) or ())


def parse_definitions(raw_list: List[str]) -> Dict[str, str]:
    """Parse ``["key=value", ...]`` into a dict.

    Each entry must contain at least one ``=``.  The key is everything
    before the first ``=``; the value is everything after.
    """
    result: Dict[str, str] = {}
    for entry in raw_list:
        if "=" not in entry:
            fatal("-D requires VAR=VALUE format, got: %s" % entry)
        key, _, val = entry.partition("=")
        if not key:
            fatal("-D requires a non-empty variable name: %s" % entry)
        result[key] = val
    return result


# ------------------------------------------------------------------
# match
# ------------------------------------------------------------------

def _is_match_node(v) -> bool:
    """True for a mapping whose *only* key is ``match``.

    Requiring it to be the sole key is what keeps this change inert: any
    other mapping -- including one that happens to contain a ``match`` key
    beside others -- is data and is left alone.
    """
    return isinstance(v, dict) and len(v) == 1 and "match" in v


def _match_body(raw_body: dict) -> dict:
    """The match body with YAML 1.1's boolean spellings mapped back to text.

    ``on:`` is the natural spelling and the one the design specifies, but
    PyYAML resolves the unquoted plain scalar ``on`` to ``True``. Requiring
    authors to write ``"on":`` would be a poor trade, so undo it here.
    """
    body = {}
    for k, v in raw_body.items():
        if k is True:
            k = "on"
        elif k is False:
            k = "off"
        body[str(k)] = v
    return body


def _case_matches(key, on: str) -> bool:
    """True when case *key* equals the resolved *on* value.

    Both sides are compared as strings: YAML reads an unquoted ``24.04`` as a
    float and ``on`` is always a string, so the coercion makes the unquoted
    form work. A key YAML turned into a bool matches any of the spellings it
    could have been written as -- which one it was is unrecoverable, and
    guessing wrong would silently drop a case.
    """
    if key is True:
        return on.lower() in _YAML_TRUE
    if key is False:
        return on.lower() in _YAML_FALSE
    return str(key) == on


def _case_label(key) -> str:
    """How a case key is spelled in a diagnostic."""
    if key is True:
        return "|".join(sorted(_YAML_TRUE))
    if key is False:
        return "|".join(sorted(_YAML_FALSE))
    return str(key)


def _eval_match(node, variables: Dict[str, str], ctx: str,
                builtins_only: bool = False,
                used: Optional[Set[str]] = None):
    """Evaluate one ``match`` node and return the selected value.

    *ctx* labels the node in diagnostics (a variable name, or a document key).
    *builtins_only* enforces the v1 restriction that a ``vars:`` match may
    only key off platform builtins. *used* accumulates every variable name
    read, including the ones ``on:`` referenced -- the choice depended on
    them just as much as an interpolation would have.
    """
    raw_body = node["match"]
    if not isinstance(raw_body, dict):
        fatal("'match' requires a mapping with 'on' and 'cases'", node)
    body = _match_body(raw_body)

    for key in body.keys():
        if key not in _MATCH_KEYS:
            fatal("unknown key '%s' in match; expected %s" % (
                key, ", ".join(_MATCH_KEYS)), raw_body)

    if "on" not in body or "cases" not in body:
        fatal("'match' requires both 'on' and 'cases'", raw_body)

    cases = body["cases"]
    if not isinstance(cases, dict):
        fatal("'cases' in match for '%s' must be a mapping" % ctx, raw_body)

    on_raw = str(body["on"])
    if builtins_only:
        for m in _VAR_RE.finditer(on_raw):
            name = m.group(1)
            if name is not None and not name.startswith(RESERVED_PREFIX):
                fatal("match 'on:' may reference platform builtins only "
                      "(got '${{%s}}')" % name, raw_body)
    on = _substitute_str(on_raw, variables, used)

    for k, v in cases.items():
        if _case_matches(k, on):
            return _eval_value(v, variables, ctx, builtins_only, used)

    if "default" in body:
        return _eval_value(body["default"], variables, ctx, builtins_only, used)

    fatal("no case matches '%s' for variable '%s' (cases: %s)" % (
        on, ctx, ", ".join(sorted(_case_label(k) for k in cases.keys()))),
        raw_body)


def _eval_value(v, variables: Dict[str, str], ctx: str, builtins_only: bool,
                used: Optional[Set[str]] = None):
    """Resolve a selected case value: nested matches first, then ``${{}}``.

    The result is fully resolved here, so the caller must not walk it again:
    a second substitution pass would expand a ``$${{`` escape that the first
    pass had just turned into a literal ``${{``.
    """
    if _is_match_node(v):
        return _eval_match(v, variables, ctx, builtins_only, used)
    if isinstance(v, str):
        return _substitute_str(v, variables, used)
    if isinstance(v, dict):
        for key in list(v.keys()):
            v[key] = _eval_value(v[key], variables, ctx, builtins_only, used)
        return v
    if isinstance(v, list):
        for i, item in enumerate(v):
            v[i] = _eval_value(item, variables, ctx, builtins_only, used)
        return v
    return v


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _recompute_platform(resolved: Dict[str, str], cli_overrides: Dict[str, str]):
    """Rebuild ``ivpm_platform`` from the *resolved* os and arch.

    It is a convenience spelling of ``"{ivpm_os}-{ivpm_arch}"``, so leaving it
    at the probed value after ``-D ivpm_os=macos`` would make the two disagree
    -- and ``ivpm_platform`` is what the lock's ``resolved_on`` records, so a
    cross-resolved macOS entry would be tagged with the Linux box that built
    it and then look native on the next bare run there.

    An explicit override of ``ivpm_platform`` itself still wins; deriving over
    the top of it would make the override unusable.
    """
    name = "ivpm_platform"
    if name not in resolved:
        return
    if name in cli_overrides or (_ENV_PREFIX + name.upper()) in os.environ:
        return
    resolved[name] = "%s-%s" % (resolved.get("ivpm_os", ""),
                                resolved.get("ivpm_arch", ""))


def _merge_values(
    declared: Dict[str, str],
    cli_overrides: Dict[str, str],
    persisted: Dict[str, str],
) -> Dict[str, str]:
    """Apply four-tier precedence for each declared variable.

    Order (highest wins): CLI > env (``IVPM_VAR_<NAME>``) > persisted > default.
    """
    resolved: Dict[str, str] = {}
    for name, default in declared.items():
        env_key = _ENV_PREFIX + name.upper()
        if name in cli_overrides:
            resolved[name] = cli_overrides[name]
        elif env_key in os.environ:
            resolved[name] = os.environ[env_key]
        elif name in persisted:
            resolved[name] = persisted[name]
        else:
            resolved[name] = default
    return resolved


def _note_used(node, used: Set[str]):
    """Record *used* on *node* for get_used_vars(). Best-effort by design:
    a plain dict cannot carry attributes, and a manifest that has no
    variables has nothing to record."""
    if not used:
        return
    try:
        setattr(node, _USED_ATTR, set(used))
    except AttributeError:
        pass


def _substitute_dict(d: dict, variables: Dict[str, str],
                     used: Optional[Set[str]] = None):
    """Recursively substitute ``${{var}}`` references in dict values.

    A value that is a match node is evaluated and replaced in place. The
    names expanded within this node's subtree are recorded on the node (see
    get_used_vars) and propagated to *used*.
    """
    mine: Set[str] = set()
    for key in list(d.keys()):
        val = d[key]
        if _is_match_node(val):
            # _eval_match returns a fully-resolved value; do not walk it again.
            d[key] = _eval_match(val, variables, str(key), used=mine)
            continue
        if isinstance(val, str):
            d[key] = _substitute_str(val, variables, mine)
        elif isinstance(val, dict):
            _substitute_dict(val, variables, mine)
        elif isinstance(val, list):
            _substitute_list(val, variables, mine)
    _note_used(d, mine)
    if used is not None:
        used.update(mine)


def _substitute_list(lst: list, variables: Dict[str, str],
                     used: Optional[Set[str]] = None):
    """Recursively substitute ``${{var}}`` references in list elements."""
    mine: Set[str] = set()
    for i, val in enumerate(lst):
        if _is_match_node(val):
            # Fully resolved by _eval_match; see _substitute_dict.
            lst[i] = _eval_match(val, variables, "[%d]" % i, used=mine)
            continue
        if isinstance(val, str):
            lst[i] = _substitute_str(val, variables, mine)
        elif isinstance(val, dict):
            _substitute_dict(val, variables, mine)
        elif isinstance(val, list):
            _substitute_list(val, variables, mine)
    _note_used(lst, mine)
    if used is not None:
        used.update(mine)


def _substitute_str(s: str, variables: Dict[str, str],
                    used: Optional[Set[str]] = None) -> str:
    """Replace ``${{var}}`` references in a single string.

    ``$${{`` produces a literal ``${{`` (escape).
    ``${{name}}`` is replaced by the resolved value.
    An undefined reference raises a fatal error.
    """
    def _replacer(m):
        if m.group(0) == '$${{':
            return '${{'
        name = m.group(1)
        if name not in variables:
            fatal("Undefined variable '${{%s}}' in ivpm.yaml" % name)
        if used is not None:
            used.add(name)
        return variables[name]

    return _VAR_RE.sub(_replacer, s)
