#****************************************************************************
#* install_spec.py
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
"""Parsing for ``ivpm install``'s interleaved source-spec grammar.

``install`` takes several manifests, each with its own dep-set selection::

    ivpm install -o /opt/eda \\
      --from https://edapack.github.io -d digital-sim -d digital-formal \\
      --from https://mycorp.internal/tools -d common

argparse cannot express "this option binds to the preceding positional", so
argv is split into groups at each ``--from`` *before* argparse sees it. Each
group is then parsed by a parser carrying only the per-source options, which
is what makes a mistyped option report "unrecognized arguments" instead of
being silently taken for something else.

Position alone cannot distinguish ``-o tools`` (global, just typed late) from
a mistyped per-source option, so the caller passes a table of the invocation's
global options -- see :func:`build_global_option_table`. Recognized globals
appearing inside a group are hoisted back out to the global argv; anything
else still errors, now naming which of the two lists the option belongs to.
Without that table the split stays purely positional and everything after a
``--from`` binds to it.

Everything here is pure: no I/O, no fetching. Alias defaulting that requires
reading a manifest is completed later by the caller via
:func:`assign_manifest_aliases`.
"""
import argparse
import dataclasses as dc
import re
from typing import Dict, List, Optional, Tuple

from .utils import fatal


#: Options that bind to a single source rather than the whole invocation.
PER_SOURCE_OPTIONS = ("-d", "--dep-set", "-D", "--define", "--as")

_FROM_FLAGS = ("--from",)


@dc.dataclass
class SourceSpec:
    """One ``--from`` group: a manifest plus the selections applied to it."""

    src: str
    #: Display/resolution name. ``--as`` when given, else the manifest's
    #: ``package.name``, else a slug of the URL. Filled in by
    #: :func:`assign_manifest_aliases` when not explicit.
    alias: Optional[str] = None
    #: True when *alias* came from ``--as`` (so manifest defaulting skips it).
    alias_explicit: bool = False
    dep_sets: List[str] = dc.field(default_factory=list)
    definitions: Dict[str, str] = dc.field(default_factory=dict)


def flatten_dep_sets(values) -> Optional[List[str]]:
    """Flatten a repeatable, comma-separated ``-d`` into an ordered list.

    ``-d a,b -d c`` and ``-d a -d b -d c`` both give ``["a", "b", "c"]``.
    Duplicates are dropped, first occurrence wins, order is preserved (it
    determines merge precedence downstream). ``None`` when nothing was
    selected, meaning "use the manifest's default dep-set".
    """
    if not values:
        return None
    out: List[str] = []
    for val in values:
        for name in val.split(","):
            name = name.strip()
            if name and name not in out:
                out.append(name)
    return out


def slugify_source(src: str) -> str:
    """Derive a readable alias from a manifest URL or path.

    Used only when a source's manifest declares no ``package.name`` and the
    user gave no ``--as``; the result appears in collision messages, so it
    needs to be recognizable rather than unique-by-construction.
    """
    s = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", src.strip())
    s = re.sub(r"[?#].*$", "", s)
    s = s.rstrip("/")
    s = re.sub(r"\.(ya?ml|json)$", "", s)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "source"


def _mk_source_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ivpm install --from", add_help=False)
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("-d", "--dep-set", dest="dep_set", action="append", default=[])
    p.add_argument("-D", "--define", dest="definitions", action="append", default=[])
    p.add_argument("--as", dest="alias", default=None)
    return p


def build_global_option_table(install_parser) -> Dict[str, int]:
    """Map each of ``install``'s global option strings to its argument count.

    ``{"-o": 1, "--ssh": 0, ...}``. ``--from`` and the per-source options are
    excluded -- they are the grammar the split implements, not options it can
    hoist.

    An extension registering an option that collides with a per-source name is
    a hard error rather than a silent shadowing: the split would hoist it out
    of the group it was typed in, and the user would see a dep-set selection
    quietly go missing.
    """
    table: Dict[str, int] = {}
    if install_parser is None:
        return table
    for action in install_parser._actions:
        nargs = action.nargs
        if nargs is None:
            count = 1
        elif isinstance(nargs, int):
            count = nargs
        else:
            # '?', '*', '+' -- variable arity can't be split reliably, and no
            # install option uses it. Leave such options unhoistable.
            continue
        for opt in action.option_strings:
            if opt in _FROM_FLAGS or opt.startswith("--from"):
                continue
            if opt in PER_SOURCE_OPTIONS:
                fatal("option %s is registered both as a global 'install' "
                      "option and as a per-source option; an ivpm extension "
                      "must choose a different name." % opt)
            table[opt] = count
    return table


def _global_token_arity(tok: str,
                        global_options: Dict[str, int]) -> Optional[int]:
    """Tokens *following* ``tok`` that it consumes, or ``None`` if not global.

    Covers the attached forms (``--outdir=x``, ``-ox``) and short flag
    clusters (``-vv``), all of which consume nothing beyond themselves.
    """
    if len(tok) < 2 or not tok.startswith("-"):
        return None
    if tok in global_options:
        return global_options[tok]
    if tok.startswith("--"):
        base = tok.split("=", 1)[0]
        return 0 if "=" in tok and global_options.get(base, 0) > 0 else None
    if global_options.get(tok[:2], 0) > 0:
        return 0
    if len(tok) > 2 and all(global_options.get("-" + c) == 0 for c in tok[1:]):
        return 0
    return None


def split_source_groups(
        argv: List[str],
        global_options: Optional[Dict[str, int]] = None
        ) -> Tuple[List[str], List[List[str]]]:
    """Split *argv* into ``(global_argv, [source_argv, ...])``.

    A group starts at each ``--from`` and runs to the next one. Tokens before
    the first ``--from`` are global.

    When *global_options* is given (see :func:`build_global_option_table`),
    options it names are hoisted out of whatever group they land in, so
    ``--from X -d sim -o tools`` means the same as ``-o tools --from X -d
    sim``. Without it the split is purely positional.

    Per-source options appearing before the first ``--from`` remain a hard
    error: silently promoting them to global would apply a dep-set selection
    to every source, which is the opposite of what was typed.
    """
    global_argv: List[str] = []
    groups: List[List[str]] = []
    cur: Optional[List[str]] = None

    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _FROM_FLAGS or tok.startswith("--from="):
            cur = [tok]
            groups.append(cur)
            # A bare '--from' owns the next token unconditionally, so a
            # manifest path that happens to look like an option still binds.
            if tok in _FROM_FLAGS and i + 1 < len(argv):
                cur.append(argv[i + 1])
                i += 1
        elif cur is None:
            global_argv.append(tok)
        else:
            n = _global_token_arity(tok, global_options or {})
            if n is None:
                cur.append(tok)
            else:
                global_argv.extend(argv[i:i + 1 + n])
                i += n
        i += 1

    for tok in global_argv:
        base = tok.split("=", 1)[0]
        if base in PER_SOURCE_OPTIONS:
            fatal("%s applies to a single source and must follow a --from. "
                  "Move it after the --from it belongs to." % base)

    return global_argv, groups


def parse_source_groups(
        groups: List[List[str]],
        global_options: Optional[Dict[str, int]] = None) -> List[SourceSpec]:
    """Parse each ``--from`` group into a :class:`SourceSpec`."""
    parser = _mk_source_parser()
    specs: List[SourceSpec] = []

    for group in groups:
        ns, extra = parser.parse_known_args(group)
        if extra:
            _report_unknown_group_options(extra, ns.src, global_options or {})
        if not ns.src:
            fatal("--from requires a manifest URL or path")

        from .variables import parse_definitions
        specs.append(SourceSpec(
            src=ns.src,
            alias=ns.alias,
            alias_explicit=ns.alias is not None,
            dep_sets=flatten_dep_sets(ns.dep_set) or [],
            definitions=parse_definitions(ns.definitions),
        ))

    _check_duplicate_aliases([s for s in specs if s.alias_explicit])
    return specs


def _report_unknown_group_options(extra: List[str], src: str,
                                  global_options: Dict[str, int]):
    """Fail on leftover tokens in a ``--from`` group, saying which list they
    belong to.

    A recognized global reaching here means the caller split without a global
    option table, so the actionable advice is "move it before the first
    ``--from``" rather than "no such option" -- the option exists and is
    spelled correctly.
    """
    opts = [t for t in extra if t.startswith("-") and len(t) > 1] or extra
    misplaced = [t for t in opts
                 if _global_token_arity(t, global_options) is not None]
    if misplaced:
        fatal("%s applies to the whole install, not to one source. Move it "
              "before the first --from." % ", ".join(misplaced))
    fatal("unknown option %s in the '--from %s' group. Per-source options "
          "are: %s" % (", ".join(opts), src, ", ".join(PER_SOURCE_OPTIONS)))


def _check_duplicate_aliases(specs: List[SourceSpec]):
    seen: Dict[str, SourceSpec] = {}
    for s in specs:
        prior = seen.get(s.alias)
        if prior is not None:
            fatal("two sources resolve to the alias '%s' (%s and %s). "
                  "Give one of them a distinct --as." % (
                      s.alias, prior.src, s.src))
        seen[s.alias] = s


def assign_manifest_aliases(specs: List[SourceSpec], manifest_names):
    """Complete alias defaulting once manifests have been read.

    *manifest_names* is a parallel sequence of each source's declared
    ``package.name`` (or ``None``). Precedence is ``--as`` > ``package.name``
    > URL slug. Raises on a duplicate, before any package work begins --
    ambiguous aliases would make every downstream collision message
    unactionable.
    """
    for spec, name in zip(specs, manifest_names):
        if spec.alias_explicit:
            continue
        spec.alias = name or slugify_source(spec.src)
    _check_duplicate_aliases(specs)
    return specs
