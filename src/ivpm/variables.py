"""IVPM variable declaration, resolution, and CLI parsing.

Variables are declared in the ``vars:`` block of ``ivpm.yaml`` and
referenced as ``${name}`` in scalar values throughout the file.
This module provides the resolution engine that expands those
references before the rest of the IVPM pipeline sees the data.
"""
import os
import re
from typing import Dict, List, Optional, Tuple

from .utils import fatal

# Matches either the escape sequence ``$${`` or a variable reference
# ``${name}`` where *name* is a C-style identifier.
_VAR_RE = re.compile(r'\$\$\{|\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}')

_ENV_PREFIX = "IVPM_VAR_"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def resolve_variables(
    pkg_data: dict,
    cli_overrides: Optional[Dict[str, str]] = None,
    persisted_vars: Optional[Dict[str, str]] = None,
) -> Tuple[dict, Dict[str, str]]:
    """Resolve ``${var}`` references in *pkg_data* (mutated in place).

    1. Extract and remove the ``vars:`` key from *pkg_data*.
    2. Merge defaults with *cli_overrides*, env-var fallbacks, and
       *persisted_vars* using the four-tier precedence:
       CLI > ``IVPM_VAR_<NAME>`` > persisted > default.
    3. Walk the entire dict tree replacing ``${var}`` in all strings.
    4. Return ``(pkg_data, resolved_map)`` so the caller can persist
       the final values.

    Raises (via ``fatal()``) on:
    - ``${name}`` referencing an undeclared variable.
    - A CLI override naming a variable not present in ``vars:``.
    """
    if cli_overrides is None:
        cli_overrides = {}
    if persisted_vars is None:
        persisted_vars = {}

    raw_vars = pkg_data.pop("vars", None)
    if raw_vars is None:
        raw_vars = {}

    # Normalize defaults to strings
    declared: Dict[str, str] = {}
    for k, v in raw_vars.items():
        declared[str(k)] = str(v)

    # Validate that every CLI override names a declared variable
    for name in cli_overrides:
        if name not in declared:
            fatal("Variable '%s' specified with -D but not declared "
                  "in vars: block of ivpm.yaml" % name)

    # Build final resolved map using four-tier precedence
    resolved = _merge_values(declared, cli_overrides, persisted_vars)

    # Walk and substitute
    _substitute_dict(pkg_data, resolved)

    return (pkg_data, resolved)


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
# Internal helpers
# ------------------------------------------------------------------

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


def _substitute_dict(d: dict, variables: Dict[str, str]):
    """Recursively substitute ``${var}`` references in dict values."""
    for key in list(d.keys()):
        val = d[key]
        if isinstance(val, str):
            d[key] = _substitute_str(val, variables)
        elif isinstance(val, dict):
            _substitute_dict(val, variables)
        elif isinstance(val, list):
            _substitute_list(val, variables)


def _substitute_list(lst: list, variables: Dict[str, str]):
    """Recursively substitute ``${var}`` references in list elements."""
    for i, val in enumerate(lst):
        if isinstance(val, str):
            lst[i] = _substitute_str(val, variables)
        elif isinstance(val, dict):
            _substitute_dict(val, variables)
        elif isinstance(val, list):
            _substitute_list(val, variables)


def _substitute_str(s: str, variables: Dict[str, str]) -> str:
    """Replace ``${var}`` references in a single string.

    ``$${`` produces a literal ``${`` (escape).
    ``${name}`` is replaced by the resolved value.
    An undefined reference raises a fatal error.
    """
    def _replacer(m):
        if m.group(0) == '$${':
            return '${'
        name = m.group(1)
        if name not in variables:
            fatal("Undefined variable '${%s}' in ivpm.yaml" % name)
        return variables[name]

    return _VAR_RE.sub(_replacer, s)
