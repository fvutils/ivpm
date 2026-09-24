#!/usr/bin/env python3
"""Characterize an NFS mount against the assumptions IVPM's cache store makes.

The store is deliberately lock-free.  Mutual exclusion between concurrent
publishers is provided entirely by the semantics of ``os.rename``, and access
protection is provided entirely by setgid inheritance and exact ``chmod``.
Every one of those behaviors has been verified on local ext4 and tmpfs and on
nothing else.  This script checks them where it matters.

Standalone by design: stdlib only, no IVPM import, no third-party packages, so
it runs on a bare production host.  It creates one scratch directory under the
path you give it and touches nothing else.

    # 1. On one client, against the real cache mount:
    python3 nfs_characterize.py --cache-dir /nfs/ivpm-cache --group server2 \\
        --out solo-$(hostname).json

    # 2. On two clients at once, same shared path (order does not matter):
    python3 nfs_characterize.py --cache-dir /nfs/ivpm-cache --group server2 \\
        --role writer --run-id run1 --out writer.json     # host A
    python3 nfs_characterize.py --cache-dir /nfs/ivpm-cache --group server2 \\
        --role reader --run-id run1 --out reader.json     # host B

    # 3. Cross-export rename, if the workspace and cache are separate mounts:
    python3 nfs_characterize.py --cache-dir /nfs/ivpm-cache \\
        --alt-dir /local/workspaces --out xdev.json

Send the JSON files back for review.  The human-readable summary printed at the
end is a convenience; the JSON is what carries the detail.
"""
from __future__ import print_function

import argparse
import errno
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time

SCRATCH_NAME = ".ivpm-nfs-probe"

OK = "ok"
DEVIATION = "deviation"      # behaves differently than the store assumes
SKIPPED = "skipped"
ERROR = "error"
INFO = "info"                # recorded, not asserted


class Probes(object):
    def __init__(self):
        self.results = []

    def record(self, pid, name, status, expect=None, got=None, detail=None,
               impact=None, **extra):
        row = {
            "id": pid, "name": name, "status": status,
            "expect": expect, "got": got, "detail": detail, "impact": impact,
        }
        row.update(extra)
        self.results.append(row)
        return row

    def check(self, pid, name, expect, got, impact, detail=None):
        status = OK if got == expect else DEVIATION
        return self.record(pid, name, status, expect=expect, got=got,
                           detail=detail, impact=impact)

    def errno_of(self, fn):
        """Run *fn*; return 'OK' or the errno name it raised."""
        try:
            fn()
            return "OK"
        except OSError as e:
            return errno.errorcode.get(e.errno, "errno %s" % e.errno)


# --------------------------------------------------------------------------
# Group 0 -- environment.  Recorded, never asserted: these are the facts that
# explain every deviation below, and the first thing anyone reviewing the
# results will ask for.
# --------------------------------------------------------------------------

def probe_environment(P, cache_dir):
    info = {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "python": sys.version.split()[0],
        "uid": os.getuid(),
        "gid": os.getgid(),
        "groups": sorted(os.getgroups()),
        "umask": "0o%03o" % _read_umask(),
        "cache_dir": cache_dir,
        "cache_dir_realpath": os.path.realpath(cache_dir),
    }
    info["mount"] = _mount_for(cache_dir)
    info["nfsstat"] = _run(["nfsstat", "-m"])
    info["statfs"] = _statfs(cache_dir)
    info["setfacl_available"] = _which("setfacl") is not None
    info["idmap_domain"] = _read_file("/etc/idmapd.conf")
    P.record("E0", "environment", INFO, got=info,
             impact="Explains every other result; not a pass/fail.")
    return info


def _read_umask():
    cur = os.umask(0o022)
    os.umask(cur)
    return cur


def _mount_for(path):
    """The mount entry governing *path*, with its options."""
    try:
        with open("/proc/mounts") as fp:
            entries = [l.split() for l in fp if l.strip()]
    except IOError:
        return None
    real = os.path.realpath(path)
    best = None
    for e in entries:
        if len(e) < 4:
            continue
        mp = e[1].replace("\\040", " ")
        if real == mp or real.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) > len(best[1]):
                best = e
    if best is None:
        return None
    return {"source": best[0], "mountpoint": best[1],
            "fstype": best[2], "options": best[3]}


def _statfs(path):
    try:
        st = os.statvfs(path)
        return {"f_bsize": st.f_bsize, "f_namemax": st.f_namemax}
    except OSError:
        return None


def _run(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return out.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return None


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _read_file(path):
    try:
        with open(path) as fp:
            return fp.read()[:4000]
    except IOError:
        return None


# --------------------------------------------------------------------------
# Group 1 -- ownership and protection.
#
# Failures here are the SILENT WRONG ACCESS class: content is published,
# everything looks fine, and the wrong people can read it.  This is the exact
# failure the protection work exists to prevent, so a deviation in this group
# means the fix does not hold on this mount.
# --------------------------------------------------------------------------

def probe_protection(P, root, gid, group_name):
    if gid is None:
        P.record("P1", "ownership and protection", SKIPPED,
                 detail="no --group given and no secondary group available",
                 impact="The protection model is unverified on this mount.")
        return

    d = os.path.join(root, "prot")
    os.mkdir(d)

    # P1.1 -- can we set the group at all?  Export squashing (root_squash,
    # all_squash, anonuid/anongid) and NFSv4 idmapping can all silently
    # substitute a different owner.
    try:
        os.chown(d, -1, gid)
        got_gid = os.stat(d).st_gid
    except OSError as e:
        P.record("P1.1", "chgrp a directory", ERROR, got=str(e),
                 impact="FATAL: the store fails closed; no package under a "
                        "policy can be cached on this mount.")
        return
    P.check("P1.1", "chgrp a directory: gid reads back", gid, got_gid,
            impact="If the gid differs, the export is squashing or idmapping "
                   "ownership. Entries would be published to the wrong group "
                   "and the store's verify step would fail every publish.",
            detail={"requested": gid, "group_name": group_name})

    # P1.2 -- setgid must STICK.  Some servers drop it; on others a later
    # chown clears it.
    os.chmod(d, 0o2770)
    mode = os.stat(d).st_mode
    P.check("P1.2", "setgid bit persists on a directory",
            True, bool(mode & stat.S_ISGID),
            impact="Without setgid the store falls back to an explicit chgrp "
                   "of every node during the seal walk -- correct, but O(files) "
                   "SETATTR round trips. See P6.1 for what that costs here.")

    # P1.3 -- THE mechanism the whole design rests on.  If content created
    # inside a setgid directory does not inherit its group, protection is not
    # free and is not correct-by-construction.
    child_f = os.path.join(d, "child.txt")
    with open(child_f, "w") as fp:
        fp.write("x")
    child_d = os.path.join(d, "childdir")
    os.mkdir(child_d)
    P.check("P1.3a", "new FILE inherits the directory's group",
            gid, os.stat(child_f).st_gid,
            impact="CRITICAL: this is the mechanism that makes a fetch land "
                   "with the right group. If it fails, every published entry "
                   "carries the wrong group unless the seal walk corrects it.")
    P.check("P1.3b", "new DIRECTORY inherits the group",
            gid, os.stat(child_d).st_gid, impact="Same as P1.3a.")
    P.check("P1.3c", "new directory inherits setgid",
            True, bool(os.stat(child_d).st_mode & stat.S_ISGID),
            impact="Inheritance must be transitive or only the top level of a "
                   "fetched tree is protected.")

    # P1.4 -- exact chmod.  NFSv4 ACLs can mangle a mode on the way through,
    # which would make the store's pre-publish verification fail (fail-closed,
    # so safe -- but nothing would ever publish).
    for want in (0o2770, 0o0640, 0o0400, 0o0550):
        target = child_d if want & 0o7000 else child_f
        try:
            os.chmod(target, want)
            got = stat.S_IMODE(os.stat(target).st_mode)
        except OSError as e:
            P.record("P1.4-%o" % want, "chmod 0o%o exact" % want, ERROR,
                     got=str(e), impact="Publishing would fail closed.")
            continue
        P.check("P1.4-%o" % want, "chmod 0o%o reads back exactly" % want,
                "0o%o" % want, "0o%o" % got,
                impact="A mode the server rewrites makes the store's "
                       "pre-publish verify fail, so nothing publishes. Safe "
                       "but total.")

    # P1.5 -- POSIX draft ACLs.  NFSv3 has no notion of them; NFSv4 uses a
    # different model entirely.  If they silently no-op, a site relying on
    # default_acl gets LESS protection than it asked for and no error.
    if not _which("setfacl"):
        P.record("P1.5", "POSIX default ACL inheritance", SKIPPED,
                 detail="setfacl not installed",
                 impact="Cannot tell whether default_acl policies work here.")
    else:
        acl_d = os.path.join(root, "acl")
        os.mkdir(acl_d)
        spec = "g:%s:r-x" % (group_name or gid)
        rc = subprocess.call(["setfacl", "-d", "-m", spec, acl_d],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if rc != 0:
            P.record("P1.5", "POSIX default ACL can be set", DEVIATION,
                     expect="setfacl succeeds", got="exit %d" % rc,
                     impact="default_acl in a policy would fail this mount. "
                            "The store raises rather than publishing without "
                            "it, so it fails closed -- but loudly.")
        else:
            inner = os.path.join(acl_d, "inherited.txt")
            with open(inner, "w") as fp:
                fp.write("x")
            got = _run(["getfacl", "-c", inner]) or ""
            inherited = ("group:%s" % (group_name or gid)) in got
            P.check("P1.5", "default ACL is inherited by new files",
                    True, inherited, detail={"getfacl": got},
                    impact="CRITICAL if a policy uses default_acl: silent "
                           "non-inheritance means named-group grants simply "
                           "do not exist on published content.")

    # P1.6 -- does copystat carry the ACL?  The cross-filesystem publish path
    # relies on it to reproduce per-file ACLs.
    src = os.path.join(root, "xattr-src.txt")
    dst = os.path.join(root, "xattr-dst.txt")
    with open(src, "w") as fp:
        fp.write("x")
    try:
        names = os.listxattr(src) if hasattr(os, "listxattr") else []
    except OSError as e:
        names = "error: %s" % e
    shutil.copyfile(src, dst)
    try:
        shutil.copystat(src, dst)
        copied = os.listxattr(dst) if hasattr(os, "listxattr") else []
    except OSError as e:
        copied = "error: %s" % e
    P.record("P1.6", "xattrs visible / copyable", INFO,
             got={"src_xattrs": list(names) if isinstance(names, list) else names,
                  "dst_xattrs": list(copied) if isinstance(copied, list) else copied},
             impact="Cross-filesystem publish uses copystat to carry POSIX "
                    "ACLs. Empty here means per-file ACLs do not survive that "
                    "path on this mount.")


# --------------------------------------------------------------------------
# Group 2 -- rename semantics.
#
# Failures here are the CORRUPTION class.  The publish rename IS the store's
# mutual-exclusion primitive: its failure on a populated target is what
# serializes concurrent publishers. If these do not hold, two builders can both
# believe they won.
# --------------------------------------------------------------------------

def probe_rename(P, root):
    d = os.path.join(root, "rename")
    os.mkdir(d)

    def mkdir_with(name, content=True, mode=None):
        p = os.path.join(d, name)
        os.mkdir(p)
        if content:
            with open(os.path.join(p, "f"), "w") as fp:
                fp.write("x")
        if mode is not None:
            os.chmod(p, mode)
        return p

    # P2.1 -- THE serialization point.
    a = mkdir_with("a")
    full = mkdir_with("full")
    got = P.errno_of(lambda: os.rename(a, full))
    P.check("P2.1", "rename onto a NON-EMPTY directory fails",
            "ENOTEMPTY", got,
            impact="CRITICAL: this failure is what makes concurrent publishes "
                   "mutually exclusive without a lock. 'OK' here means two "
                   "builders can both publish and one silently wins; EEXIST is "
                   "equally acceptable and already handled.")

    # P2.2 -- crash-leftover rebuild depends on this SUCCEEDING.
    b = mkdir_with("b")
    empty = mkdir_with("empty", content=False)
    got = P.errno_of(lambda: os.rename(b, empty))
    P.check("P2.2", "rename onto an EMPTY directory succeeds", "OK", got,
            impact="An empty leftover version dir must be replaceable, or a "
                   "crashed run wedges that entry permanently.")

    # P2.3 -- a hand-placed symlink at an entry path must not be silently
    # replaced or adopted.
    c = mkdir_with("c")
    link = os.path.join(d, "link")
    os.symlink("/tmp", link)
    got = P.errno_of(lambda: os.rename(c, link))
    P.check("P2.3", "rename onto a SYMLINK fails", "ENOTDIR", got,
            impact="Handled as a lost race by the store; a different errno "
                   "would be reported as a hard store failure instead.")

    # P2.4 -- the sibling property.  The publish rename happens AFTER the seal
    # has removed every write bit, so it must work on an unwritable directory.
    s1 = mkdir_with("sealed1", mode=0o555)
    got = P.errno_of(lambda: os.rename(s1, os.path.join(d, "sealed1-moved")))
    P.check("P2.4", "rename a SEALED dir within its parent succeeds", "OK", got,
            impact="CRITICAL: the publish rename runs on a sealed tree. A "
                   "failure here means nothing can ever be published.")

    # P2.5 -- informational: the constraint that forced publish staging to be a
    # sibling rather than a private subdirectory.
    os.mkdir(os.path.join(d, "otherparent"))
    s2 = mkdir_with("sealed2", mode=0o555)
    got = P.errno_of(
        lambda: os.rename(s2, os.path.join(d, "otherparent", "x")))
    P.record("P2.5", "rename a SEALED dir to a DIFFERENT parent", INFO,
             expect="EACCES on POSIX", got=got,
             impact="Explains why publish staging is a sibling. If this is OK "
                    "here, the private-parent publish would have worked -- but "
                    "the sibling form is correct either way.")

    # P2.7 -- GC's crash-leftover cleanup uses rmdir precisely because it
    # refuses to remove a directory a builder has just populated.
    nonempty = mkdir_with("rmdirtest")
    got = P.errno_of(lambda: os.rmdir(nonempty))
    P.check("P2.7", "rmdir on a NON-EMPTY directory fails", "ENOTEMPTY", got,
            impact="GC uses rmdir, not rmtree, so that losing this race "
                   "destroys nothing. 'OK' would mean GC can delete a "
                   "freshly published entry.")

    # P2.8 -- mkdir must be atomic across clients; it is how partition
    # directories elect a single creator.
    mk = os.path.join(d, "mkonce")
    os.mkdir(mk)
    got = P.errno_of(lambda: os.mkdir(mk))
    P.check("P2.8", "mkdir of an existing directory fails", "EEXIST", got,
            impact="Partition creation elects one promoter via EEXIST. Any "
                   "other result breaks that election.")


def probe_cross_device(P, cache_dir, alt_dir):
    """P2.6 -- does a cross-mount rename actually raise EXDEV?

    The store catches EXDEV to switch to an ownership-preserving copy. If a
    cross-mount rename fails with something else, that fallback never engages
    and the publish reports a hard error instead.
    """
    if not alt_dir:
        P.record("P2.6", "cross-mount rename raises EXDEV", SKIPPED,
                 detail="no --alt-dir given",
                 impact="The cross-filesystem publish path is unverified. Pass "
                        "--alt-dir pointing at the workspace/deps filesystem.")
        return
    a = tempfile.mkdtemp(prefix=SCRATCH_NAME, dir=cache_dir)
    b = tempfile.mkdtemp(prefix=SCRATCH_NAME, dir=alt_dir)
    try:
        src = os.path.join(b, "tree")
        os.mkdir(src)
        with open(os.path.join(src, "f"), "w") as fp:
            fp.write("x")
        got = P.errno_of(lambda: os.rename(src, os.path.join(a, "tree")))
        same_dev = os.stat(a).st_dev == os.stat(b).st_dev
        if same_dev:
            P.record("P2.6", "cross-mount rename raises EXDEV", INFO,
                     got=got, detail="--alt-dir is on the SAME device as the "
                                     "cache; nothing crossed.",
                     impact="Re-run with an --alt-dir on the real workspace "
                            "filesystem to exercise this.")
        else:
            P.check("P2.6", "cross-mount rename raises EXDEV", "EXDEV", got,
                    impact="The store switches to a preserving copy on EXDEV. "
                           "A different errno means it raises instead.")
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


# --------------------------------------------------------------------------
# Group 3 -- cache coherence between CLIENTS.
#
# This is the group that cannot be tested on one host and the reason two roles
# exist.  NFS close-to-open consistency says nothing about directory entry
# caching, so a second client can hold a stale view of what exists.
#
#   stale NEGATIVE  -> redundant fetches and wasted work (annoying)
#   stale POSITIVE  -> a client resolves an entry that is gone (breakage)
#   PARTIAL view    -> a client uses a half-published tree (corruption)
# --------------------------------------------------------------------------

MANIFEST = ".ivpm-cache-entry.json"


def _seal_and_publish(staging, entry, nfiles=3):
    """Mimic the store: build, write the manifest last, seal, rename."""
    os.makedirs(staging)
    for i in range(nfiles):
        with open(os.path.join(staging, "f%d.txt" % i), "w") as fp:
            fp.write("payload-%d" % i)
    with open(os.path.join(staging, MANIFEST), "w") as fp:
        json.dump({"schema": 1, "files": nfiles}, fp)
    for name in os.listdir(staging):
        os.chmod(os.path.join(staging, name), 0o444)
    os.chmod(staging, 0o555)
    os.rename(staging, entry)


def _looks_populated(entry):
    """The store's _is_populated, reimplemented without importing IVPM."""
    try:
        if os.path.islink(entry) or not os.path.isdir(entry):
            return False, "absent"
        names = os.listdir(entry)
        if not names:
            return False, "empty"
        if not os.path.isfile(os.path.join(entry, MANIFEST)):
            return True, "PARTIAL: non-empty but no manifest"
        return True, "complete"
    except OSError as e:
        return False, "error: %s" % e


def probe_writer(P, root, run_id, rounds, settle):
    """Publish and evict entries, signalling each transition to the reader."""
    shared = os.path.join(root, "coherence-" + run_id)
    _mkdir_p(shared)
    _barrier(shared, "writer", "reader", timeout=settle)

    for i in range(rounds):
        entry = os.path.join(shared, "entry-%d" % i)
        # Reader is polling this path already (negative-cache priming).
        time.sleep(settle)
        _seal_and_publish(os.path.join(shared, "stage-%d" % i), entry)
        _signal(shared, "published-%d" % i)

        _wait_signal(shared, "saw-%d" % i, timeout=settle * 4)

        _unseal(entry)
        shutil.rmtree(entry, ignore_errors=True)
        _signal(shared, "evicted-%d" % i)
        _wait_signal(shared, "gone-%d" % i, timeout=settle * 4)

    _signal(shared, "done")
    P.record("P3", "writer role complete", INFO,
             got={"rounds": rounds, "shared": shared},
             impact="Results are recorded on the reader side; send both files.")


def probe_reader(P, root, run_id, rounds, settle):
    shared = os.path.join(root, "coherence-" + run_id)
    _mkdir_p(shared)
    _barrier(shared, "reader", "writer", timeout=settle)

    appear, disappear, partials, stale_reads = [], [], [], []

    for i in range(rounds):
        entry = os.path.join(shared, "entry-%d" % i)

        # Prime a NEGATIVE dentry: repeatedly observe that it does not exist.
        deadline = time.time() + settle
        while time.time() < deadline:
            _looks_populated(entry)
            time.sleep(0.05)

        # The writer creates the entry and only THEN the signal, so any delay
        # measured from signal-visible to entry-visible is pure client-side
        # staleness on this host. No clock synchronisation is involved.
        _wait_signal(shared, "published-%d" % i, timeout=settle * 10)
        t0 = time.time()
        seen = None
        while time.time() - t0 < settle * 10:
            populated, how = _looks_populated(entry)
            if populated:
                seen = time.time() - t0
                if how.startswith("PARTIAL"):
                    partials.append({"round": i, "detail": how})
                # Content check: once visible, is it readable and whole?
                try:
                    with open(os.path.join(entry, MANIFEST)) as fp:
                        json.load(fp)
                except (IOError, OSError, ValueError) as e:
                    stale_reads.append({"round": i, "error": str(e)})
                break
            time.sleep(0.02)
        appear.append(seen)
        _signal(shared, "saw-%d" % i)

        _wait_signal(shared, "evicted-%d" % i, timeout=settle * 10)
        t0 = time.time()
        gone = None
        while time.time() - t0 < settle * 10:
            populated, _ = _looks_populated(entry)
            if not populated:
                gone = time.time() - t0
                break
            time.sleep(0.02)
        disappear.append(gone)
        _signal(shared, "gone-%d" % i)

    P.record("P3.1", "latency: another client's publish becomes visible", INFO,
             got={"seconds": appear, "max": _maxf(appear)},
             impact="This is how long a stale NEGATIVE view lasts: for this "
                    "long after a publish, another host still sees a MISS and "
                    "re-fetches. Wasted work, not corruption. Compare with "
                    "acdirmin/acdirmax in the mount options.")
    P.record("P3.2", "latency: another client's eviction becomes visible", INFO,
             got={"seconds": disappear, "max": _maxf(disappear)},
             impact="This is how long a stale POSITIVE view lasts -- the "
                    "dangerous direction. For this long, a host can resolve "
                    "and symlink an entry that no longer exists.")
    P.check("P3.3", "a half-published entry is never observed",
            [], partials,
            impact="CRITICAL: a non-empty directory with no manifest would be "
                   "served as a cache HIT by a legacy-tolerant store. Any "
                   "entry here means the publish rename is not atomic as "
                   "observed from another client.")
    P.check("P3.4", "a visible entry's content is readable and whole",
            [], stale_reads,
            impact="CRITICAL: content visible but unreadable means "
                   "close-to-open consistency is not holding for this "
                   "workload.")


def probe_race(P, root, run_id, procs, rounds):
    """P3.5 -- N processes (across any number of hosts) publish the SAME entry.

    The store's whole concurrency story is that exactly one wins and the rest
    observe a complete entry. Run this simultaneously on every client.
    """
    shared = os.path.join(root, "race-" + run_id)
    _mkdir_p(shared)
    tag = "%s-%d" % (platform.node(), os.getpid())

    outcomes = []
    for i in range(rounds):
        entry = os.path.join(shared, "target-%d" % i)
        _barrier_n(shared, "round-%d" % i, tag)
        staging = os.path.join(shared, "stage-%d-%s" % (i, tag))
        try:
            _seal_and_publish(staging, entry)
            result = "won"
        except OSError as e:
            result = errno.errorcode.get(e.errno, str(e.errno))
            _unseal(staging)
            shutil.rmtree(staging, ignore_errors=True)
        populated, how = _looks_populated(entry)
        outcomes.append({"round": i, "result": result, "entry_state": how})

    P.record("P3.5", "concurrent publishers of one entry", INFO,
             got={"tag": tag, "outcomes": outcomes},
             impact="Merge the outcomes from every participant: exactly one "
                    "'won' per round, every other result ENOTEMPTY/EEXIST, and "
                    "entry_state 'complete' for ALL of them. Two winners means "
                    "the lock-free publish does not hold on this mount.")


# --------------------------------------------------------------------------
# Group 4 -- deletion behavior peculiar to NFS.
# --------------------------------------------------------------------------

def probe_silly_rename(P, root):
    """NFS renames a deleted-but-open file to .nfsXXXX rather than unlinking.

    Eviction removes a tombstone with rmtree. If a consumer on any client has a
    file open inside it, the rmtree can leave .nfs* residue and fail ENOTEMPTY
    -- so the tombstone survives, and the sweep retries it forever.
    """
    d = os.path.join(root, "silly")
    os.mkdir(d)
    target = os.path.join(d, "victim.txt")
    with open(target, "w") as fp:
        fp.write("x")
    held = open(target, "r")
    try:
        os.unlink(target)
        leftovers = [n for n in os.listdir(d) if n.startswith(".nfs")]
        P.record("P4.1", "silly-rename on unlink of an open file", INFO,
                 got={"leftovers": leftovers},
                 impact="Non-empty here means eviction of an in-use entry "
                        "leaves .nfs* residue and the tombstone rmtree fails. "
                        "Inert (the entry is already renamed out of view) but "
                        "it accumulates until the last reader closes.")
    finally:
        held.close()
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# Group 5 -- time.  GC decides what to delete from timestamps written by one
# client and mtimes set by the server.
# --------------------------------------------------------------------------

def probe_time(P, root):
    p = os.path.join(root, "clock.txt")
    t_before = time.time()
    with open(p, "w") as fp:
        fp.write("x")
    t_after = time.time()
    server_mtime = os.stat(p).st_mtime
    skew = server_mtime - ((t_before + t_after) / 2.0)
    P.record("P5.1", "client/server clock skew", INFO,
             got={"skew_seconds": round(skew, 3),
                  "server_mtime": server_mtime, "client_time": t_after},
             impact="GC compares a client-written 'last_linked' against the "
                    "server's directory mtime. A skew larger than the cleanup "
                    "window can make entries look older or newer than they "
                    "are. Seconds are fine; minutes are not.")


# --------------------------------------------------------------------------
# Group 6 -- cost.  The seal walk issues a chmod per node. On a local disk that
# is free; on NFS each one is a SETATTR round trip, and a large package could
# turn a fast fetch into a very slow publish. This is a PERFORMANCE question,
# not a correctness one, but it determines whether the design is usable here.
# --------------------------------------------------------------------------

def probe_seal_cost(P, root, nfiles):
    d = os.path.join(root, "sealcost")
    os.makedirs(os.path.join(d, "sub"))
    for i in range(nfiles):
        with open(os.path.join(d, "sub", "f%05d" % i), "w") as fp:
            fp.write("x")

    t0 = time.time()
    walked = 0
    for r, dirs, files in os.walk(d):
        for name in dirs + files:
            os.lstat(os.path.join(r, name))
            walked += 1
    t_walk = time.time() - t0

    t0 = time.time()
    for r, dirs, files in os.walk(d):
        for name in files:
            os.chmod(os.path.join(r, name), 0o444)
        for name in dirs:
            os.chmod(os.path.join(r, name), 0o555)
    t_chmod = time.time() - t0

    per = (t_chmod / walked * 1e3) if walked else 0
    P.record("P6.1", "seal walk cost", INFO,
             got={"nodes": walked,
                  "lstat_seconds": round(t_walk, 3),
                  "chmod_seconds": round(t_chmod, 3),
                  "ms_per_chmod": round(per, 3),
                  "projected_60k_node_seal_seconds": round(per * 60000 / 1e3, 1)},
             impact="The seal chmods every node. Extrapolate to your largest "
                    "package: if that projection is minutes, the seal needs to "
                    "skip nodes whose mode is already correct, or protection "
                    "needs to ride inheritance alone.")
    _unseal(d)


# --------------------------------------------------------------------------
# rendezvous helpers -- files on the shared mount, so no network between hosts
# --------------------------------------------------------------------------

def _mkdir_p(p):
    try:
        os.makedirs(p)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def _signal(shared, name):
    with open(os.path.join(shared, name + ".signal"), "w") as fp:
        fp.write(str(time.time()))


def _wait_signal(shared, name, timeout):
    p = os.path.join(shared, name + ".signal")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(p):
            return True
        time.sleep(0.02)
    return False


def _barrier(shared, me, other, timeout):
    _signal(shared, "ready-" + me)
    if not _wait_signal(shared, "ready-" + other, timeout=max(timeout, 60) * 5):
        raise SystemExit(
            "timed out waiting for the '%s' role; start it on the other "
            "client with the same --run-id" % other)


def _barrier_n(shared, round_name, tag):
    """Loose barrier for the race probe: announce, then wait a fixed moment.

    Deliberately not a counted barrier -- the number of participants is not
    known to any one of them, and the probe only needs them to overlap.
    """
    _signal(shared, "%s-%s" % (round_name, tag))
    now = time.time()
    time.sleep(max(0.0, 2.0 - (now % 2.0)))     # align on a 2s boundary


def _unseal(path):
    for r, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            try:
                os.chmod(os.path.join(r, name), 0o700)
            except OSError:
                pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _maxf(vals):
    real = [v for v in vals if v is not None]
    return max(real) if real else None


def _resolve_group(name):
    if name is None:
        others = [g for g in os.getgroups() if g != os.getgid()]
        return (others[0], None) if others else (None, None)
    if name.isdigit():
        return int(name), None
    import grp
    try:
        e = grp.getgrnam(name)
        return e.gr_gid, e.gr_name
    except KeyError:
        raise SystemExit("unknown group: %s" % name)


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", required=True,
                    help="a writable path on the NFS mount used for the cache")
    ap.add_argument("--alt-dir",
                    help="a path on the workspace/deps filesystem, to test "
                         "cross-mount rename (P2.6)")
    ap.add_argument("--group",
                    help="group name or gid a policy would use (e.g. server2)")
    ap.add_argument("--role", default="solo",
                    choices=["solo", "writer", "reader", "racer"],
                    help="solo: all single-host probes. writer/reader: run "
                         "together on two clients. racer: run on every client "
                         "at once.")
    ap.add_argument("--run-id", default="default",
                    help="must match across roles")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--settle", type=float, default=3.0,
                    help="seconds to prime caches; set above acdirmax")
    ap.add_argument("--scale", type=int, default=2000,
                    help="file count for the seal-cost probe (P6.1)")
    ap.add_argument("--out", default="nfs-characterization.json")
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch directory in place")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.cache_dir):
        raise SystemExit("not a directory: %s" % args.cache_dir)

    gid, gname = _resolve_group(args.group)
    P = Probes()
    env = probe_environment(P, args.cache_dir)

    root = os.path.join(args.cache_dir, SCRATCH_NAME + "-" + args.run_id)
    if args.role in ("solo", "racer"):
        root = root + "-" + platform.node() + "-" + str(os.getpid())
    _mkdir_p(root)

    try:
        if args.role == "solo":
            probe_protection(P, root, gid, gname or args.group)
            probe_rename(P, root)
            probe_cross_device(P, args.cache_dir, args.alt_dir)
            probe_silly_rename(P, root)
            probe_time(P, root)
            probe_seal_cost(P, root, args.scale)
        elif args.role == "writer":
            probe_writer(P, os.path.dirname(root) or args.cache_dir,
                         args.run_id, args.rounds, args.settle)
        elif args.role == "reader":
            probe_reader(P, os.path.dirname(root) or args.cache_dir,
                         args.run_id, args.rounds, args.settle)
        elif args.role == "racer":
            probe_race(P, args.cache_dir, args.run_id, 1, args.rounds)
    finally:
        if not args.keep:
            _unseal(root)
            shutil.rmtree(root, ignore_errors=True)

    report = {
        "schema": 1,
        "generated": time.time(),
        "role": args.role,
        "run_id": args.run_id,
        "args": vars(args),
        "environment": env,
        "probes": P.results,
    }
    with open(args.out, "w") as fp:
        json.dump(report, fp, indent=2, sort_keys=True, default=str)

    _print_summary(P.results, args.out)
    return 0


def _print_summary(results, out):
    order = {DEVIATION: 0, ERROR: 1, SKIPPED: 2, OK: 3, INFO: 4}
    print("")
    print("%-9s %-52s %s" % ("STATUS", "PROBE", "RESULT"))
    print("-" * 100)
    for r in sorted(results, key=lambda r: (order.get(r["status"], 9), r["id"])):
        got = r.get("got")
        if isinstance(got, (dict, list)):
            got = "(see JSON)"
        print("%-9s %-52s %s" % (r["status"].upper(),
                                 ("%s %s" % (r["id"], r["name"]))[:52],
                                 str(got)[:35]))
    bad = [r for r in results if r["status"] in (DEVIATION, ERROR)]
    print("")
    if bad:
        print("%d probe(s) deviate from what the cache store assumes:" % len(bad))
        for r in bad:
            print("  [%s] %s" % (r["id"], r["name"]))
            print("      expected %r, got %r" % (r.get("expect"), r.get("got")))
            if r.get("impact"):
                print("      impact: %s" % r["impact"])
    else:
        print("No deviations in the asserted probes. The INFO rows still need "
              "review -- latencies and costs are judgement calls, not "
              "pass/fail.")
    print("")
    print("Wrote %s" % out)


if __name__ == "__main__":
    sys.exit(main())
