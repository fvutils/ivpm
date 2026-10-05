#****************************************************************************
#* cache_adopt.py
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
"""Adopting the winner of a lost cache-publish race.

A publisher that loses the race (its ``rename`` fails ``ENOTEMPTY``) knows from
the server that a complete, sealed entry exists.  What it does not know is
whether *this host* can see it yet: on NFS the client may still hold the
"doesn't exist" answer from the cache-miss lookup moments earlier, for up to
``acdirmax`` seconds.  This module answers two questions:

* **How long to wait** (:func:`resolve_adopt_budget`) -- explicit
  configuration first, then derived from the cache mount's attribute-cache
  options, then a fixed fallback.
* **Whether the winner is usable yet** (:func:`await_winner`) -- refresh this
  host's view of the package directory and probe the entry, on a backing-off
  schedule, reporting progress, until it is usable or the budget runs out.

What to do when it never becomes usable (publish this run's copy under a
divergent name) is the store's job; see ``DirectoryCacheStore.store_version``.
See cache-lost-race-design.md, Part A.
"""
import dataclasses as dc
import errno
import json
import os
import threading
import time
from typing import Callable, List, Optional

from .msg import note

#: Divergent-entry marker: ``<key>~alt~<uuid32>``.  ``~`` is outside the
#: version-key safe set, so ``safe_version_key`` escapes it and no real key
#: can ever contain this marker.
ALT_MARKER = "~alt~"

#: The record written inside a divergent entry explaining why it exists.
DIVERGENCE_RECORD = ".ivpm-cache-divergence.json"
DIVERGENCE_SCHEMA = 1

# --- outcomes -------------------------------------------------------------
ADOPTED = "adopted"
ADOPTED_AFTER_WAIT = "adopted_after_wait"
DIVERGENT_NOT_VISIBLE = "divergent_not_visible"
DIVERGENT_UNREADABLE = "divergent_unreadable"
DIVERGENT_MISMATCH = "divergent_mismatch"

DIVERGENT_REASONS = (DIVERGENT_NOT_VISIBLE, DIVERGENT_UNREADABLE,
                     DIVERGENT_MISMATCH)

# --- budget ---------------------------------------------------------------

#: Linux NFS client defaults, which ``/proc/self/mountinfo`` does not list.
NFS_DEFAULT_ACDIRMAX = 60
#: Added to ``acdirmax``: covers server latency and the gap between the
#: client's last lookup and the moment the wait starts.
BUDGET_MARGIN_S = 10
#: Mounts that cache no negative lookups (``lookupcache=none|positive``,
#: ``noac``) and local filesystems: only server latency to cover.
SHORT_BUDGET_S = 5
#: When the mount cannot be determined, or is a shared filesystem whose
#: caching IVPM does not model (Lustre, GPFS, ...).
FALLBACK_BUDGET_S = 60

_NFS_TYPES = ("nfs", "nfs4")
_LOCAL_TYPES = frozenset((
    "ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "zfs", "f2fs", "jfs",
    "reiserfs", "overlay", "ramfs", "apfs", "hfs", "vfat", "exfat", "ntfs",
))

_MOUNTINFO = "/proc/self/mountinfo"


@dc.dataclass(frozen=True)
class Mount:
    mount_point: str
    fstype: str
    source: str
    options: str              # mount options + super options, comma-joined


@dc.dataclass(frozen=True)
class AdoptBudget:
    seconds: float
    source: str               # config / mount:acdirmax=N / mount:default / fallback
    mount: Optional[Mount] = None

    def to_json(self) -> dict:
        out = {"seconds": self.seconds, "source": self.source}
        if self.mount is not None:
            out["mount"] = dc.asdict(self.mount)
        return out


def _unescape(field: str) -> str:
    """mountinfo octal-escapes space, tab, newline and backslash."""
    out, i = [], 0
    while i < len(field):
        c = field[i]
        if c == "\\" and i + 4 <= len(field) and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_mountinfo(text: str) -> List[Mount]:
    """Parse ``/proc/self/mountinfo`` content; malformed lines are skipped."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        try:
            sep = parts.index("-")
        except ValueError:
            continue
        if sep < 6 or len(parts) < sep + 3:
            continue
        mount_point = _unescape(parts[4])
        opts = parts[5]
        fstype = parts[sep + 1]
        source = _unescape(parts[sep + 2])
        super_opts = parts[sep + 3] if len(parts) > sep + 3 else ""
        mounts.append(Mount(mount_point, fstype, source,
                            ",".join(o for o in (opts, super_opts) if o)))
    return mounts


def find_mount(path: str, mounts: List[Mount]) -> Optional[Mount]:
    """The mount holding *path* (longest mount-point prefix; last one wins on
    a tie, since a later mount over the same point shadows an earlier one)."""
    best = None
    for m in mounts:
        mp = m.mount_point
        if mp == "/" or path == mp or path.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) >= len(best.mount_point):
                best = m
    return best


def _options(opts: str) -> dict:
    out = {}
    for o in opts.split(","):
        if not o:
            continue
        k, _, v = o.partition("=")
        out[k] = v
    return out


def budget_for_mount(mount: Optional[Mount]) -> AdoptBudget:
    """The wait budget implied by *mount*'s attribute-cache options."""
    if mount is None:
        return AdoptBudget(FALLBACK_BUDGET_S, "fallback")
    if mount.fstype in _NFS_TYPES:
        opts = _options(mount.options)
        if opts.get("lookupcache") in ("none", "pos", "positive"):
            return AdoptBudget(SHORT_BUDGET_S,
                               "mount:lookupcache=%s" % opts["lookupcache"],
                               mount)
        if "noac" in opts:
            return AdoptBudget(SHORT_BUDGET_S, "mount:noac", mount)
        for key in ("acdirmax", "actimeo"):
            try:
                n = int(opts[key])
            except (KeyError, ValueError):
                continue
            return AdoptBudget(n + BUDGET_MARGIN_S,
                               "mount:%s=%d" % (key, n), mount)
        # /proc lists only non-default ac* options: absent means the default.
        return AdoptBudget(NFS_DEFAULT_ACDIRMAX + BUDGET_MARGIN_S,
                           "mount:default", mount)
    if mount.fstype in _LOCAL_TYPES:
        return AdoptBudget(SHORT_BUDGET_S, "mount:local", mount)
    return AdoptBudget(FALLBACK_BUDGET_S, "fallback:%s" % mount.fstype, mount)


_budget_cache = {}
_budget_lock = threading.Lock()


def resolve_adopt_budget(cache_dir: str,
                         mountinfo_path: str = _MOUNTINFO) -> AdoptBudget:
    """How long a lost race waits for the winner, for a cache at *cache_dir*.

    Explicit configuration is re-read every call (cheap, and tests change it);
    the mount-derived value is cached per cache directory.
    """
    from .site_config import resolve_cache_adopt_wait
    seconds, source = resolve_cache_adopt_wait()
    if seconds is not None:
        return AdoptBudget(seconds, source or "config")

    key = (os.path.realpath(cache_dir), mountinfo_path)
    with _budget_lock:
        cached = _budget_cache.get(key)
    if cached is not None:
        return cached
    try:
        with open(mountinfo_path) as fp:
            mounts = parse_mountinfo(fp.read())
    except OSError:
        mounts = None
    budget = budget_for_mount(find_mount(key[0], mounts) if mounts else None)
    with _budget_lock:
        _budget_cache[key] = budget
    return budget


# --- probing --------------------------------------------------------------

USABLE = "usable"


class AdoptMonitor:
    """Hooks a caller can supply to observe a lost-race wait.

    All methods are optional overrides; the base class does nothing.  They are
    called on the publishing thread.
    """

    def started(self, budget: AdoptBudget) -> None:
        pass

    def progress(self, elapsed: float, budget: float, detail: str) -> None:
        pass

    def finished(self, result: 'AdoptResult') -> None:
        pass


@dc.dataclass
class AdoptResult:
    outcome: str
    waited_s: float
    budget: AdoptBudget
    observations: List[dict]

    @property
    def adopted(self) -> bool:
        return self.outcome in (ADOPTED, ADOPTED_AFTER_WAIT)

    @property
    def last_probe(self) -> Optional[str]:
        return self.observations[-1]["result"] if self.observations else None


def _errname(e: OSError) -> str:
    return errno.errorcode.get(e.errno, str(e.errno))


def probe_entry(version_dir: str, package_name: str, version_key: str,
                manifest_name: str, legacy_ok: bool) -> str:
    """Can this host use the entry at *version_dir* right now?

    Returns :data:`USABLE`, or a short reason it is not.  The manifest is
    opened by its full path, never via ``isdir``, so every component of the
    path is looked up afresh.
    """
    try:
        import stat as _stat
        st = os.lstat(version_dir)
        if not _stat.S_ISDIR(st.st_mode):
            return "not-a-directory"
    except OSError as e:
        return _errname(e)
    try:
        with open(os.path.join(version_dir, manifest_name)) as fp:
            manifest = json.load(fp)
    except OSError as e:
        if e.errno == errno.ENOENT:
            # A winner published by an IVPM that predates manifests.
            try:
                if legacy_ok and os.listdir(version_dir):
                    return USABLE
            except OSError as e2:
                return _errname(e2)
            return "no-manifest"
        return _errname(e)
    except ValueError:
        return "manifest-unparseable"
    if not isinstance(manifest, dict):
        return "manifest-unparseable"
    if manifest.get("package") not in (None, package_name) or \
            manifest.get("version") not in (None, version_key):
        return "manifest-mismatch"
    return USABLE


#: Probe results that waiting cannot change.
_TERMINAL = {
    "EACCES": DIVERGENT_UNREADABLE,
    "EPERM": DIVERGENT_UNREADABLE,
    "not-a-directory": DIVERGENT_MISMATCH,
    "manifest-mismatch": DIVERGENT_MISMATCH,
}

#: Probe times, in seconds from the start of the wait; then every
#: :data:`_POLL_TAIL_S` until the deadline.
_POLL_HEAD = (0.0, 0.25, 0.5, 1.0, 2.0)
_POLL_TAIL_S = 2.0
_PROGRESS_EVERY_S = 1.0
_NOTE_EVERY_S = 10.0
_MAX_OBSERVATIONS = 400


def poll_times(budget: float):
    """Probe times for a wait of *budget* seconds; always ends at the deadline."""
    for t in _POLL_HEAD:
        if t >= budget:
            break
        yield t
    t = _POLL_HEAD[-1] + _POLL_TAIL_S
    while t < budget:
        yield t
        t += _POLL_TAIL_S
    yield budget


def refresh_view(pkg_dir: str) -> str:
    """Nudge this host's NFS client to revalidate *pkg_dir*.

    Reading a directory makes the client recheck its attributes; when its
    change time moved, the client drops cached negative lookups under it.
    Best effort -- the probe that follows is what decides.
    """
    try:
        os.listdir(pkg_dir)
        return "listdir"
    except OSError as e:
        return "listdir:%s" % _errname(e)


def await_winner(version_dir: str, package_name: str, version_key: str,
                 budget: AdoptBudget, *, manifest_name: str,
                 legacy_ok: bool = True,
                 monitor: Optional[AdoptMonitor] = None,
                 clock: Optional[Callable[[], float]] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 probe: Optional[Callable[[], str]] = None) -> AdoptResult:
    """Wait (up to *budget*) until this host can use the winner's entry.

    Never raises for a probe failure: every outcome is a named result, and the
    caller decides what a non-adoption means.  ``KeyboardInterrupt`` from
    *sleep* propagates so Ctrl-C stays responsive.
    """
    monitor = monitor or AdoptMonitor()
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    pkg_dir = os.path.dirname(version_dir)
    if probe is None:
        def probe():
            return probe_entry(version_dir, package_name, version_key,
                               manifest_name, legacy_ok)

    observations: List[dict] = []
    start = clock()
    started = False
    last_progress = None
    last_note = 0.0
    result = None

    for t in poll_times(budget.seconds):
        # Sleep until the next probe time, reporting progress at most once a
        # second while we do.
        while True:
            now = clock() - start
            remaining = t - now
            if remaining <= 0:
                break
            if last_progress is None or now - last_progress >= _PROGRESS_EVERY_S:
                last_progress = now
                monitor.progress(now, budget.seconds, "waiting")
                if now - last_note >= _NOTE_EVERY_S:
                    last_note = now
                    note("%s: still waiting for the cache entry another process "
                         "published to become visible on this host (%ds / %ds)"
                         % (package_name, now, budget.seconds))
            sleep(min(remaining, _PROGRESS_EVERY_S))

        refreshed = refresh_view(pkg_dir)
        got = probe()
        elapsed = clock() - start
        if len(observations) < _MAX_OBSERVATIONS:
            observations.append({"t": round(elapsed, 3), "refresh": refreshed,
                                 "probe": "manifest", "result": got})
        if got == USABLE:
            result = ADOPTED if not observations[:-1] else ADOPTED_AFTER_WAIT
            break
        if got in _TERMINAL:
            result = _TERMINAL[got]
            break
        if not started:
            started = True
            monitor.started(budget)
            if budget.seconds > 0:
                note("%s: another process published this cache entry first; "
                     "waiting up to %ds for it to become visible on this host "
                     "(%s)" % (package_name, budget.seconds, budget.source))

    if result is None:
        result = DIVERGENT_NOT_VISIBLE
    out = AdoptResult(result, clock() - start, budget, observations)
    monitor.finished(out)
    return out


def describe_outcome(result: AdoptResult) -> str:
    """One line: why the winner was not used."""
    if result.outcome == DIVERGENT_UNREADABLE:
        return ("this user cannot read the entry another process published "
                "(%s); that is a cache group/permission problem, not a timing "
                "one" % result.last_probe)
    if result.outcome == DIVERGENT_MISMATCH:
        return ("the entry another process published is not what this run "
                "expected (%s)" % result.last_probe)
    mount = result.budget.mount
    why = ("NFS attribute cache; " if mount is not None
           and mount.fstype in _NFS_TYPES else "")
    return ("this host could not see the entry another process published after "
            "waiting %ds (%sbudget from %s)"
            % (round(result.waited_s), why, result.budget.source))
