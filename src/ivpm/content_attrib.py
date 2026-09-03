#****************************************************************************
#* content_attrib.py
#*
#* Attributing a language-content install failure to the package that caused
#* it, and to the ivpm.yaml line that imported that package.
#*
#* The mechanism here is deliberately *structural*: every input handed to an
#* installer is recorded against the Package that contributed it, at the
#* moment it is emitted, and the import chain is walked from links the
#* resolver already established. Nothing here reads installer prose to decide
#* who is at fault. Parsing an installer's wording to find the culprit is the
#* same open-ended guessing game as tightening validation, moved one step
#* downstream: it works until the installer rewords a message, and it never
#* covers the failure modes nobody anticipated.
#*
#* Textual identification is still *used* -- see resolve_dist -- but only as a
#* fast path that names a candidate. When it produces nothing, the caller
#* falls back to a mechanism that cannot fail to produce an answer.
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
#****************************************************************************
import dataclasses as dc
import logging
import os
import re
import threading
from typing import Dict, List, Optional

from . import msg
from .handlers.scope_keys import pkg_key, resolver_key

_logger = logging.getLogger("ivpm.content_attrib")

# Enrollment reasons, in descending order of how much authority the user gave
# the decision. The severity of a manifest problem follows this: a package the
# user explicitly declared is one they are entitled to be told about loudly,
# while one IVPM merely guessed at is one it should be quiet about.
ENROLLED_SRC_TYPE = "src-type"   # src: pypi / src: npm -- unambiguous
ENROLLED_EXPLICIT = "explicit"   # type: python / type: node at the dep site
ENROLLED_PROVIDES = "provides"   # the package's own ivpm.yaml declared it
ENROLLED_PROBE    = "probe"      # IVPM found a manifest and guessed

_ENROLLMENT_ATTR = "_ivpm_enrollment"


def set_enrollment(pkg, language: str, reason: str, evidence: str = None) -> None:
    """Record *why* *pkg* was enrolled as carrying *language* content.

    Kept per-language rather than as one field on the package: a package can
    legitimately provide both Python and Node content, and can have been
    enrolled for each by a different route.

    *evidence* is the concrete thing that decided it -- for a probe, the file
    that was found. Naming it turns "IVPM thought this was a Python package"
    into "IVPM found a pyproject.toml in it", which is the difference between
    a claim the user can check and one they can only take on faith.
    """
    m = getattr(pkg, _ENROLLMENT_ATTR, None)
    if m is None:
        m = {}
        try:
            setattr(pkg, _ENROLLMENT_ATTR, m)
        except AttributeError:
            # A caller passed something that cannot carry the annotation (a
            # frozen stand-in in a test). Attribution degrades; it never fails.
            return
    m[language] = (reason, evidence)


def get_enrollment(pkg, language: str) -> Optional[str]:
    entry = (getattr(pkg, _ENROLLMENT_ATTR, None) or {}).get(language)
    return entry[0] if entry else None


def get_evidence(pkg, language: str) -> Optional[str]:
    entry = (getattr(pkg, _ENROLLMENT_ATTR, None) or {}).get(language)
    return entry[1] if entry else None


def normalize_dist(name: str) -> str:
    """PEP 503 normalization, applied to node names too (harmlessly)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

@dc.dataclass
class ContentOrigin:
    """One input handed to an installer, and the package that contributed it.

    Recorded at emit time, where the Package is already in hand, so it is
    exact by construction and cannot drift out of date.
    """
    pkg         : object          # the contributing Package
    spec        : str             # the exact line / dependency value emitted
    group       : str             # requirements file path, or "node"
    enrolled_by : str             # one of the ENROLLED_* reasons
    dist        : Optional[str] = None   # installer-visible name, if known
    evidence    : Optional[str] = None   # what decided the enrollment

    @property
    def name(self) -> str:
        return getattr(self.pkg, "name", "<unknown>")

    def matches_dist(self, dist_name: str) -> bool:
        want = normalize_dist(dist_name)
        if self.dist is not None and normalize_dist(self.dist) == want:
            return True
        return normalize_dist(self.name) == want


class OriginMap:
    """Every installer input emitted this run, indexed for lookup.

    One instance per handler per run. Populated by whatever writes the
    installer's input file; read when that installer fails.
    """

    def __init__(self):
        self._origins : List[ContentOrigin] = []
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._origins)

    def record(self, spec: str, pkg, group: str, language: str = None,
               enrolled_by: str = None, dist: str = None,
               evidence: str = None) -> ContentOrigin:
        """Record one emitted installer input against its contributing package.

        The enrollment reason is read off the package when *language* is given,
        so callers do not have to thread it through separately from where it
        was decided.
        """
        if enrolled_by is None and language is not None:
            enrolled_by = get_enrollment(pkg, language)
            if evidence is None:
                evidence = get_evidence(pkg, language)
        if enrolled_by is None:
            enrolled_by = ENROLLED_PROBE
        origin = ContentOrigin(pkg=pkg, spec=spec, group=group,
                               enrolled_by=enrolled_by, dist=dist,
                               evidence=evidence)
        with self._lock:
            self._origins.append(origin)
        return origin

    def all(self) -> List[ContentOrigin]:
        return list(self._origins)

    def by_spec(self, spec: str) -> Optional[ContentOrigin]:
        spec = spec.strip()
        for o in self._origins:
            if o.spec.strip() == spec:
                return o
        return None

    def by_group(self, group: str) -> List[ContentOrigin]:
        return [o for o in self._origins if o.group == group]

    def by_pkg(self, pkg) -> List[ContentOrigin]:
        return [o for o in self._origins if o.pkg is pkg]

    def resolve_dist(self, dist_name: str,
                     group: str = None) -> Optional[ContentOrigin]:
        """The origin an installer-reported distribution name refers to.

        This is the *fast path*: when an installer happens to name the package
        it choked on, we can skip straight to the answer. A miss is entirely
        expected and is not an error -- the caller falls back to isolation.
        """
        if not dist_name:
            return None
        candidates = self.by_group(group) if group is not None else self._origins
        for o in candidates:
            if o.matches_dist(dist_name):
                return o
        # A name reported by the installer may be a transitive dependency
        # nobody declared. Say nothing rather than blame the nearest match.
        return None


def probe_allowed(pkg, language: str) -> bool:
    """May *pkg* be auto-detected as carrying *language* content?

    Three things stop a probe, and none of them is another language's
    business:

    - the decision for *this* language has already been made (explicitly, by
      the provider, or by an earlier probe), or
    - the package's own ivpm.yaml has a ``provides:`` that does not list this
      language. Having said what it provides, it has said what it does not:
      ``provides: []`` is a package declaring it provides nothing, which is
      the cheapest possible fix for one that keeps being mis-detected. An
      *absent* ``provides:`` is not this -- it means the package said nothing,
      so the probe still runs. Collapsing the two would throw away the only
      way a package can opt out.
    - the package is declared ``type: raw``, which is the consumer-side
      escape hatch for "this is not a Python/Node package, stop guessing".

    Notably absent: ``pkg_type``. That is a single slot shared by every
    language handler, and gating on it meant ``type: python`` silently
    switched off Node auto-detection and vice versa. One language's
    declaration disabling another language's detection is never what the user
    asked for -- and the failure is invisible, since the content simply does
    not get installed.
    """
    if get_enrollment(pkg, language) is not None:
        return False

    provides = getattr(pkg, "provides_declared", None)
    if provides is not None and language not in provides:
        return False

    for td in (getattr(pkg, "type_data", None) or []):
        if getattr(td, "type_name", None) == "raw":
            return False

    pkg_type = getattr(pkg, "pkg_type", None)
    if pkg_type is not None and str(getattr(pkg_type, "name", pkg_type)).lower() == "raw":
        return False

    return True


def report_probe_adoption(pkg, language: str, evidence: str = None,
                          strict: bool = False) -> None:
    """Note that an IVPM-aware package was enrolled by guesswork.

    A package that ships an ivpm.yaml is one whose author can say what it
    provides, in one line, at no cost. When such a package is instead enrolled
    by probing, that is a gap worth naming -- it keeps the probe fallthrough a
    *migration state* rather than settling into a permanent second tier, and
    it produces the adoption evidence needed to revisit whether probing should
    remain the default at all.

    Deliberately silent for a package with no ivpm.yaml: nothing is being
    asked of that author, so a note would be advice the reader cannot act on.
    Deliberately a note and not a warning: nothing is wrong, and warning about
    a working package on every update is how users learn to skim warnings.
    """
    if not has_own_manifest(pkg):
        return
    if getattr(pkg, "provides_declared", None) is not None:
        return

    name = getattr(pkg, "name", "<unknown>")
    found = (" (found %s)" % evidence) if evidence else ""
    text = ("package '%s' has an ivpm.yaml but no 'provides:'; its %s content "
            "was decided by auto-detection%s. Add 'provides: [%s]' to its "
            "ivpm.yaml to make this explicit." % (name, language, found,
                                                  language))
    loc = getattr(pkg, "srcinfo", None)
    if strict:
        msg.error(text, loc)
    else:
        msg.note(text, loc)


def has_own_manifest(pkg) -> bool:
    """True when *pkg* ships an ivpm.yaml of its own."""
    path = getattr(pkg, "path", None)
    return bool(path) and os.path.isfile(os.path.join(path, "ivpm.yaml"))


# --------------------------------------------------------------------------
# Manifest problems: severity follows authority
# --------------------------------------------------------------------------

def provider_manifest_loc(pkg):
    """The provider's own ivpm.yaml, when it has one.

    A self-declared type is a claim the *package* made, so a diagnostic about
    it belongs at the package's file, not at the line that imported it. The
    importer did nothing wrong and can do nothing about it.
    """
    path = getattr(pkg, "path", None)
    if not path:
        return None
    candidate = os.path.join(path, "ivpm.yaml")
    return candidate if os.path.isfile(candidate) else None


def report_manifest_problem(pkg, diag, language: str, strict: bool = False
                            ) -> bool:
    """Report a manifest that cannot be installed from. True ⇒ still enroll.

    Severity follows *authority* -- how firmly the user said this package
    carries this content:

    ==============  ==========================================================
    explicit        fatal, at the dependency entry. The user asked for this
                    package to be installed as `language` content and it
                    cannot be. Proceeding would silently do nothing.
    src-type        fatal, same reasoning.
    provides        fatal, at the provider's own ivpm.yaml. The package
                    claimed to provide content it cannot deliver; the
                    importer is not at fault and cannot fix it.
    probe           decline to enroll, and do not be loud about it -- IVPM
                    guessed, and a wrong guess costs nothing if it is quiet.
                    Split by what was actually wrong:
                      malformed  -> warning. A file on disk is broken; rare,
                                    and silence here reproduces exactly the
                                    complaint this work exists to fix.
                      not a target -> note (`-v`), error under --strict. The
                                    steady state in any workspace with foreign
                                    dependencies. A warning per dependency on
                                    every update is noise, and noise is what
                                    teaches users to skim the warnings that
                                    matter.
    ==============  ==========================================================
    """
    enrolled_by = get_enrollment(pkg, language) or ENROLLED_PROBE
    name = getattr(pkg, "name", "<unknown>")

    if enrolled_by in (ENROLLED_EXPLICIT, ENROLLED_SRC_TYPE):
        msg.fatal(
            "package '%s' is declared as %s content, but its manifest %s\n"
            "  %s" % (name, language, diag.reason, diag.located()),
            getattr(pkg, "srcinfo", None))
        return False

    if enrolled_by == ENROLLED_PROVIDES:
        loc = provider_manifest_loc(pkg)
        where = ("\n  declared in %s" % loc) if loc else ""
        msg.fatal(
            "package '%s' declares that it provides %s content, but its "
            "manifest %s\n  %s%s" % (
                name, language, diag.reason, diag.located(), where),
            getattr(pkg, "srcinfo", None))
        return False

    # Probe. IVPM guessed; it declines and says so at a proportionate volume.
    text = ("not treating '%s' as a %s package: %s %s" % (
        name, language, diag.located(), diag.reason))
    loc = getattr(pkg, "srcinfo", None)
    if diag.malformed:
        msg.warning(text, loc)
    elif strict:
        msg.error(text, loc)
    else:
        msg.note(text, loc)
    return False


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------

# Outcome kinds. See isolate().
ISO_SINGLE   = "single"     # exactly one member fails on its own
ISO_SUBSET   = "subset"     # several, but not all, fail on their own
ISO_GROUP    = "group"      # every member fails on its own
ISO_CONFLICT = "conflict"   # each is fine alone; only together do they fail
ISO_SKIPPED  = "skipped"    # too many members to be worth re-running


@dc.dataclass
class IsolationResult:
    culprits  : List['ContentOrigin']
    kind      : str
    attempted : int = 0

    @property
    def conclusive(self) -> bool:
        """True when isolation narrowed the failure to specific packages."""
        return self.kind in (ISO_SINGLE, ISO_SUBSET)


def max_isolate() -> int:
    """How many members are worth re-running individually.

    Each attempt is a full installer invocation, so a large phase would take
    a long time to bisect. Overridable via ``IVPM_MAX_ISOLATE``; mirrors
    ``IVPM_MAX_DEP_DEPTH``.
    """
    raw = os.environ.get("IVPM_MAX_ISOLATE")
    if raw is None:
        return 32
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("IVPM_MAX_ISOLATE must be an integer, got '%s'", raw)
        return 32
    return value if value >= 1 else 32


def isolate(origins: List['ContentOrigin'], retry_one, limit: int = None
            ) -> IsolationResult:
    """Find which members of a failed batch fail on their own.

    This is the mechanism the whole design rests on. It answers *which*
    package, and it is entirely indifferent to *why* -- so unlike reading
    installer output, it does not have to be taught about each new failure
    mode, and it cannot be broken by an installer rewording a message.

    *retry_one* takes one origin and returns True if it installs cleanly by
    itself. It is expected to be expensive; ``limit`` bounds how many times it
    is called.

    The outcome is reported for what it is. A dependency cycle is collapsed
    into a single install phase, so several members failing alone does not
    make any one of them the culprit -- and a batch whose members are each
    fine alone is a conflict between them, which is a different problem with a
    different fix.
    """
    if limit is None:
        limit = max_isolate()

    if len(origins) <= 1:
        return IsolationResult(culprits=list(origins), kind=ISO_SINGLE,
                               attempted=0)

    if len(origins) > limit:
        return IsolationResult(culprits=[], kind=ISO_SKIPPED, attempted=0)

    failed = []
    attempted = 0
    for o in origins:
        attempted += 1
        try:
            ok = retry_one(o)
        except Exception as e:
            # A diagnostic pass must never replace the failure it is
            # diagnosing. Treat an unusable retry as no evidence either way.
            _logger.debug("isolation retry for %s raised: %s", o.name, e)
            ok = True
        if not ok:
            failed.append(o)

    if len(failed) == 1:
        kind = ISO_SINGLE
    elif not failed:
        kind = ISO_CONFLICT
    elif len(failed) == len(origins):
        kind = ISO_GROUP
    else:
        kind = ISO_SUBSET

    culprits = failed if failed else list(origins)
    if kind == ISO_GROUP:
        culprits = list(origins)

    return IsolationResult(culprits=culprits, kind=kind, attempted=attempted)


# How each outcome is explained. The wording commits to exactly what was
# established and no more: an honest "these three, and I cannot say which"
# leaves the user somewhere to go, while a confident wrong name does not.
_ISOLATION_TEXT = {
    ISO_SINGLE:   "a diagnostic re-run of %(attempted)d packages, installed "
                  "one at a time",
    ISO_SUBSET:   "a diagnostic re-run of %(attempted)d packages; these failed "
                  "individually",
    ISO_GROUP:    "a diagnostic re-run of %(attempted)d packages; every one of "
                  "them failed individually",
    ISO_CONFLICT: "a diagnostic re-run of %(attempted)d packages; each "
                  "installed cleanly alone, so the failure is a conflict "
                  "between them rather than a fault in any one",
    ISO_SKIPPED:  "the packages in this install phase",
}


def isolation_identified_by(result: 'IsolationResult') -> str:
    text = _ISOLATION_TEXT.get(result.kind, _ISOLATION_TEXT[ISO_SKIPPED])
    return text % {"attempted": result.attempted}


def isolation_note(result: 'IsolationResult', limit: int = None) -> Optional[str]:
    """Anything about the isolation pass the user needs told, or None.

    A skipped isolation says so: a bounded search that reports its bound looks
    like a bound, while one that stays quiet looks like a complete answer.
    """
    if result.kind == ISO_SKIPPED:
        return ("this install phase has more packages than IVPM will re-run "
                "individually (IVPM_MAX_ISOLATE=%d), so the failure was not "
                "narrowed to one of them" % (limit or max_isolate()))
    if result.attempted:
        return ("packages were re-installed individually to identify this; "
                "the environment reflects that diagnostic pass")
    return None


# --------------------------------------------------------------------------
# Import chain
# --------------------------------------------------------------------------

def import_chain(pkg, pkgs_by_key: Dict[str, object],
                 max_depth: int = None) -> List[object]:
    """The packages from the root down to *pkg*, inclusive.

    Walks the ``resolved_by``/``resolved_by_key`` links the resolver already
    set. Guarded against cycles (a mis-linked graph must not hang an error
    report) and against unbounded depth.
    """
    if max_depth is None:
        try:
            from .dep_scope import max_dep_depth
            max_depth = max_dep_depth()
        except Exception:
            max_depth = 64

    chain = [pkg]
    seen = {id(pkg)}
    seen_keys = {pkg_key(pkg)}
    cur = pkg

    while len(chain) <= max_depth:
        key = resolver_key(cur)
        if key is None or key in seen_keys:
            break
        parent = pkgs_by_key.get(key)
        if parent is None or id(parent) in seen:
            break
        chain.append(parent)
        seen.add(id(parent))
        seen_keys.add(key)
        cur = parent

    chain.reverse()
    return chain


def loc_of(pkg) -> Optional[str]:
    """``file:line:col`` for the dependency entry that declared *pkg*, or None.

    Not ``utils.getlocstr``: that assumes ``srcinfo`` is both present and
    non-None, which is untrue for a root package or a synthetic one.
    """
    si = getattr(pkg, "srcinfo", None)
    if si is None:
        return None
    filename = getattr(si, "filename", None)
    if not filename:
        return None
    lineno = getattr(si, "lineno", None)
    linepos = getattr(si, "linepos", None)
    if lineno is None:
        return filename
    if linepos is None:
        return "%s:%d" % (filename, lineno)
    return "%s:%d:%d" % (filename, lineno, linepos)


def render_chain(chain: List[object], indent: str = "    ") -> str:
    """Render an import chain as ``<where it was written>  ->  <what it named>``.

    Each line answers "which file, on which line, asked for this?" -- which is
    the only question a user can act on. A package with no source location
    (the root, or a synthetic package in a test) renders by name alone rather
    than being dropped: an incomplete chain still beats a missing one.
    """
    lines = []
    for p in chain:
        name = getattr(p, "name", "<unknown>")
        loc = loc_of(p)
        if loc is None:
            lines.append("%s%s" % (indent, name))
        else:
            lines.append("%s%-40s  ->  %s" % (indent, loc, name))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_ENROLLMENT_TEXT = {
    ENROLLED_SRC_TYPE: "it was declared with an explicit source type",
    ENROLLED_EXPLICIT: "the dependency entry declares 'type: %(language)s'",
    ENROLLED_PROVIDES: "its own ivpm.yaml declares that it provides "
                       "%(language)s content",
    ENROLLED_PROBE:    "IVPM found %(language)s metadata in it and enrolled "
                       "it automatically",
}


def enrollment_reason(language: str, enrolled_by: str,
                      evidence: str = None) -> str:
    text = _ENROLLMENT_TEXT.get(enrolled_by)
    if text is None:
        return "of an unrecorded decision"
    if enrolled_by == ENROLLED_PROBE and evidence:
        return ("IVPM found %s in it and enrolled it automatically" % evidence)
    return text % {"language": language}


def next_step(language: str, origin: 'ContentOrigin') -> str:
    """What the user can actually do about it, at a named file and line.

    Only offered for a probe: for the other routes the user already said what
    they meant, so the fix is in the package, not in the declaration -- and
    suggesting they retract a deliberate declaration would be wrong.
    """
    if origin.enrolled_by != ENROLLED_PROBE:
        return None
    loc = loc_of(origin.pkg)
    where = ("at %s" % loc) if loc else "at its dependency entry"
    return ("if '%s' does not provide %s content, say so %s:\n"
            "        - name: %s\n"
            "          type: raw" % (origin.name, language, where, origin.name))


def format_content_failure(language: str,
                           origins: List['ContentOrigin'],
                           pkgs_by_key: Dict[str, object],
                           group: str = None,
                           output: str = None,
                           identified_by: str = None,
                           installer: str = None,
                           note_text: str = None) -> str:
    """Render the attribution report.

    *origins* is who is being reported. One entry is the common case; several
    means the failure could not be reduced further, and every contributor is
    named rather than one being picked arbitrarily.
    """
    pkgs_by_key = pkgs_by_key or {}
    parts = []

    if len(origins) == 1:
        parts.append("failed to install %s content for package '%s'"
                     % (language, origins[0].name))
    elif origins:
        parts.append("failed to install %s content contributed by %d packages: %s"
                     % (language, len(origins),
                        ", ".join(sorted(o.name for o in origins))))
    else:
        parts.append("failed to install %s content" % language)

    for o in origins:
        chain = import_chain(o.pkg, pkgs_by_key)
        parts.append("")
        if len(origins) > 1:
            parts.append("  '%s' imported by:" % o.name)
        else:
            parts.append("  imported by:")
        parts.append(render_chain(chain, indent="    "))
        parts.append("")
        parts.append("  '%s' was treated as a %s package because %s"
                     % (o.name, language,
                        enrollment_reason(language, o.enrolled_by, o.evidence)))

    if group:
        parts.append("")
        parts.append("  installer input: %s" % group)

    if installer:
        # The exact command and exit code. Not decoration: it is what the user
        # re-runs by hand when they want to see the failure for themselves.
        parts.append("  installer: %s" % installer)

    if identified_by:
        parts.append("")
        parts.append("  identified by: %s" % identified_by)

    if note_text:
        parts.append("")
        parts.append("  note: %s" % note_text)

    if output:
        parts.append("")
        parts.append("  installer output:")
        for line in output.strip().splitlines():
            parts.append("    %s" % line)

    steps = [s for s in (next_step(language, o) for o in origins) if s]
    if steps:
        parts.append("")
        parts.append("  next step:")
        for s in steps:
            parts.append("    %s" % s)

    return "\n".join(parts)


def report_content_failure(language: str,
                           origins: List['ContentOrigin'],
                           pkgs_by_key: Dict[str, object],
                           group: str = None,
                           output: str = None,
                           identified_by: str = None,
                           installer: str = None,
                           note_text: str = None) -> None:
    """Report an attributed content-install failure and raise.

    Located at the culprit's dependency entry when there is exactly one, so
    the diagnostic's own header points at the line the user must edit; the
    chain in the body covers the rest.
    """
    text = format_content_failure(
        language, origins, pkgs_by_key, group=group, output=output,
        identified_by=identified_by, installer=installer,
        note_text=note_text)
    loc = None
    if len(origins) == 1:
        loc = getattr(origins[0].pkg, "srcinfo", None)
    msg.fatal(text, loc)
