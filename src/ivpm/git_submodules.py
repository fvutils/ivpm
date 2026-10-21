#****************************************************************************
#* git_submodules.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""Submodules of git dependencies and of the root project.

Everything that runs ``git submodule`` lives here (see
git-submodules-design.md).  ``PackageGit`` and ``GitCloneProvider`` call in;
nothing else touches submodules.

Three pieces:

* :class:`SubmoduleSpec` -- what a manifest asks for (``submodules:``), parsed,
  validated and canonicalized.  Its :meth:`~SubmoduleSpec.spec_id` is part of
  the cache key and of the lock entry.
* :func:`walk` -- what is on disk: every declared submodule, recursively, with
  its gitlink, checkout state and whether the spec puts it in scope.  Local
  only (no network), and safe on a read-only cache entry.
* :func:`fetch` -- make the disk match the spec: ``git submodule update``, a
  verification walk, and a full-history retry for a submodule whose pinned
  commit the server would not serve shallowly.

The pin of a submodule is always its gitlink in the superproject; nothing here
moves a submodule anywhere else.
"""
import dataclasses as dc
import hashlib
import json
import logging
import os
import posixpath
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Tuple

_logger = logging.getLogger("ivpm.git_submodules")

#: What a git dependency with no ``submodules:`` key gets.
#:
#: ``"off"`` is git's own default: nothing is initialized unless asked for,
#: and a note names each package that has submodules it did not request.
#: ``"legacy"`` is the pre-2.42 behavior -- every submodule, recursively, full
#: depth (a failure a warning, never cached) -- kept selectable for tests and
#: as a fallback. A v1/v2 lock file is always reproduced with legacy
#: semantics, whatever this says (spec_from_lock).
IMPLICIT_DEFAULT = "off"

#: Keys of the long form of ``submodules:``.
SPEC_KEYS = frozenset({"recursive", "paths", "depth", "jobs", "required"})

#: Prefix of every spec id, so the hashing scheme can change without an old
#: id ever being mistaken for a new one.
SPEC_ID_PREFIX = "sm1-"

#: Separator of the submodule part of a cache key (mirrors ``_patch_``).
SM_SEP = "_sm_"

#: Fallback job count when ``jobs`` is not given.
DEFAULT_MAX_JOBS = 4

#: Substrings of git's stderr that mean "the server would not serve the pinned
#: commit at the requested depth".  A full-history retry fixes these.
_SHALLOW_MISS = (
    "did not contain",
    "direct fetching of that commit failed",
    "not our ref",
    "unadvertised object",
)


class SubmoduleSpecError(ValueError):
    """A ``submodules:`` value that cannot be accepted."""


class SubmoduleFetchError(Exception):
    """Raised by callers that want to abort on a failed :func:`fetch`."""

    def __init__(self, message: str, result: "FetchResult" = None):
        super().__init__(message)
        self.result = result


# --------------------------------------------------------------------------
# Spec
# --------------------------------------------------------------------------

@dc.dataclass(frozen=True)
class SubmoduleSpec:
    """A parsed ``submodules:`` setting.

    *depth* is ``None`` when the manifest did not set it (the default then
    depends on the package's cache mode, see :meth:`resolved_depth`), an int
    >= 1, ``"recommended"`` (honor ``shallow = true`` in ``.gitmodules``), or
    ``"full"`` -- never written by a user, used by the legacy spec and for a
    lock that recorded a full-depth fetch.

    *origin* says where the spec came from: ``explicit`` (the manifest),
    ``implicit`` (no key; :data:`IMPLICIT_DEFAULT` applied), ``defaults`` (the
    manifest's ``dep-defaults``), ``lock`` (a v3 lock entry) or
    ``lock-legacy`` (a v1/v2 lock entry, which predates the setting).
    """
    enabled: bool = False
    recursive: bool = True
    paths: Optional[Tuple[str, ...]] = None
    depth: object = None
    jobs: Optional[int] = None
    required: bool = True
    legacy: bool = False
    origin: str = "explicit"

    # -- identity --------------------------------------------------------- #

    def resolved_depth(self, cached: bool):
        """The depth actually fetched: an int, ``"recommended"`` or ``None``
        (full history).  Cached entries are read-only snapshots, so they
        default to depth 1, like the superproject; editable clones follow
        git's default of full history."""
        if self.depth == "full":
            return None
        if self.depth is None:
            return 1 if cached else None
        return self.depth

    def canonical(self, cached: bool) -> dict:
        """The content-affecting part of the spec, as recorded in a lock.

        ``jobs`` does not change what is fetched, and ``required`` does not
        change a successful tree, so neither is part of the identity."""
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "recursive": bool(self.recursive),
            "paths": sorted(self.paths) if self.paths is not None else None,
            "depth": self.resolved_depth(cached),
        }

    def spec_id(self, cached: bool) -> Optional[str]:
        """Hash of :meth:`canonical`, or ``None`` for a spec that is off."""
        if not self.enabled:
            return None
        payload = json.dumps(self.canonical(cached), sort_keys=True,
                             separators=(",", ":"))
        return SPEC_ID_PREFIX + hashlib.sha256(
            payload.encode("utf-8")).hexdigest()[:16]

    def cache_suffix(self, cached: bool = True) -> str:
        """What this spec appends to a cache key.

        Empty for an off spec and for the legacy spec: legacy is exactly what
        every pre-existing cache entry holds, so it keeps sharing them."""
        if not self.enabled or self.legacy:
            return ""
        return SM_SEP + self.spec_id(cached)[len(SPEC_ID_PREFIX):]

    def to_lock(self, cached: bool) -> dict:
        d = self.canonical(cached)
        if self.enabled:
            d["required"] = bool(self.required)
            if self.legacy:
                d["legacy"] = True
            if self.jobs is not None:
                d["jobs"] = self.jobs
        return d

    def describe(self) -> str:
        """A short human form for status output."""
        if not self.enabled:
            return "off"
        if self.legacy:
            return "legacy"
        parts = ["recursive" if self.recursive else "top-level"]
        if self.paths is not None:
            parts.append("paths:%d" % len(self.paths))
        if self.depth not in (None, "full"):
            parts.append("depth:%s" % self.depth)
        if not self.required:
            parts.append("optional")
        return ",".join(parts)

    # -- scope ------------------------------------------------------------ #

    def selects_top(self, path: str) -> bool:
        if not self.enabled:
            return False
        return self.paths is None or path in self.paths


OFF = SubmoduleSpec(enabled=False, origin="implicit")

#: What the code did before submodules were configurable.  ``required`` is
#: false because the old code tolerated (silently) every failure.
LEGACY = SubmoduleSpec(enabled=True, recursive=True, depth="full",
                       required=False, legacy=True, origin="implicit")


def implicit_spec() -> SubmoduleSpec:
    """The spec for a dependency that does not say."""
    return LEGACY if IMPLICIT_DEFAULT == "legacy" else OFF


def normalize_path(path) -> str:
    """Canonical superproject-relative spelling of a submodule path."""
    if not isinstance(path, str) or not path.strip():
        raise SubmoduleSpecError("a submodule path must be a non-empty string, "
                                 "not %r" % (path,))
    p = path.strip().replace("\\", "/")
    if p.startswith("/"):
        raise SubmoduleSpecError("submodule path %r must be relative to the "
                                 "repository root" % path)
    p = posixpath.normpath(p)
    if p == "." or p == ".." or p.startswith("../"):
        raise SubmoduleSpecError("submodule path %r is outside the "
                                 "repository" % path)
    return p


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_spec(value, origin: str = "explicit") -> SubmoduleSpec:
    """Parse a manifest ``submodules:`` value.

    ``true``/``false`` or a mapping of :data:`SPEC_KEYS`.  Anything else --
    an unknown key, a wrong type -- raises :class:`SubmoduleSpecError`, so a
    typo never silently becomes a default."""
    if isinstance(value, bool):
        return SubmoduleSpec(enabled=value, origin=origin)
    if not isinstance(value, dict):
        raise SubmoduleSpecError(
            "'submodules' must be true, false or a mapping, not %s (%r)"
            % (type(value).__name__, value))

    unknown = sorted(set(value.keys()) - SPEC_KEYS)
    if unknown:
        raise SubmoduleSpecError(
            "unknown 'submodules' key%s: %s (expected: %s)" % (
                "s" if len(unknown) > 1 else "", ", ".join(map(str, unknown)),
                ", ".join(sorted(SPEC_KEYS))))

    recursive = value.get("recursive", True)
    if not isinstance(recursive, bool):
        raise SubmoduleSpecError("'submodules.recursive' must be true or false, "
                                 "not %r" % (recursive,))

    required = value.get("required", True)
    if not isinstance(required, bool):
        raise SubmoduleSpecError("'submodules.required' must be true or false, "
                                 "not %r" % (required,))

    paths = value.get("paths", None)
    if paths is not None:
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            raise SubmoduleSpecError("'submodules.paths' must be a non-empty "
                                     "list of submodule paths, not %r" % (paths,))
        norm = []
        for p in paths:
            n = normalize_path(p)
            if n not in norm:
                norm.append(n)
        paths = tuple(sorted(norm))

    depth = value.get("depth", None)
    if depth is not None:
        if isinstance(depth, str) and depth.strip().lower() == "recommended":
            depth = "recommended"
        elif not _is_int(depth) or depth < 1:
            raise SubmoduleSpecError("'submodules.depth' must be a positive "
                                     "integer or \"recommended\", not %r"
                                     % (depth,))

    jobs = value.get("jobs", None)
    if jobs is not None and (not _is_int(jobs) or jobs < 1):
        raise SubmoduleSpecError("'submodules.jobs' must be a positive integer, "
                                 "not %r" % (jobs,))

    return SubmoduleSpec(enabled=True, recursive=recursive, paths=paths,
                         depth=depth, jobs=jobs, required=required,
                         origin=origin)


def spec_from_lock(record: Optional[dict]) -> SubmoduleSpec:
    """The spec recorded in a lock entry's ``submodules`` object.

    ``None`` -- an entry with no ``submodules`` key, i.e. one written before
    the setting existed -- reproduces what that ivpm did: :data:`LEGACY`."""
    if record is None:
        return dc.replace(LEGACY, origin="lock-legacy")
    spec = (record or {}).get("spec") or {}
    if not spec.get("enabled"):
        return SubmoduleSpec(enabled=False, origin="lock")
    depth = spec.get("depth")
    if depth is None:
        depth = "full"
    paths = spec.get("paths")
    return SubmoduleSpec(
        enabled=True,
        recursive=bool(spec.get("recursive", True)),
        paths=tuple(paths) if paths is not None else None,
        depth=depth,
        jobs=spec.get("jobs"),
        required=bool(spec.get("required", True)),
        legacy=bool(spec.get("legacy", False)),
        origin="lock")


def is_lock_record(value) -> bool:
    """Is *value* a lock entry's ``submodules`` object (rather than a
    manifest value)?  ``ivpm sync`` rebuilds packages by passing lock entries
    through ``process_options``, so both shapes arrive under one key."""
    return isinstance(value, dict) and "spec" in value


# --------------------------------------------------------------------------
# Reading the repository (local only)
# --------------------------------------------------------------------------

def _git(cwd: str, *args, timeout: int = 30) -> Tuple[int, str, str]:
    try:
        r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
    except Exception as e:      # git missing, timeout
        return 1, "", str(e)
    return r.returncode, r.stdout, r.stderr


@dc.dataclass
class GitModule:
    """One ``[submodule "<name>"]`` section of a ``.gitmodules`` file."""
    name: str
    path: str
    url: Optional[str] = None
    update: Optional[str] = None
    shallow: Optional[bool] = None
    branch: Optional[str] = None


def read_gitmodules(repo_dir: str) -> List[GitModule]:
    """The submodules declared in *repo_dir*/.gitmodules, sorted by path.

    Empty when there is no ``.gitmodules``.  Sections without a ``path`` are
    ignored, as git ignores them."""
    gm = os.path.join(repo_dir, ".gitmodules")
    if not os.path.isfile(gm):
        return []
    rc, out, _ = _git(repo_dir, "config", "-f", ".gitmodules", "--null",
                      "--get-regexp", r"^submodule\..*\.(path|url|update|shallow|branch)$")
    if rc not in (0, 1):
        return []
    by_name: Dict[str, dict] = {}
    for item in out.split("\0"):
        if not item:
            continue
        key, _, val = item.partition("\n")
        # submodule.<name>.<var>; <name> may itself contain dots.
        body = key[len("submodule."):]
        name, _, var = body.rpartition(".")
        by_name.setdefault(name, {})[var] = val
    mods = []
    for name, vals in by_name.items():
        if "path" not in vals:
            continue
        try:
            path = normalize_path(vals["path"])
        except SubmoduleSpecError:
            continue
        shallow = vals.get("shallow")
        mods.append(GitModule(
            name=name, path=path, url=vals.get("url"),
            update=vals.get("update"),
            shallow=(shallow.lower() == "true") if shallow is not None else None,
            branch=vals.get("branch")))
    mods.sort(key=lambda m: m.path)
    return mods


def gitlinks(repo_dir: str, paths: List[str], rev: str = "HEAD") -> Dict[str, str]:
    """``{path: commit}`` for the gitlinks at *paths* in *rev*'s tree.

    Read from the commit, not the index, so it works on a read-only tree and
    always reports what the superproject pins."""
    if not paths:
        return {}
    rc, out, _ = _git(repo_dir, "ls-tree", "-z", rev, "--", *paths)
    if rc != 0:
        return {}
    links = {}
    for item in out.split("\0"):
        if not item:
            continue
        meta, _, path = item.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[1] == "commit":
            links[path] = fields[2]
    return links


def _unmerged(repo_dir: str, paths: List[str]) -> set:
    if not paths:
        return set()
    rc, out, _ = _git(repo_dir, "ls-files", "-u", "-z", "--", *paths)
    if rc != 0:
        return set()
    res = set()
    for item in out.split("\0"):
        if item and "\t" in item:
            res.add(item.split("\t", 1)[1])
    return res


def _is_checked_out(sub_dir: str) -> bool:
    return os.path.exists(os.path.join(sub_dir, ".git"))


def _head(sub_dir: str) -> Optional[str]:
    rc, out, _ = _git(sub_dir, "rev-parse", "HEAD")
    return out.strip() if rc == 0 and out.strip() else None


@dc.dataclass
class SubmoduleRecord:
    """One submodule as found on disk.

    *path* is relative to the top of the walk (the superproject); *parent* is
    the path of the repository that declares it (``""`` for top level).

    *state* is ``ok`` (checked out at its gitlink), ``uninitialized``,
    ``moved`` (checked out at another commit), ``conflict`` (the gitlink is
    unmerged) or ``missing`` (declared in ``.gitmodules`` but with no gitlink
    in the tree).
    """
    path: str
    name: str
    parent: str
    url: Optional[str]
    commit: Optional[str]
    state: str
    in_scope: bool
    update: Optional[str] = None
    head: Optional[str] = None
    shallow: Optional[bool] = None

    @property
    def update_none(self) -> bool:
        return (self.update or "").strip().lower() == "none"

    @property
    def needs_work(self) -> bool:
        """In scope but not yet in the state the spec asks for."""
        return self.in_scope and self.state != "ok"

    def lock_status(self) -> str:
        if self.update_none:
            return "update-none"
        if not self.in_scope:
            return "not-requested"
        if self.state == "ok":
            return "ok"
        if self.state in ("moved", "conflict"):
            return self.state
        return "failed"

    def to_lock(self) -> dict:
        d = {
            "path": self.path,
            "name": self.name,
            "url": self.url,
            "commit": self.commit,
            "initialized": self.state in ("ok", "moved", "conflict"),
            "status": self.lock_status(),
        }
        if d["initialized"]:
            d["shallow"] = bool(self.shallow)
        return d


def walk(repo_dir: str, spec: SubmoduleSpec,
         _prefix: str = "", _parent_in_scope: bool = True,
         _top: bool = True) -> List[SubmoduleRecord]:
    """Every submodule declared under *repo_dir*, recursively, in path order.

    Recursion follows checked-out submodules only -- an uninitialized one has
    no tree, so whatever it declares cannot be seen (or needed) yet.  Runs a
    handful of local git commands per repository and never writes, so it is
    safe on a read-only cache entry."""
    mods = read_gitmodules(repo_dir)
    if not mods:
        return []
    links = gitlinks(repo_dir, [m.path for m in mods])
    unmerged = _unmerged(repo_dir, [m.path for m in mods])
    records = []
    for m in mods:
        full = posixpath.join(_prefix, m.path) if _prefix else m.path
        update_none = (m.update or "").strip().lower() == "none"
        if _top:
            in_scope = spec.selects_top(m.path)
        else:
            in_scope = spec.enabled and spec.recursive and _parent_in_scope
        if update_none:
            in_scope = False

        commit = links.get(m.path)
        sub_dir = os.path.join(repo_dir, m.path)
        head = None
        shallow = None
        if m.path in unmerged:
            state = "conflict"
        elif commit is None:
            state = "missing"
        elif not _is_checked_out(sub_dir):
            state = "uninitialized"
        else:
            head = _head(sub_dir)
            state = "ok" if head == commit else "moved"
            rc, out, _ = _git(sub_dir, "rev-parse", "--is-shallow-repository")
            shallow = rc == 0 and out.strip() == "true"

        url = m.url
        if state not in ("uninitialized", "missing"):
            rc, out, _ = _git(repo_dir, "config", "--get",
                              "submodule.%s.url" % m.name)
            if rc == 0 and out.strip():
                url = out.strip()

        if state == "missing":
            # .gitmodules names a path the tree has no gitlink for: a stale
            # entry.  git ignores it, so it is never in scope.
            in_scope = False

        records.append(SubmoduleRecord(
            path=full, name=m.name, parent=_prefix, url=url, commit=commit,
            state=state, in_scope=in_scope, update=m.update, head=head,
            shallow=shallow))

        if state in ("ok", "moved"):
            records.extend(walk(sub_dir, spec, _prefix=full,
                                _parent_in_scope=in_scope, _top=False))
    return records


def module_gitdir(repo_dir: str, rec: "SubmoduleRecord") -> Optional[str]:
    """The submodule's repository under its parent's ``.git/modules``, when
    it exists -- as it still does after ``git submodule deinit``."""
    parent_dir = os.path.join(repo_dir, rec.parent) if rec.parent else repo_dir
    rc, out, _ = _git(parent_dir, "rev-parse", "--git-path",
                      "modules/%s" % rec.name)
    if rc != 0 or not out.strip():
        return None
    gd = out.strip()
    if not os.path.isabs(gd):
        gd = os.path.join(parent_dir, gd)
    return gd if os.path.isdir(gd) else None


def has_gitmodules(repo_dir: str) -> bool:
    return os.path.isfile(os.path.join(repo_dir, ".gitmodules"))


def submodule_dirty(repo_dir: str, rec: SubmoduleRecord) -> List[str]:
    """Porcelain lines of a checked-out submodule's own working tree."""
    sub = os.path.join(repo_dir, rec.path)
    rc, out, _ = _git(sub, "status", "--porcelain", "--ignore-submodules=all")
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def lock_record(spec: SubmoduleSpec, records: List[SubmoduleRecord],
                cached: bool) -> dict:
    """The lock entry's ``submodules`` object (design §6.1)."""
    rec = {"spec": spec.to_lock(cached),
           "modules": [r.to_lock() for r in records]}
    sid = spec.spec_id(cached)
    if sid is not None:
        rec["spec_id"] = sid
    return rec


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

#: ``run(cmd, cwd, progress) -> (rc, stderr)``: runs a git command.
Runner = Callable[[List[str], str, bool], Tuple[int, str]]


def _default_runner(cmd, cwd, progress=False):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except Exception as e:
        return 1, str(e)
    return r.returncode, r.stderr


@dc.dataclass
class FetchResult:
    """What :func:`fetch` did.

    *ok* is True when every in-scope submodule ended up checked out at its
    gitlink.  *problems* are the in-scope records that did not; *stderr* is
    git's output from the failing run; *retried* names the submodules that
    were re-fetched with full history after a shallow fetch was refused."""
    ok: bool
    spec: SubmoduleSpec
    records: List[SubmoduleRecord] = dc.field(default_factory=list)
    problems: List[SubmoduleRecord] = dc.field(default_factory=list)
    stderr: str = ""
    rc: int = 0
    retried: List[str] = dc.field(default_factory=list)
    ran: bool = False

    @property
    def count(self) -> int:
        return len([r for r in self.records if r.in_scope])


def validate_paths(repo_dir: str, spec: SubmoduleSpec, label: str) -> None:
    """Every ``paths`` entry must be a top-level submodule at this commit.

    Raises :class:`SubmoduleSpecError` naming the bad entries and the
    submodules that do exist, so a typo is caught rather than skipped."""
    if not spec.enabled or spec.paths is None:
        return
    declared = [m.path for m in read_gitmodules(repo_dir)]
    if not declared:
        raise SubmoduleSpecError(
            "%s: 'submodules.paths' lists %s, but %s has no submodules" % (
                label, ", ".join(spec.paths), label))
    bad = [p for p in spec.paths if p not in declared]
    if bad:
        nested = [p for p in bad
                  if any(p.startswith(d + "/") for d in declared)]
        msg = "%s: 'submodules.paths' entr%s %s %s not a submodule" % (
            label, "ies" if len(bad) > 1 else "y", ", ".join(bad),
            "are" if len(bad) > 1 else "is")
        msg += " (submodules: %s)" % ", ".join(declared)
        if nested:
            msg += "; selecting a nested submodule is not supported -- " \
                   "list its top-level parent instead"
        raise SubmoduleSpecError(msg)


def _depth_args(depth) -> List[str]:
    if depth is None:
        return []
    if depth == "recommended":
        return ["--recommend-shallow"]
    return ["--depth", str(depth)]


def update_command(spec: SubmoduleSpec, cached: bool, n_modules: int,
                   paths: Optional[List[str]] = None,
                   depth: object = "spec") -> List[str]:
    """The ``git submodule update`` command line for *spec*."""
    cmd = ["git", "submodule", "update", "--init"]
    if spec.recursive:
        cmd.append("--recursive")
    cmd += _depth_args(spec.resolved_depth(cached) if depth == "spec" else depth)
    jobs = spec.jobs if spec.jobs is not None else min(DEFAULT_MAX_JOBS,
                                                       max(1, n_modules))
    if jobs > 1:
        cmd += ["--jobs", str(jobs)]
    if paths:
        cmd += ["--"] + list(paths)
    return cmd


def is_shallow_miss(stderr_text: str) -> bool:
    low = (stderr_text or "").lower()
    return any(n in low for n in _SHALLOW_MISS)


def _retry_full(repo_dir: str, rec: SubmoduleRecord, spec: SubmoduleSpec,
                run: Runner, wrap: Callable[[List[str]], List[str]],
                fresh: bool = True):
    """Re-fetch one submodule with full history.

    A *fresh* submodule (cloned by this very run, so holding nothing of the
    user's) is removed and cloned again: the shallow attempt left a
    repository that a plain update would only re-fetch shallowly.  An
    existing one is deepened in place instead -- its repository may hold
    branches or stashes that must survive."""
    parent_dir = os.path.join(repo_dir, rec.parent) if rec.parent else repo_dir
    rel = posixpath.relpath(rec.path, rec.parent) if rec.parent else rec.path
    if fresh:
        _git(parent_dir, "submodule", "deinit", "-f", "--", rel)
        rc, out, _ = _git(parent_dir, "rev-parse", "--git-path",
                          "modules/%s" % rec.name)
        if rc == 0 and out.strip():
            gd = out.strip()
            if not os.path.isabs(gd):
                gd = os.path.join(parent_dir, gd)
            shutil.rmtree(gd, ignore_errors=True)
    else:
        sub = os.path.join(repo_dir, rec.path)
        if os.path.exists(os.path.join(sub, ".git")):
            where, gd_args = sub, []
        else:
            # Not checked out: an empty directory would resolve to the
            # *parent* repository, so address the module's own git dir.
            gd = module_gitdir(repo_dir, rec)
            if gd is None:
                return 1, "no repository for submodule %s" % rec.path
            where, gd_args = parent_dir, ["--git-dir=%s" % gd]
        rc, out, _ = _git(where, *(gd_args + ["rev-parse",
                                              "--is-shallow-repository"]))
        fetch = ["git"] + gd_args + ["fetch", "origin"]
        if rc == 0 and out.strip() == "true":
            fetch = ["git"] + gd_args + ["fetch", "--unshallow", "origin"]
        frc, ferr = run(wrap(fetch), where, False)
        if frc != 0:
            return frc, ferr
    cmd = ["git", "submodule", "update", "--init"]
    if spec.recursive:
        cmd.append("--recursive")
    cmd += ["--", rel]
    return run(wrap(cmd), parent_dir, True)


def fetch(repo_dir: str, spec: SubmoduleSpec, cached: bool, *,
          label: str = "",
          run: Runner = None,
          wrap: Callable[[List[str]], List[str]] = None,
          paths: Optional[List[str]] = None) -> FetchResult:
    """Initialize the submodules *spec* asks for under *repo_dir*.

    Steps (design §5.1): validate ``paths``; run one ``git submodule update``;
    walk the tree to verify every in-scope submodule is checked out at its
    gitlink (git has been known to exit 0 on a partial failure); and, when a
    shallow fetch was refused for a pinned commit, re-fetch only those
    submodules with full history.

    *paths* restricts the update to those top-level submodules (used to
    initialize only what is missing in an existing tree).  Raises
    :class:`SubmoduleSpecError` for a bad ``paths`` entry; every other
    failure is reported in the result."""
    run = run or _default_runner
    wrap = wrap or (lambda c: c)
    label = label or os.path.basename(repo_dir)

    if not spec.enabled:
        return FetchResult(ok=True, spec=spec, records=walk(repo_dir, spec))

    validate_paths(repo_dir, spec, label)
    mods = read_gitmodules(repo_dir)
    if not mods:
        return FetchResult(ok=True, spec=spec)

    if paths is None:
        targets = list(spec.paths) if spec.paths is not None else None
    else:
        targets = list(paths)
    n = len(targets) if targets is not None else len(mods)

    depth = spec.resolved_depth(cached)
    cmd = update_command(spec, cached, n, targets)
    _logger.debug("submodule update: %s", cmd)
    rc, err = run(wrap(cmd), repo_dir, True)

    records = walk(repo_dir, spec)
    problems = [r for r in records if r.needs_work]
    retried = []

    if problems and depth is not None and is_shallow_miss(err):
        for rec in problems:
            if rec.state not in ("uninitialized", "moved"):
                continue
            _logger.debug("retrying %s with full history", rec.path)
            rrc, rerr = _retry_full(repo_dir, rec, spec, run, wrap)
            retried.append(rec.path)
            if rrc != 0:
                err = (err + "\n" + rerr) if err else rerr
        records = walk(repo_dir, spec)
        problems = [r for r in records if r.needs_work]

    ok = not problems and (rc == 0 or bool(retried))
    return FetchResult(ok=ok, spec=spec, records=records, problems=problems,
                       stderr=err if not ok or retried else "", rc=rc,
                       retried=retried, ran=True)


def missing(records: List[SubmoduleRecord]) -> List[SubmoduleRecord]:
    """In-scope submodules that are not initialized and can be: the parent is
    the superproject, or a submodule checked out at its gitlink.

    A submodule checked out at another commit (``moved``) or with an unmerged
    gitlink is the user's work in progress; it is never in this list, and
    nothing below a moved submodule is either."""
    ok_paths = set(r.path for r in records if r.state == "ok")
    return [r for r in records
            if r.in_scope and r.state == "uninitialized"
            and (r.parent == "" or r.parent in ok_paths)]


def init_missing(repo_dir: str, spec: SubmoduleSpec, cached: bool, *,
                 records: Optional[List[SubmoduleRecord]] = None,
                 targets: Optional[List[SubmoduleRecord]] = None,
                 label: str = "",
                 run: Runner = None,
                 wrap: Callable[[List[str]], List[str]] = None) -> FetchResult:
    """Initialize the in-scope submodules of an existing tree that are not
    initialized yet (or exactly *targets*), touching nothing else.

    A bare ``git submodule update`` would also move every initialized
    submodule back to its gitlink, discarding where the user had put it, so
    each submodule is named explicitly, in the repository that declares it.
    *targets* may include initialized submodules that should move to their
    gitlink (``ivpm sync`` after a merge changed it)."""
    run = run or _default_runner
    wrap = wrap or (lambda c: c)
    if records is None:
        records = walk(repo_dir, spec)
    todo = list(targets) if targets is not None else missing(records)
    if not todo:
        return FetchResult(ok=True, spec=spec, records=records)
    # Fresh = nothing of the user's can exist yet: not checked out, *and* no
    # repository under .git/modules. A deinitialized submodule keeps its
    # repository there, possibly with branches and stashes, so it is not
    # fresh, and a retry must deepen it rather than delete it.
    fresh = set(r.path for r in todo
                if r.state == "uninitialized"
                and module_gitdir(repo_dir, r) is None)
    todo_paths = [r.path for r in todo]

    def _under_todo(path):
        return any(path == p or path.startswith(p + "/") for p in todo_paths)

    by_parent: Dict[str, List[SubmoduleRecord]] = {}
    for r in todo:
        by_parent.setdefault(r.parent, []).append(r)

    depth = spec.resolved_depth(cached)
    err_all = []
    rc_all = 0
    for parent, recs in sorted(by_parent.items()):
        pdir = os.path.join(repo_dir, parent) if parent else repo_dir
        rels = [posixpath.relpath(r.path, parent) if parent else r.path
                for r in recs]
        cmd = update_command(spec, cached, len(rels), rels)
        rc, err = run(wrap(cmd), pdir, True)
        if rc != 0:
            rc_all = rc
            err_all.append(err)
    err = "\n".join(e for e in err_all if e)

    records = walk(repo_dir, spec)
    still = [r for r in records if r.needs_work and _under_todo(r.path)]
    retried = []
    if still and depth is not None and is_shallow_miss(err):
        for rec in still:
            if rec.state not in ("uninitialized", "moved"):
                continue
            rrc, rerr = _retry_full(repo_dir, rec, spec, run, wrap,
                                    fresh=rec.path in fresh)
            retried.append(rec.path)
            if rrc != 0:
                err = (err + "\n" + rerr) if err else rerr
        records = walk(repo_dir, spec)
        still = [r for r in records if r.needs_work and _under_todo(r.path)]

    ok = not still and (rc_all == 0 or bool(retried))
    return FetchResult(ok=ok, spec=spec, records=records, problems=still,
                       stderr=err if not ok or retried else "", rc=rc_all,
                       retried=retried, ran=True)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _host(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    from .utils import url_host
    h = url_host(url)
    if h:
        return h
    # git@host:path
    if "@" in url and ":" in url.split("@", 1)[1]:
        return url.split("@", 1)[1].split(":", 1)[0]
    return None


def _state_phrase(rec: SubmoduleRecord) -> str:
    if rec.state == "uninitialized":
        return "not initialized"
    if rec.state == "moved":
        return "checked out at %s, superproject pins %s" % (
            (rec.head or "?")[:7], (rec.commit or "?")[:7])
    if rec.state == "conflict":
        return "unmerged gitlink"
    return rec.state


def failure_message(label: str, commit: Optional[str], result: FetchResult,
                    superproject_url: Optional[str] = None) -> str:
    """A multi-line message in the shape of a failed-clone message: what
    failed, git's own words, then hints (design §5.2)."""
    from .git_diagnose import classify_git_failure, diagnose_git_failure
    head = "Failed to initialize submodules of %s" % label
    if commit:
        head += " (%s)" % commit[:7]
    parts = [head + ":"]
    for rec in result.problems:
        parts.append("  %s -> %s (%s)" % (rec.path, rec.url or "?",
                                          _state_phrase(rec)))
    if not result.problems and result.rc != 0:
        parts.append("  git submodule update exited %d" % result.rc)

    tail = [ln for ln in (result.stderr or "").splitlines() if ln.strip()]
    if tail:
        parts.append("git reported:")
        parts.extend("  " + ln for ln in tail[-8:])

    err = result.stderr or ""
    low = err.lower()
    first_url = next((r.url for r in result.problems if r.url), None)
    hints = []
    if "transport 'file' not allowed" in low:
        hints.append("git refuses file:// submodule URLs by default; allow them "
                     "with 'git config --global protocol.file.allow always' if "
                     "you trust the repository")
    kind = classify_git_failure(err)
    if kind in ("auth", "no_credential", "sso", "scope"):
        sp_host = _host(superproject_url)
        bad_hosts = sorted(set(h for h in (_host(r.url) for r in result.problems)
                               if h and h != sp_host))
        if bad_hosts and sp_host:
            hints.append(
                "submodule host %s differs from %s's host %s; credentials ivpm "
                "supplies for %s do not apply there. Configure a credential "
                "helper for %s, or rewrite the URL with url.<base>.insteadOf"
                % (", ".join(bad_hosts), label, sp_host, sp_host,
                   ", ".join(bad_hosts)))
    if first_url:
        for h in diagnose_git_failure(first_url, err):
            if h not in hints:
                hints.append(h)
    if any(r.state == "moved" for r in result.problems) and not result.retried:
        hints.append("a submodule left at another commit usually means its "
                     "server would not serve the pinned commit; try "
                     "'depth' unset (full history) for this dependency")
    parts.extend("hint: " + h for h in hints)
    return "\n".join(parts)


def summary(records: List[SubmoduleRecord]) -> str:
    """``3/5 (2 uninitialized: ext/a, ext/b)``-style summary of in-scope
    submodules, for status output."""
    scope = [r for r in records if r.in_scope]
    if not scope:
        n = len([r for r in records if not r.parent])
        return "%d not requested" % n if n else ""
    ok = [r for r in scope if r.state == "ok"]
    s = "%d/%d" % (len(ok), len(scope))
    bad: Dict[str, List[str]] = {}
    for r in scope:
        if r.state != "ok":
            bad.setdefault(r.state, []).append(r.path)
    if bad:
        s += " (" + "; ".join("%d %s: %s" % (len(v), k, ", ".join(v[:3]) +
                                             (", ..." if len(v) > 3 else ""))
                              for k, v in sorted(bad.items())) + ")"
    return s
