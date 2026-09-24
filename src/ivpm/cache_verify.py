#****************************************************************************
#* cache_verify.py
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
"""Cache verification: one problem model, three levels of checking.

Four things need to talk about a damaged cache -- the HIT check inside an
``ivpm update``, ``ivpm cache verify``, ``--repair``, and the JSON contract a
monitor consumes.  They all speak the vocabulary defined here (:class:`Problem`,
:class:`Finding`, :class:`VerifyResult`) rather than each inventing its own, so
the same defect has one name, one severity, and one prescribed repair no matter
which code path found it.

**Check mode never mutates.**  :func:`verify_cache` walks the filesystem itself
and calls only the store's pure readers.  This is deliberate and load-bearing:
several store methods mutate opportunistically as a side effect of being called
(``ensure_cache_dir`` sweeps stale staging, ``has_version`` -> ``touch_linked``
refreshes a sidecar), so a checker written casually against the store would
quietly modify the cache it was asked only to inspect -- the worst possible bug
in a tool whose entire value is being trustworthy about state.
"""
import os
import enum
import stat
import time
import shutil
import hashlib
import dataclasses as dc
from typing import Dict, List, Optional

from .diagnostics import Severity
from .protection import PARTITION_PREFIX
from .site_config import (CACHE_VERIFY_LEVELS, DEFAULT_CACHE_VERIFY_LEVEL,
                          resolve_cache_verify_level)
from .utils import sha256_file

LEVEL_OFF = "off"
LEVEL_SHAPE = "shape"
LEVEL_CONTENT = "content"


class Problem(enum.Enum):
    """Every way a cache can be wrong, named once."""

    ENTRY_EMPTY          = "entry-empty"
    ENTRY_SYMLINK        = "entry-symlink"
    MANIFEST_MISSING     = "manifest-missing"       # legacy entry
    MANIFEST_UNPARSEABLE = "manifest-unparseable"
    MANIFEST_MISMATCH    = "manifest-mismatch"      # package/version != location
    SOURCE_MISMATCH      = "source-mismatch"        # cache-key collision
    SHAPE_MISMATCH       = "shape-mismatch"         # counts/bytes differ
    CONTENT_MISMATCH     = "content-mismatch"       # merkle differs
    ENTRY_WRITABLE       = "entry-writable"         # seal drift: a writable file
    ENTRY_PERMS          = "entry-perms"            # seal drift: a directory mode
    SIDECAR_UNPARSEABLE  = "sidecar-unparseable"
    SIDECAR_ORPHAN       = "sidecar-orphan"
    STAGING_RESIDUE      = "staging-residue"
    TOMB_RESIDUE         = "tomb-residue"
    VERSION_KEY_INVALID  = "version-key-invalid"


# --- repair actions -------------------------------------------------------
REPAIR_EVICT    = "evict"
REPAIR_RESEAL   = "reseal"
REPAIR_REMOVE   = "remove"
REPAIR_BACKFILL = "backfill"
REPAIR_MANUAL   = "manual"

#: How each problem is fixed.  A property of the *problem*, decided once here
#: rather than re-derived at each call site -- otherwise the CLI, the update
#: path, and the repair loop would each be free to disagree about whether a
#: given defect warrants throwing content away.
REPAIR: Dict[Problem, str] = {
    # Content is untrustworthy, and is rebuildable from source.
    Problem.ENTRY_EMPTY:          REPAIR_EVICT,
    Problem.MANIFEST_UNPARSEABLE: REPAIR_EVICT,
    Problem.MANIFEST_MISMATCH:    REPAIR_EVICT,
    Problem.SOURCE_MISMATCH:      REPAIR_EVICT,
    Problem.SHAPE_MISMATCH:       REPAIR_EVICT,
    Problem.CONTENT_MISMATCH:     REPAIR_EVICT,
    # Content is fine; only the seal drifted.  chmod is enough -- evicting
    # would discard good bytes over a permission bit.
    Problem.ENTRY_WRITABLE:       REPAIR_RESEAL,
    Problem.ENTRY_PERMS:          REPAIR_RESEAL,
    # Not content at all; safe to delete.  A bad sidecar degrades to dir-mtime.
    Problem.STAGING_RESIDUE:      REPAIR_REMOVE,
    Problem.TOMB_RESIDUE:         REPAIR_REMOVE,
    Problem.SIDECAR_ORPHAN:       REPAIR_REMOVE,
    Problem.SIDECAR_UNPARSEABLE:  REPAIR_REMOVE,
    # A legacy entry: record a baseline, do not certify the past.
    Problem.MANIFEST_MISSING:     REPAIR_BACKFILL,
    # IVPM must not guess at an administrator's intent.
    Problem.ENTRY_SYMLINK:        REPAIR_MANUAL,
    Problem.VERSION_KEY_INVALID:  REPAIR_MANUAL,
}

#: Default severity per problem.  See the severity policy in the docs: an
#: entry that can serve wrong content is a warning even when self-healed,
#: because a shared cache producing bad entries is news; tidiness is a note.
SEVERITY: Dict[Problem, Severity] = {
    Problem.ENTRY_EMPTY:          Severity.WARNING,
    Problem.MANIFEST_UNPARSEABLE: Severity.WARNING,
    Problem.MANIFEST_MISMATCH:    Severity.WARNING,
    Problem.SOURCE_MISMATCH:      Severity.WARNING,
    Problem.SHAPE_MISMATCH:       Severity.WARNING,
    Problem.CONTENT_MISMATCH:     Severity.WARNING,
    Problem.VERSION_KEY_INVALID:  Severity.WARNING,
    Problem.ENTRY_SYMLINK:        Severity.ERROR,
    Problem.ENTRY_WRITABLE:       Severity.NOTE,
    Problem.ENTRY_PERMS:          Severity.NOTE,
    Problem.STAGING_RESIDUE:      Severity.NOTE,
    Problem.TOMB_RESIDUE:         Severity.NOTE,
    Problem.SIDECAR_ORPHAN:       Severity.NOTE,
    Problem.SIDECAR_UNPARSEABLE:  Severity.NOTE,
    # Expected during the migration window, and there may be hundreds.  INFO,
    # aggregated, so it informs without becoming noise.
    Problem.MANIFEST_MISSING:     Severity.INFO,
}

#: Problems that mean *an entry can serve content that is not what its key
#: promises*.  This set alone separates a cache that is untidy from one that is
#: incorrect, which is the distinction a CI gate needs to make.
SERVES_WRONG_CONTENT = frozenset({
    Problem.ENTRY_EMPTY,
    Problem.ENTRY_SYMLINK,
    Problem.MANIFEST_UNPARSEABLE,
    Problem.MANIFEST_MISMATCH,
    Problem.SOURCE_MISMATCH,
    Problem.SHAPE_MISMATCH,
    Problem.CONTENT_MISMATCH,
})

#: Problems that describe residue rather than an entry -- their bytes are pure
#: reclaimable waste.
RESIDUE = frozenset({
    Problem.STAGING_RESIDUE,
    Problem.TOMB_RESIDUE,
    Problem.SIDECAR_ORPHAN,
    Problem.SIDECAR_UNPARSEABLE,
})


class Status(enum.Enum):
    HEALTHY  = "HEALTHY"
    DEGRADED = "DEGRADED"
    BROKEN   = "BROKEN"


@dc.dataclass
class Finding:
    """One defect, at one place, with its prescribed repair."""

    problem: Problem
    package: str
    version: Optional[str]
    path: str
    detail: str
    severity: Severity = Severity.WARNING
    repair: str = REPAIR_MANUAL
    #: Bytes this finding would reclaim if repaired by removal/eviction.
    reclaimable: int = 0
    #: Owning uid, recorded for anything a user other than the owner must fix.
    uid: Optional[int] = None
    # --- filled in by the repair loop, not by verification ---
    attempted: bool = False
    repaired: bool = False
    repair_pass: Optional[int] = None
    repair_error: Optional[str] = None

    @property
    def key(self):
        """Identity for the repair loop's attempted-once bound.

        Keyed on the *problem* as well as the location: a re-appearing healthy
        entry produces no finding at all, so a concurrent update republishing
        something this run evicted is never mistaken for a failed repair.
        """
        return (self.package, self.version, self.problem)

    @property
    def auto_repairable(self) -> bool:
        return self.repair != REPAIR_MANUAL

    def to_json(self) -> dict:
        out = {
            "problem": self.problem.value,
            "package": self.package,
            "version": self.version,
            "path": self.path,
            "detail": self.detail,
            "severity": self.severity.name.lower(),
            "repair": self.repair,
            "reclaimable_bytes": self.reclaimable,
            "attempted": self.attempted,
            "repaired": self.repaired,
        }
        if self.uid is not None:
            out["uid"] = self.uid
        if self.repair_pass is not None:
            out["pass"] = self.repair_pass
        if self.repair_error:
            out["error"] = self.repair_error
        return out

    def message(self) -> str:
        """The four-part user-facing rendering: what, where, why, and how bad.

        A user who sees a package mysteriously re-fetch has nothing to act on
        unless the message names the entry, its path on disk, and the concrete
        discrepancy.
        """
        where = "%s/%s" % (self.package, self.version) if self.version \
            else self.package
        return ("cache: %s (%s)\n  at %s\n  %s"
                % (where, self.problem.value, self.path, self.detail))


def make_finding(problem: Problem, package: str, version: Optional[str],
                 path: str, detail: str, **kwargs) -> Finding:
    """A :class:`Finding` with the repair and severity tables already applied."""
    kwargs.setdefault("repair", REPAIR[problem])
    kwargs.setdefault("severity", SEVERITY[problem])
    return Finding(problem=problem, package=package, version=version,
                   path=path, detail=detail, **kwargs)


@dc.dataclass
class VerifyResult:
    """Everything one verification pass learned about a cache."""

    findings: List[Finding] = dc.field(default_factory=list)
    entries_checked: int = 0
    bytes_hashed: int = 0
    level: str = DEFAULT_CACHE_VERIFY_LEVEL
    elapsed_s: float = 0.0
    # --- inventory (what is here), independent of what is wrong with it ---
    packages: int = 0
    entries: int = 0
    total_bytes: int = 0
    sealed: int = 0
    legacy: int = 0
    cache_dir: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def status(self) -> Status:
        """HEALTHY / DEGRADED / BROKEN.

        The line between DEGRADED and BROKEN is the line between *untidy* and
        *incorrect*: DEGRADED is residue and permission drift that IVPM can
        clean up on its own; BROKEN means either an entry could hand a consumer
        the wrong bytes, or a human has to intervene.  A CI gate fails on
        BROKEN and warns on DEGRADED.
        """
        real = [f for f in self.findings if f.problem is not Problem.MANIFEST_MISSING]
        if not real:
            return Status.HEALTHY
        for f in real:
            if f.problem in SERVES_WRONG_CONTENT or not f.auto_repairable:
                return Status.BROKEN
        return Status.DEGRADED

    @property
    def reclaimable_bytes(self) -> int:
        return sum(f.reclaimable for f in self.findings)

    def by_problem(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for f in self.findings:
            counts[f.problem.value] = counts.get(f.problem.value, 0) + 1
        return counts

    def by_package(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for f in self.findings:
            counts[f.package] = counts.get(f.package, 0) + 1
        return counts

    def extend(self, other: 'VerifyResult'):
        self.findings.extend(other.findings)
        self.entries_checked += other.entries_checked
        self.bytes_hashed += other.bytes_hashed

    def to_json(self) -> dict:
        auto = sum(1 for f in self.findings if f.auto_repairable)
        return {
            "schema": 1,
            "cache_dir": self.cache_dir,
            "status": self.status.value,
            "level": self.level,
            "inventory": {"packages": self.packages, "entries": self.entries,
                          "bytes": self.total_bytes, "sealed": self.sealed,
                          "legacy": self.legacy},
            "entries_checked": self.entries_checked,
            "bytes_hashed": self.bytes_hashed,
            "elapsed_s": round(self.elapsed_s, 3),
            "totals": {"problems": len(self.findings),
                       "auto_repairable": auto,
                       "manual": len(self.findings) - auto,
                       "reclaimable_bytes": self.reclaimable_bytes},
            "by_problem": self.by_problem(),
            "findings": [f.to_json() for f in self.findings],
        }


# --------------------------------------------------------------------------
# Tree measurement
# --------------------------------------------------------------------------

@dc.dataclass
class TreeMeasurement:
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    merkle: Optional[str] = None
    bytes_hashed: int = 0
    #: Regular files that still carry a write bit (seal drift).
    writable: List[str] = dc.field(default_factory=list)
    #: Directories whose permission bits differ from the store's entry mode.
    bad_dirs: List[str] = dc.field(default_factory=list)
    #: Paths that could not be read at all.
    unreadable: List[str] = dc.field(default_factory=list)


def _check_dir(m: 'TreeMeasurement', dp: str):
    """Flag a directory that a reader could still write into.

    The test is *"has no write bit"*, not *"equals a fixed mode"*.  Comparing
    against a constant (the old ``_ENTRY_DIR_MODE``) forced every entry to the
    same permissions, which meant a site protecting content to one group had
    its restrictions reported as drift and "repaired" by widening them back to
    world-readable.  Sealing only ever removes write permission, so that is the
    only thing worth asserting.

    Symlinks are skipped.  ``os.walk`` classifies a symlink-to-directory into
    ``dirnames``, and its own mode is ``0777`` on Linux and meaningless -- so
    an entry containing a ``lib -> lib64`` link reported permanent drift that
    ``--repair`` could never fix, because the reseal chmod'd the *target* while
    the check ``lstat``-ed the link.
    """
    try:
        st = os.lstat(dp)
    except OSError:
        m.unreadable.append(dp)
        return
    if stat.S_ISLNK(st.st_mode):
        return
    if stat.S_IMODE(st.st_mode) & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        m.bad_dirs.append(dp)


def measure_tree(path: str, *, skip_name: Optional[str] = None,
                 hash_content: bool = False,
                 check_dir_seal: bool = False) -> TreeMeasurement:
    """Census one entry tree, by exactly the rule the seal used.

    This function and ``DirectoryCacheStore._make_readonly_and_measure`` must
    agree node for node, or every entry would fail its own shape check.  Three
    rules carry that agreement, and each one matters:

    * **``lstat``, never ``stat``.**  A symlink is counted as a file and
      contributes zero bytes -- its *target's* size is not part of this tree.
    * **Only regular files contribute bytes.**  Devices, fifos and sockets are
      counted but sized zero, because their ``st_size`` means something else.
    * **The manifest is excluded.**  It did not exist when the seal walked the
      tree, so counting it now would make every sealed entry look one file too
      large.

    With *hash_content*, also computes a merkle root over the tree: sorted
    ``relpath\\0kind\\0digest`` lines, so a rename, a retype, or a content
    change all move the root, and the result does not depend on walk order.
    """
    m = TreeMeasurement()
    lines = []
    if check_dir_seal:
        # The entry root is sealed too, and is the *only* directory in an entry
        # with no subdirectories -- so skipping it would make permission drift
        # undetectable for exactly the flat entries most packages produce.
        _check_dir(m, path)
    for root, dirnames, filenames in os.walk(path):
        for d in sorted(dirnames):
            m.dirs += 1
            dp = os.path.join(root, d)
            if check_dir_seal:
                _check_dir(m, dp)
        for f in sorted(filenames):
            if skip_name is not None and root == path and f == skip_name:
                continue
            m.files += 1
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, path)
            try:
                st = os.lstat(fp)
            except OSError:
                m.unreadable.append(fp)
                continue
            if stat.S_ISLNK(st.st_mode):
                if hash_content:
                    try:
                        lines.append("%s\0link\0%s" % (rel, os.readlink(fp)))
                    except OSError:
                        m.unreadable.append(fp)
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            m.bytes += st.st_size
            if st.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                m.writable.append(fp)
            if hash_content:
                try:
                    lines.append("%s\0file\0%s" % (rel, sha256_file(fp)))
                    m.bytes_hashed += st.st_size
                except OSError:
                    m.unreadable.append(fp)
    if hash_content:
        h = hashlib.sha256()
        for line in sorted(lines):
            h.update(line.encode("utf-8", "surrogateescape"))
            h.update(b"\n")
        m.merkle = h.hexdigest()
    return m


def entry_merkle(path: str, skip_name: Optional[str] = None) -> str:
    """The merkle root of an entry tree (used at seal time and at verify time)."""
    return measure_tree(path, skip_name=skip_name, hash_content=True).merkle


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def verify_entry(store, package_name: str, version: str,
                 level: str = LEVEL_SHAPE,
                 source: Optional[dict] = None) -> VerifyResult:
    """Check one cache entry.  Pure: never creates, removes, or touches.

    *source*, when given, is the resolved source of the dependency currently
    asking for this entry; a contradiction against the manifest is the
    cache-key collision that :data:`Problem.SOURCE_MISMATCH` names.  It is
    omitted by a whole-cache scan, which has no requester to compare against.
    """
    result = VerifyResult(level=level, cache_dir=store.cache_dir)
    path = store.get_version_cache_dir(package_name, version)
    _verify_one(store, result, package_name, version, path, level, source)
    return result


def _verify_one(store, result: VerifyResult, package_name: str, version: str,
                path: str, level: str, source: Optional[dict]):
    add = result.findings.append

    if os.path.islink(path):
        add(make_finding(
            Problem.ENTRY_SYMLINK, package_name, version, path,
            "the entry is a symlink to %s, not a directory published by IVPM; "
            "IVPM will not follow it and will not remove it"
            % _readlink(path), uid=_uid(path)))
        return
    if not os.path.isdir(path):
        return                      # nothing here: not a problem, just absent

    result.entries_checked += 1

    try:
        names = os.listdir(path)
    except OSError as e:
        add(make_finding(Problem.ENTRY_EMPTY, package_name, version, path,
                         "the entry cannot be read (%s)" % e, uid=_uid(path)))
        return
    if not names:
        add(make_finding(Problem.ENTRY_EMPTY, package_name, version, path,
                         "the entry directory is empty; it is a crash leftover "
                         "rather than published content"))
        return

    manifest_path = store.entry_manifest_path(path)
    manifest = store.read_entry_manifest(path)
    if manifest is None:
        if os.path.lexists(manifest_path):
            add(make_finding(
                Problem.MANIFEST_UNPARSEABLE, package_name, version, path,
                "the entry manifest exists but is not readable JSON; what this "
                "entry holds cannot be established",
                reclaimable=_size(store, path)))
            return
        if level != LEVEL_OFF:
            add(make_finding(
                Problem.MANIFEST_MISSING, package_name, version, path,
                "the entry predates entry manifests; its contents cannot be "
                "verified until it is re-published or backfilled"))
        # A legacy entry still gets its seal checked below -- permissions are
        # observable without a manifest -- but not its shape.
        manifest = None

    if manifest is not None:
        claimed_pkg = manifest.get("package")
        claimed_ver = manifest.get("version")
        # Compare against the *escaped* key: a lookup arrives with the raw
        # version, a cache scan reads it off the directory name, and the
        # manifest records the escaped form -- so normalize before comparing
        # or an escaped entry reads as a collision from one side only.
        from .utils import safe_version_key
        want_ver = safe_version_key(version) if version else version
        if (claimed_pkg is not None and claimed_pkg != package_name) or \
                (claimed_ver is not None and claimed_ver != want_ver):
            add(make_finding(
                Problem.MANIFEST_MISMATCH, package_name, version, path,
                "the entry says it is %s/%s but is stored as %s/%s; it was "
                "moved or hand-edited"
                % (claimed_pkg, claimed_ver, package_name, version),
                reclaimable=_size(store, path)))
            return
        if source:
            mismatch = _source_conflict(manifest.get("source") or {}, source)
            if mismatch:
                add(make_finding(
                    Problem.SOURCE_MISMATCH, package_name, version, path,
                    "the entry was published from a different source than the "
                    "one requesting it (entry: %s; requested: %s); the version "
                    "key does not distinguish these two sources" % mismatch,
                    reclaimable=_size(store, path)))
                return

    if level == LEVEL_OFF:
        # 'off' still catches a *contradicted* entry, because that costs one
        # small file read and is the only defense against a cache-key
        # collision.  What it skips is the walk: no shape, no content, no seal
        # check.  Turning verification off is a statement about cost, not a
        # request to hand over bytes that say they are something else.
        return

    want_hash = (level == LEVEL_CONTENT
                 and manifest is not None
                 and manifest.get("content", {}).get("merkle"))
    m = measure_tree(path, skip_name=store._ENTRY_MANIFEST,
                     hash_content=bool(want_hash),
                     check_dir_seal=True)
    result.bytes_hashed += m.bytes_hashed

    if manifest is not None:
        content = manifest.get("content") or {}
        expected = (content.get("files"), content.get("dirs"), content.get("bytes"))
        if None not in expected:
            actual = (m.files, m.dirs, m.bytes)
            if actual != expected:
                add(make_finding(
                    Problem.SHAPE_MISMATCH, package_name, version, path,
                    "expected %d files / %d dirs / %d bytes, found "
                    "%d files / %d dirs / %d bytes"
                    % (expected + actual),
                    reclaimable=m.bytes))
                return
        if want_hash and m.merkle != content.get("merkle"):
            add(make_finding(
                Problem.CONTENT_MISMATCH, package_name, version, path,
                "the entry's content hash does not match the one recorded when "
                "it was published (expected %s, found %s); the shape is intact, "
                "so the bytes themselves changed"
                % (_short(content.get("merkle")), _short(m.merkle)),
                reclaimable=m.bytes))
            return

    # Seal drift.  Reported after content, and never instead of it: a tree that
    # failed a shape or content check must be evicted, not resealed.
    if m.writable:
        add(make_finding(
            Problem.ENTRY_WRITABLE, package_name, version, path,
            "%d file(s) in this read-only entry are writable, starting with "
            "%s; shared cached content can be edited in place"
            % (len(m.writable), os.path.relpath(m.writable[0], path)),
            uid=_uid(m.writable[0])))
    if m.bad_dirs:
        add(make_finding(
            Problem.ENTRY_PERMS, package_name, version, path,
            "%d director(ies) in this read-only entry are writable, starting "
            "with %s; content can be unlinked or replaced through them"
            % (len(m.bad_dirs),
               os.path.relpath(m.bad_dirs[0], path) if m.bad_dirs[0] != path else "."),
            uid=_uid(m.bad_dirs[0])))


def verify_cache(store, level: Optional[str] = None,
                 package: Optional[str] = None,
                 mutate: bool = False) -> VerifyResult:
    """Check a whole cache (or one package within it) without touching it.

    Walks ``<cache>/<pkg>/`` with :func:`os.scandir` and calls only the store's
    pure readers.  It never calls ``ensure_cache_dir`` (which sweeps), never
    touches a sidecar, and never removes anything -- including on a cache that
    is *full of* residue, which is exactly the state that tempts an
    opportunistic sweep.
    """
    if mutate:
        raise ValueError(
            "verify_cache never mutates; use repair_cache for repairs")

    level = level or resolve_cache_verify_level()
    started = time.time()
    result = VerifyResult(level=level, cache_dir=store.cache_dir)

    if not store.cache_dir or not os.path.isdir(store.cache_dir):
        result.elapsed_s = time.time() - started
        return result

    for pkg_name in sorted(_listdir(store.cache_dir)):
        if package is not None and pkg_name != package:
            continue
        pkg_dir = os.path.join(store.cache_dir, pkg_name)
        if not os.path.isdir(pkg_dir) or os.path.islink(pkg_dir):
            continue
        result.packages += 1
        # A package directory now holds either entries directly (no policy) or
        # protection partitions, each holding its own entries.  Both shapes are
        # verified with the same code -- a partition IS just a package
        # directory that happens to be protected -- so nothing below has to
        # know which layout it is looking at.
        _verify_package(store, result, pkg_name, pkg_dir, level)
        for part in store._partition_dirs(pkg_dir):
            _verify_package(store, result, pkg_name, part, level)

    result.elapsed_s = time.time() - started
    return result


def _verify_package(store, result: VerifyResult, pkg_name: str, pkg_dir: str,
                    level: str):
    add = result.findings.append
    names = sorted(_listdir(pkg_dir))
    versions = set()

    for name in names:
        path = os.path.join(pkg_dir, name)

        if name.endswith(store._META_SUFFIX):
            continue                       # handled in the sidecar pass below

        if store._is_transient(name):
            _check_residue(store, result, pkg_name, name, path)
            continue

        if name.startswith(PARTITION_PREFIX):
            continue          # a protection partition; verified in its own pass

        if name == store._POLICY_FILE:
            continue          # the partition's own description, not content

        if not os.path.isdir(path) and not os.path.islink(path):
            continue                       # a stray file, not our business

        if _bad_version_key(name):
            add(make_finding(
                Problem.VERSION_KEY_INVALID, pkg_name, name, path,
                "%r is not a usable version key; it is not something IVPM "
                "published and will never be looked up" % name,
                uid=_uid(path)))
            continue

        versions.add(name)
        if os.path.islink(path):
            # Reported (and not followed) by _verify_one.  Deliberately left
            # out of the inventory: measuring it would walk a tree this cache
            # does not own.
            _verify_one(store, result, pkg_name, name, path, level, None)
            continue

        result.entries += 1
        size = _size(store, path)
        result.total_bytes += size
        if os.path.isfile(store.entry_manifest_path(path)):
            result.sealed += 1
        else:
            result.legacy += 1
        _verify_one(store, result, pkg_name, name, path, level, None)

    # Sidecars: a *.meta.json with no entry beside it, or one that will not
    # parse.  Both are advisory metadata, so both are safe to delete -- an
    # entry with no sidecar simply degrades to dir-mtime for GC purposes.
    for name in names:
        if not name.endswith(store._META_SUFFIX):
            continue
        version = name[:-len(store._META_SUFFIX)]
        path = os.path.join(pkg_dir, name)
        if version not in versions:
            add(make_finding(
                Problem.SIDECAR_ORPHAN, pkg_name, version, path,
                "the sidecar has no entry beside it; %s was removed "
                "out-of-band" % version,
                reclaimable=_file_size(path)))
        elif store._read_meta_at(path) is None:
            add(make_finding(
                Problem.SIDECAR_UNPARSEABLE, pkg_name, version, path,
                "the sidecar is not readable JSON; this entry's age falls back "
                "to its directory mtime",
                reclaimable=_file_size(path)))


def _check_residue(store, result: VerifyResult, pkg_name: str, name: str,
                   path: str):
    """Report staging/tombstone residue -- but only once it is *stale*.

    A fresh staging tree is a build in flight, quite possibly one belonging to
    another user on a shared cache.  Reporting it would make a healthy busy
    cache look damaged, and (worse) invite ``--repair`` to delete a tree
    someone is actively writing.  The store's own sweep thresholds define
    "stale", so verification and the sweep can never disagree about what is
    abandoned.
    """
    if not os.path.isdir(path) or os.path.islink(path):
        return
    is_tomb = store._TOMB_MARKER in name
    max_age = store._STALE_TOMB_AGE_S if is_tomb else store._STALE_STAGING_AGE_S
    try:
        st = os.stat(path)
    except OSError:
        return
    age = time.time() - max(st.st_mtime, st.st_ctime)
    if age < max_age:
        return
    problem = Problem.TOMB_RESIDUE if is_tomb else Problem.STAGING_RESIDUE
    what = ("an evicted entry whose bytes were never deleted" if is_tomb
            else "an abandoned build tree from an interrupted run")
    result.findings.append(make_finding(
        problem, pkg_name, None, path,
        "%s, %s old" % (what, _duration(age)),
        reclaimable=_size(store, path), uid=_uid(path)))


# --------------------------------------------------------------------------
# Repair
# --------------------------------------------------------------------------

class Outcome(enum.Enum):
    CONVERGED = "CONVERGED"   # a verification pass found nothing
    STALLED   = "STALLED"     # progress stopped; only manual/already-tried work
    EXHAUSTED = "EXHAUSTED"   # max-passes reached with findings outstanding


@dc.dataclass
class RepairPass:
    number: int
    repaired: int = 0
    failed: int = 0
    skipped_manual: int = 0
    findings: List[Finding] = dc.field(default_factory=list)


@dc.dataclass
class RepairReport:
    outcome: Outcome
    passes: List[RepairPass]
    final: VerifyResult
    max_passes: int = 3
    dry_run: bool = False

    @property
    def repaired(self) -> int:
        return sum(p.repaired for p in self.passes)

    @property
    def failed(self) -> int:
        return sum(p.failed for p in self.passes)

    @property
    def manual(self) -> int:
        return sum(1 for f in self.final.findings if not f.auto_repairable)

    def to_json(self) -> dict:
        return {"outcome": self.outcome.value, "passes": len(self.passes),
                "max_passes": self.max_passes, "repaired": self.repaired,
                "failed": self.failed, "manual": self.manual,
                "dry_run": self.dry_run}


def repair_cache(store, level: Optional[str] = None,
                 package: Optional[str] = None, *,
                 max_passes: int = 3, dry_run: bool = False,
                 upgrade: bool = False,
                 on_pass=None) -> RepairReport:
    """Iterate verify -> repair -> re-verify until the cache converges.

    A single pass is not enough, because repairs change what is observable:
    evicting an entry orphans its sidecar, removing a tombstone can expose an
    empty version dir, and a reseal on a read-only mount changes nothing at
    all.  So this loops -- with four bounds, each closing a different way a
    loop could fail to terminate:

    1. *max_passes*, the blunt ceiling.
    2. **Attempted at most once per ``(package, version, problem)``.**  The
       important one: a reseal whose ``chmod`` raises ``EACCES`` is swallowed
       by design, so it "succeeds" while changing nothing; a pass-count bound
       alone would retry it until exhausted.  Attempted-once turns that into
       one attempt followed by an honest ``manual`` finding.
    3. **Fixed-point detection** -- a pass with no actionable findings stops
       immediately as ``STALLED`` rather than burning the remaining passes.
    4. **Always end on a pure verification pass**, so the report describes the
       cache as it actually is rather than as the last repair believed it
       left it.

    A shared cache has other writers, and an entry evicted in pass 1 may be
    legitimately republished by a concurrent ``ivpm update`` before pass 2.
    That is the system working.  The loop tells the two apart because the
    attempt key includes the *problem*: a healthy republished entry produces no
    finding at all, while a genuinely unrepairable one produces the same
    finding and is already in ``attempted``.
    """
    level = level or resolve_cache_verify_level()
    attempted = set()
    failed = set()
    passes: List[RepairPass] = []

    def _finish(outcome, result):
        # The final pass is a *pure* re-verification, so it re-derives every
        # finding from scratch -- including ones this run already tried and
        # failed to fix, which would otherwise reappear looking untouched and
        # auto-repairable, telling the user to run the very command that just
        # gave up on them.  Carry that knowledge across.
        for f in result.findings:
            if f.key in failed:
                f.attempted = True
                f.repair = REPAIR_MANUAL
                f.repair_error = ("a repair was attempted this run and did not "
                                  "take effect")
        return RepairReport(outcome, passes, result, max_passes, dry_run)

    for n in range(1, max_passes + 1):
        result = verify_cache(store, level, package=package)
        if not result.findings:
            return _finish(Outcome.CONVERGED, result)

        actionable = [f for f in result.findings
                      if f.auto_repairable
                      and f.key not in attempted
                      and (upgrade or f.problem is not Problem.MANIFEST_MISSING)]
        if not actionable:
            return _finish(Outcome.STALLED, result)

        attempted.update(f.key for f in actionable)
        p = _apply_repairs(store, actionable, n, dry_run=dry_run)
        if not dry_run:
            failed.update(f.key for f in actionable if not f.repaired)
        passes.append(p)
        if on_pass is not None:
            on_pass(p)

        if dry_run:
            # Later passes' findings depend on repairs that were not applied,
            # so a second pass would report fiction.  Say so by stopping here
            # rather than implying a convergence that was never tested.
            return _finish(Outcome.STALLED, result)

    final = verify_cache(store, level, package=package)
    if not final.findings:
        return _finish(Outcome.CONVERGED, final)
    return _finish(Outcome.EXHAUSTED, final)


def _apply_repairs(store, findings: List[Finding], number: int,
                   dry_run: bool = False) -> RepairPass:
    p = RepairPass(number=number, findings=findings)
    for f in findings:
        f.attempted = True
        f.repair_pass = number
        if dry_run:
            p.repaired += 1
            continue
        try:
            ok = _apply_one(store, f)
        except OSError as e:
            f.repair_error = str(e)
            ok = False
        f.repaired = ok
        if ok:
            p.repaired += 1
        else:
            p.failed += 1
            # Attempted and did not work: downgrade to manual so the report
            # names it as something a human has to look at, rather than
            # leaving it looking auto-repairable and untried.
            f.repair = REPAIR_MANUAL
    return p


def _apply_one(store, f: Finding) -> bool:
    """Perform one repair.  Never edits entry content in place.

    Every action here either removes an entry whole (an atomic rename, then a
    delete) or changes permissions.  No code path rewrites cached bytes, so a
    repair can never *create* a corrupt entry -- which is what makes it safe to
    run against a cache with active updates.
    """
    if f.repair == REPAIR_EVICT:
        return bool(store._evict(f.package, f.version))

    if f.repair == REPAIR_RESEAL:
        # Shape was already verified before we got here (a tree that failed a
        # shape or content check is evicted, never resealed), so the content
        # is known good and only its modes are wrong.
        store._make_readonly(f.path)
        m = measure_tree(f.path, skip_name=store._ENTRY_MANIFEST,
                         check_dir_seal=True)
        return not m.writable and not m.bad_dirs

    if f.repair == REPAIR_REMOVE:
        if os.path.isdir(f.path) and not os.path.islink(f.path):
            store._make_writable(f.path)
            shutil.rmtree(f.path, ignore_errors=True)
        else:
            try:
                os.remove(f.path)
            except FileNotFoundError:
                pass
        return not os.path.lexists(f.path)

    if f.repair == REPAIR_BACKFILL:
        # A baseline, not a certificate: this records what the entry holds
        # *now*, which is the best any observer can do for content published
        # before manifests existed.  It deliberately does not claim the entry
        # was ever verified.
        # Same ordering as publishing: seal everything but the root, write the
        # manifest into the still-writable root, then seal the root.  A sealed
        # root (2555) has no write bit for anyone, so doing it the other way
        # round leaves the entry exactly as unsealed as it was found.
        # Owner-write only, and the original mode restored by the reseal:
        # using _PKG_DIR_MODE here published a restricted legacy entry
        # world-readable as a side effect of backfilling its manifest.
        try:
            root_mode = stat.S_IMODE(os.lstat(f.path).st_mode)
            os.chmod(f.path, root_mode | stat.S_IRWXU)
        except OSError:
            root_mode = None
        counts = store._make_readonly_and_measure(f.path, seal_root=False)
        counts["merkle"] = store._entry_merkle(f.path)
        store._write_entry_manifest(f.path, f.package, f.version, None, counts)
        store._seal_entry_root(f.path, None, root_mode)
        return os.path.isfile(store.entry_manifest_path(f.path))

    return False


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _source_conflict(recorded: dict, current: dict):
    """(entry-source, requested-source) when the two contradict, else None.

    Delegates the *rule* to the provider so there is exactly one definition of
    what counts as a collision -- notably the content-addressed exemption, in
    which two URLs resolving to one commit hash are mirrors rather than a
    conflict.
    """
    from .cache_provider import _IDENTITY_FIELDS
    differing = [f for f in _IDENTITY_FIELDS
                 if recorded.get(f) and current.get(f)
                 and recorded[f] != current[f]]
    if not differing:
        return None
    return (", ".join("%s=%s" % (f, recorded[f]) for f in differing),
            ", ".join("%s=%s" % (f, current[f]) for f in differing))


def _bad_version_key(name: str) -> bool:
    """Is *name* something IVPM could never have published as a version?

    Dot-prefixed names are the interesting case: every piece of cache
    machinery is dot-prefixed and marked, so a dot-prefixed directory that is
    *not* recognized machinery is either residue from an older IVPM or
    something a person put there by hand.  Either way it will never be looked
    up, and IVPM should say so rather than silently carrying it forever.
    """
    return not name or name.startswith(".")


def _listdir(path: str) -> List[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def _size(store, path: str) -> int:
    try:
        return store._get_dir_size(path)
    except OSError:
        return 0


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _uid(path: str) -> Optional[int]:
    try:
        return os.lstat(path).st_uid
    except OSError:
        return None


def _readlink(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return "?"


def _short(digest) -> str:
    return (str(digest)[:12] + "...") if digest else "none"


def _duration(seconds: float) -> str:
    if seconds >= 24 * 3600:
        return "%dd" % (seconds // (24 * 3600))
    if seconds >= 3600:
        return "%dh" % (seconds // 3600)
    if seconds >= 60:
        return "%dm" % (seconds // 60)
    return "%ds" % seconds
