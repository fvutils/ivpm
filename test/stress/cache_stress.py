#!/usr/bin/env python3
#****************************************************************************
#* cache_stress.py
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
"""Multi-host stress test for IVPM's shared cache.

Run the same ``run`` command on 2..N hosts against one shared directory, then
``analyze`` the merged event logs.  See docs/cache-stress-test.md for usage and
cache-lost-race-design.md (Part B) for the rationale.

Subcommands::

    launch   ssh fan-out: start 'run' on every host with one shared start time
    run      be one host's participants (spawns --workers processes)
    analyze  merge event logs, check invariants I1-I9, print a report
    clean    remove a run's directory (requires the run's sentinel)

Participants never coordinate through files on the shared mount -- that mount
is the thing under test, and a rendezvous file is subject to exactly the
stale-lookup delays being measured.  Instead every participant derives the
same slot schedule from the command line and a shared ``--start-at`` wall
clock time; clock skew between hosts is estimated and recorded so the
analysis can show how well a "race" actually overlapped.
"""
import argparse
import dataclasses as dc
import errno
import hashlib
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_SRC = os.path.normpath(os.path.join(HERE, "..", "..", "src"))

SENTINEL = ".ivpm-stress-run"
SCENARIOS = ("S1", "S2", "S3", "S4", "S5")
ENTRY_MANIFEST = ".ivpm-cache-entry.json"
DIVERGENCE_RECORD = ".ivpm-cache-divergence.json"
ALT_MARKER = "~alt~"
S2_METHODS = ("none", "listdir", "opendir_fstat", "stat_parent",
              "open_manifest", "listdir_manifest")

# Pinned so a commit hash is a pure function of content: every participant
# builds its own local remotes and still produces identical cache keys.
GIT_ENV = dict(
    GIT_AUTHOR_NAME="stress", GIT_AUTHOR_EMAIL="stress@ivpm",
    GIT_COMMITTER_NAME="stress", GIT_COMMITTER_EMAIL="stress@ivpm",
    GIT_AUTHOR_DATE="2020-01-01T00:00:00+0000",
    GIT_COMMITTER_DATE="2020-01-01T00:00:00+0000",
)


# ==========================================================================
# shared helpers
# ==========================================================================

def run_dir(root, run_id):
    return os.path.join(os.path.abspath(root), run_id)


def gen_files(seed, nfiles, size):
    """Deterministic tree content: ``(relpath, bytes)`` pairs."""
    for i in range(nfiles):
        rel = "d%03d/f%05d.txt" % (i // 100, i)
        h = hashlib.sha256(("%s:%d" % (seed, i)).encode()).hexdigest()
        yield rel, (h * (size // 64 + 1))[:size].encode()


def expected_digest(seed, nfiles, size):
    lines = sorted("%s\0%s" % (rel, hashlib.sha256(data).hexdigest())
                   for rel, data in gen_files(seed, nfiles, size))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def write_tree(path, seed, nfiles, size):
    for rel, data in gen_files(seed, nfiles, size):
        fp = os.path.join(path, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(data)


_DIGEST_SKIP = {ENTRY_MANIFEST, DIVERGENCE_RECORD, ".git", "ivpm.yaml"}


def tree_digest(path):
    """Digest of a tree's generated content (cache machinery skipped)."""
    lines = []
    for root, dirs, files in os.walk(path):
        if root == path:
            dirs[:] = [d for d in dirs if d not in _DIGEST_SKIP]
            files = [f for f in files if f not in _DIGEST_SKIP]
        for f in files:
            fp = os.path.join(root, f)
            with open(fp, "rb") as fh:
                d = hashlib.sha256(fh.read()).hexdigest()
            lines.append("%s\0%s" % (os.path.relpath(fp, path), d))
    if not lines and not os.path.isdir(path):
        raise FileNotFoundError(errno.ENOENT, "no such tree", path)
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def errname(e):
    n = getattr(e, "errno", None)
    return errno.errorcode.get(n, str(n)) if n is not None else type(e).__name__


def make_writable_and_remove(path):
    for root, dirs, files in os.walk(path):
        for n in dirs:
            try:
                p = os.path.join(root, n)
                if not os.path.islink(p):
                    os.chmod(p, 0o700)
            except OSError:
                pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=True)


def mount_for(path):
    """``{mount_point, fstype, source, options}`` for *path*, or None."""
    try:
        with open("/proc/self/mountinfo") as fp:
            text = fp.read()
    except OSError:
        return None
    real, best = os.path.realpath(path), None
    for line in text.splitlines():
        parts = line.split()
        if "-" not in parts:
            continue
        sep = parts.index("-")
        mp = parts[4].replace("\\040", " ")
        if mp == "/" or real == mp or real.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) >= len(best["mount_point"]):
                best = {"mount_point": mp, "fstype": parts[sep + 1],
                        "source": parts[sep + 2],
                        "options": ",".join(parts[5:6] + parts[sep + 3:sep + 4])}
    return best


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    k = min(len(v) - 1, max(0, int(round(q / 100.0 * (len(v) - 1)))))
    return v[k]


# ==========================================================================
# schedule
# ==========================================================================

@dc.dataclass
class Slot:
    scenario: str
    round: int
    start: float
    period: float

    @property
    def end(self):
        return self.start + self.period


def periods(cfg):
    return {"S1": cfg.s1_period, "S2": cfg.s2_period, "S3": cfg.s3_period,
            "S4": cfg.s3_period, "S5": cfg.s5_period}


def schedule(cfg):
    """Every participant computes this identically from its arguments."""
    t, per = cfg.start_at, periods(cfg)
    out = []
    for scen in cfg.scenarios:
        for r in range(cfg.rounds):
            out.append(Slot(scen, r, t, per[scen]))
            t += per[scen]
    return out


def sleep_until(t):
    while True:
        d = t - time.time()
        if d <= 0:
            return
        time.sleep(min(d, 1.0))


# ==========================================================================
# participant
# ==========================================================================

class Participant:
    def __init__(self, cfg, worker):
        self.cfg = cfg
        self.worker = worker
        self.p = cfg.host_index * cfg.workers + worker
        self.P = cfg.hosts * cfg.workers
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self.id = "%s-%d" % (self.host, self.pid)
        self.rd = run_dir(cfg.root, cfg.run_id)
        self.cache_dir = os.path.join(self.rd, "cache")
        self.local = os.path.join(cfg.local_scratch,
                                  "ivpm-stress-%s-%s" % (cfg.run_id, self.id))
        os.makedirs(os.path.join(self.rd, "events"), exist_ok=True)
        self._ev = open(os.path.join(self.rd, "events", self.id + ".jsonl"), "a")

        from ivpm.cache import DirectoryCacheStore
        self.store_cls = _stress_store_cls(DirectoryCacheStore)
        self.store = self.store_cls(self.cache_dir)
        import inspect
        sig = inspect.signature(DirectoryCacheStore.store_version)
        self.has_monitor = "monitor" in sig.parameters

    # --- events ---------------------------------------------------------

    def emit(self, kind, **fields):
        ev = {"kind": kind, "t": time.time(), "host": self.host,
              "pid": self.pid, "p": self.p, "id": self.id}
        ev.update(fields)
        self._ev.write(json.dumps(ev, sort_keys=True, default=str) + "\n")
        self._ev.flush()
        try:
            os.fsync(self._ev.fileno())
        except OSError:
            pass

    def clock_offset(self):
        """server_time - local_time, from a file write's server-set mtime."""
        d = os.path.join(self.rd, "clock")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, self.id)
        t0 = time.time()
        with open(path, "w") as f:
            f.write("x")
            f.flush()
            os.fsync(f.fileno())
            st = os.fstat(f.fileno())
        t1 = time.time()
        return st.st_mtime - (t0 + t1) / 2

    def budget(self):
        try:
            from ivpm.cache_adopt import resolve_adopt_budget
            b = resolve_adopt_budget(self.cache_dir)
            return {"seconds": b.seconds, "source": b.source}
        except Exception as e:
            return {"seconds": None, "source": "unavailable: %s" % e}

    # --- lifecycle -------------------------------------------------------

    def main(self):
        cfg = self.cfg
        try:
            offset = self.clock_offset()
        except OSError:
            offset = None
        self.emit("start", kernel=platform.release(), python=sys.version.split()[0],
                  ivpm_src=cfg.ivpm_src, mount=mount_for(self.cache_dir),
                  budget=self.budget(), clock_offset=offset,
                  has_monitor=self.has_monitor, P=self.P,
                  args=cfg.args_echo)
        os.makedirs(self.local, exist_ok=True)
        try:
            if any(s in cfg.scenarios for s in ("S3", "S4")):
                t0 = time.time()
                self.build_remotes()
                self.emit("setup", what="remotes", seconds=time.time() - t0)
            for slot in schedule(cfg):
                if time.time() >= slot.end - 1:
                    self.emit("skipped", scenario=slot.scenario, round=slot.round,
                              reason="overrun", late_by=time.time() - slot.start)
                    continue
                sleep_until(slot.start)
                fn = getattr(self, "s_" + slot.scenario.lower())
                try:
                    fn(slot)
                except Exception as e:
                    self.emit("result", scenario=slot.scenario, round=slot.round,
                              outcome="error", error=repr(e), errno=errname(e),
                              traceback=traceback.format_exc()[-2000:])
        finally:
            self.emit("end")
            self._ev.close()
            if not cfg.keep_local:
                shutil.rmtree(self.local, ignore_errors=True)

    # --- S1: publish race --------------------------------------------------

    def s_s1(self, slot):
        cfg = self.cfg
        go = slot.start + cfg.lead
        variant = "primed" if (slot.round % 2 == 0 or not cfg.s1_cold) else "cold"
        pkg, key = "s1pkg", "r%d-%s" % (slot.round, variant)
        seed = "s1-%s" % key
        src = self.store.new_staging(pkg)
        write_tree(src, seed, cfg.files, cfg.file_size)
        if time.time() >= go:
            self.store._discard(os.path.dirname(src))
            self.emit("skipped", scenario="S1", round=slot.round,
                      reason="late: tree not built by go")
            return
        if variant == "primed":
            prime_from = go - cfg.prime
            sleep_until(prime_from)
            while time.time() < go:
                self.store.has_version(pkg, key)       # caches the negative lookup
                time.sleep(0.2)
        sleep_until(go)
        self._publish("S1", slot, pkg, key, seed, src, go, variant=variant)

    # _publish is _publish_impl, assigned after the class body.

    # --- S2: visibility --------------------------------------------------

    def s_s2(self, slot):
        cfg = self.cfg
        go = slot.start + cfg.lead
        writer = slot.round % self.P
        pkg, key = "s2pkg", "r%d" % slot.round
        seed = "s2-%s" % key
        entry = self.store.get_version_cache_dir(pkg, key)
        parent = os.path.dirname(entry)
        if self.p == writer:
            src = self.store.new_staging(pkg)
            write_tree(src, seed, max(1, cfg.files // 10), cfg.file_size)
            sleep_until(go)
            self._publish("S2", slot, pkg, key, seed, src, go, role="writer")
            return

        method = S2_METHODS[(self.p + slot.round) % len(S2_METHODS)]
        if time.time() >= go:
            self.emit("skipped", scenario="S2", round=slot.round, reason="late")
            return
        sleep_until(go - cfg.prime)
        while time.time() < go:
            os.path.lexists(entry)                      # prime a negative lookup
            time.sleep(0.2)
        check = _S2_CHECKS[method]
        start, visible_at, attempts, err = time.time(), None, 0, None
        limit = start + cfg.poll_limit
        while time.time() < limit:
            attempts += 1
            try:
                if check(parent, entry):
                    visible_at = time.time()
                    break
            except OSError as e:
                err = errname(e)
            time.sleep(cfg.poll_interval)
        half = False
        manifest_ok = None
        if visible_at is not None:
            try:
                with open(os.path.join(entry, ENTRY_MANIFEST)) as f:
                    json.load(f)
                manifest_ok = True
            except (OSError, ValueError):
                manifest_ok = False
                try:
                    half = bool(os.listdir(entry))
                except OSError:
                    pass
        self.emit("result", scenario="S2", round=slot.round, role="reader",
                  method=method, go=go, start=start, visible_at=visible_at,
                  attempts=attempts, last_error=err, manifest_ok=manifest_ok,
                  half_published=half, poll_limit=cfg.poll_limit,
                  budget=self.budget(), outcome="visible" if visible_at else
                  "not_visible")

    # --- S3/S4: end-to-end ivpm update ------------------------------------

    def remote(self, k):
        return os.path.join(self.local, "remotes", "dep%02d" % k)

    def build_remotes(self):
        cfg = self.cfg
        env = dict(os.environ, **GIT_ENV)

        def git(cwd, *a):
            subprocess.run(("git",) + a, cwd=cwd, env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for k in range(cfg.deps):
            repo = self.remote(k)
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "commit.gpgsign", "false")
            for r in range(cfg.rounds):
                write_tree(repo, "s3-dep%02d-r%d" % (k, r), cfg.dep_files,
                           cfg.file_size)
                git(repo, "add", "-A")
                git(repo, "commit", "-q", "-m", "r%d" % r)
                git(repo, "tag", "r%d" % r)

    def _update(self, scen, slot, tag_round):
        cfg = self.cfg
        ws = os.path.join(self.rd, "ws", self.id, "%s-r%d" % (scen, slot.round))
        os.makedirs(ws, exist_ok=True)
        lines = ["package:", "  name: stress_ws", "  dep-sets:",
                 "    - name: default-dev", "      deps:"]
        for k in range(cfg.deps):
            lines += ["        - name: dep%02d" % k,
                      "          url: file://%s" % self.remote(k),
                      "          src: git",
                      "          tag: r%d" % tag_round,
                      "          cache: true"]
        with open(os.path.join(ws, "ivpm.yaml"), "w") as f:
            f.write("\n".join(lines) + "\n")
        snippet = ("import sys; sys.path.insert(0, %r)\n"
                   "from ivpm.project_ops import ProjectOps\n"
                   "class A: anonymous_git = None\n"
                   "ProjectOps(%r, A()).update(dep_set='default-dev', "
                   "skip_venv=True, args=A())\n" % (cfg.ivpm_src, ws))
        env = dict(os.environ, IVPM_CACHE=self.cache_dir)
        t0 = time.time()
        try:
            proc = subprocess.run([sys.executable, "-c", snippet], env=env,
                                  capture_output=True, text=True,
                                  timeout=max(10, slot.end - time.time() - 2))
            rc, out = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as e:
            rc, out = "timeout", str(e.stdout or "") + str(e.stderr or "")
        return ws, rc, out, time.time() - t0

    def _check_deps(self, ws, tag_round, churn):
        """(content_failures, partial_trees, dangling)."""
        cfg = self.cfg
        bad, partial, dangling = [], [], []
        for k in range(cfg.deps):
            link = os.path.join(ws, "packages", "dep%02d" % k)
            want = expected_digest("s3-dep%02d-r%d" % (k, tag_round),
                                   cfg.dep_files, cfg.file_size)
            if not os.path.exists(link):
                (dangling if churn else bad).append(link)
                continue
            try:
                ok = tree_digest(link) == want
            except OSError:
                ok = False
            if not ok:
                if churn and not os.path.exists(link):
                    dangling.append(link)       # evicted while we read it
                elif churn:
                    partial.append(link)
                else:
                    bad.append(link)
        return bad, partial, dangling

    def s_s3(self, slot):
        ws, rc, out, secs = self._update("S3", slot, slot.round)
        bad, _, _ = self._check_deps(ws, slot.round, churn=False)
        self.emit("result", scenario="S3", round=slot.round,
                  outcome="ok" if rc == 0 else "error", rc=rc, seconds=secs,
                  content_ok=not bad, content_failures=bad[:5],
                  divergent_notes=out.count("Using this run's own copy"),
                  output_tail=out[-3000:] if rc != 0 or bad else "")

    def s_s4(self, slot):
        cfg = self.cfg
        churner = (self.P > 1 and
                   self.p % cfg.churn_every == cfg.churn_every - 1)
        if churner:
            from ivpm.cache import DirectoryCacheStore
            store, removed, loops, errs = DirectoryCacheStore(self.cache_dir), 0, 0, []
            while time.time() < slot.end - 2:
                try:
                    removed += store.clean_older_than(0)
                except Exception as e:
                    errs.append(repr(e))
                loops += 1
                time.sleep(cfg.churn_sleep)
            self.emit("result", scenario="S4", round=slot.round, role="churner",
                      outcome="error" if errs else "ok", removed=removed,
                      loops=loops, errors=errs[:5])
            return
        tag_round = slot.round % cfg.rounds
        ws, rc, out, secs = self._update("S4", slot, tag_round)
        bad, partial, dangling = self._check_deps(ws, tag_round, churn=True)
        self.emit("result", scenario="S4", round=slot.round, role="updater",
                  outcome="ok" if rc == 0 else "error", rc=rc, seconds=secs,
                  content_ok=not bad, partial_trees=partial[:5],
                  partial=len(partial), dangling_after=len(dangling),
                  output_tail=out[-3000:] if rc != 0 or partial else "")

    # --- S5: hit-path load -------------------------------------------------

    def s_s5(self, slot):
        cfg = self.cfg
        from ivpm.cache_provider import CacheContext, DirectoryCacheProvider
        pkg_name = "s5pkg"

        class Pkg:
            name, cache, src_type = pkg_name, True, "stress"

        for i in range(cfg.s5_entries):
            key = "e%d" % i
            if not self.store.has_version(pkg_name, key):
                src = self.store.new_staging(pkg_name)
                write_tree(src, "s5-%s" % key, max(1, cfg.files // 10),
                           cfg.file_size)
                try:
                    self.store.store_version(pkg_name, key, src)
                except Exception as e:
                    self.emit("result", scenario="S5", round=slot.round,
                              role="seed", outcome="error", error=repr(e))
        deps = os.path.join(self.local, "s5deps")
        os.makedirs(deps, exist_ok=True)
        ctx = CacheContext(root_name=None, root_version=None, root_dir=self.local,
                           deps_dir=deps)
        rng = random.Random("%s-%d" % (self.id, slot.round))
        ops = hits = misses = 0
        errors, content_bad = [], []
        while time.time() < slot.end - 2:
            i = rng.randrange(cfg.s5_entries)
            level = ("shape", "content")[ops % 2]
            prov = DirectoryCacheProvider(ctx, self.store_cls(self.cache_dir),
                                          verify_level=level)
            ops += 1
            try:
                res = prov.lookup(Pkg(), "e%d" % i)
                if not res.is_hit:
                    misses += 1
                    continue
                hits += 1
                link = prov.materialize(Pkg(), "e%d" % i)
                want = expected_digest("s5-e%d" % i, max(1, cfg.files // 10),
                                       cfg.file_size)
                if tree_digest(link) != want:
                    content_bad.append(link)
                prov.note_reference(Pkg())
            except Exception as e:
                errors.append(repr(e))
        self.emit("result", scenario="S5", round=slot.round,
                  outcome="error" if errors else "ok", ops=ops, hits=hits,
                  misses=misses, errors=errors[:5], content_ok=not content_bad,
                  content_failures=content_bad[:5])


def _publish_impl(self, scen, slot, pkg, key, seed, src, go, **extra):
    """store_version at *go*; classify the outcome and check what we got."""
    cfg = self.cfg
    nfiles = max(1, cfg.files // 10) if scen == "S2" else cfg.files
    mon = _Monitor()
    kwargs = {"monitor": mon} if self.has_monitor else {}
    store = self.store
    store.publish_state, store.publish_time = None, None
    t_call = time.time()
    try:
        path = store.store_version(pkg, key, src, **kwargs)
        err = None
    except Exception as e:
        path, err = None, e
    t_ret = time.time()

    if err is not None:
        outcome = "error"
    elif store.publish_state == "won":
        outcome = "won"
    elif mon.result is not None:
        outcome = mon.result.outcome
    elif store.publish_state == "lost":
        outcome = "lost_returned"           # pre-Part-A code: no wait, no record
    else:
        outcome = "hit"

    content_ok, content_error = None, None
    if path is not None:
        try:
            content_ok = tree_digest(path) == expected_digest(seed, nfiles,
                                                              cfg.file_size)
        except OSError as e:
            content_ok, content_error = False, errname(e)
    obs = mon.result.observations if mon.result is not None else []
    self.emit(
        "result", scenario=scen, round=slot.round, key=key, outcome=outcome,
        go=go, t_call=t_call, t_return=t_ret, publish_time=store.publish_time,
        path=path, content_ok=content_ok, content_error=content_error,
        error=repr(err) if err else None,
        errno=errname(getattr(err, "cause", None) or err) if err else None,
        waited_s=mon.result.waited_s if mon.result else None,
        budget=(mon.result.budget.to_json() if mon.result else self.budget()),
        observations=obs[:60], probes=len(obs),
        half_published=any(o.get("result") == "no-manifest" for o in obs),
        **extra)


Participant._publish = _publish_impl


class _Monitor:
    """Duck-typed AdoptMonitor: just keep the result."""

    def __init__(self):
        self.result = None

    def started(self, budget):
        pass

    def progress(self, elapsed, budget, detail):
        pass

    def finished(self, result):
        self.result = result


def _stress_store_cls(base):
    class StressStore(base):
        """Records whether *this* process's publish rename won or lost."""
        publish_state = None
        publish_time = None

        def _move_and_publish(self, *a, **kw):
            try:
                r = super()._move_and_publish(*a, **kw)
            except BaseException as e:
                cause = getattr(e, "cause", e)
                self.publish_state = (
                    "lost" if getattr(cause, "errno", None)
                    in (errno.ENOTEMPTY, errno.EEXIST) else "failed")
                raise
            self.publish_state, self.publish_time = "won", time.time()
            return r
    return StressStore


def _m_none(parent, entry):
    return os.path.lexists(entry)


def _m_listdir(parent, entry):
    try:
        os.listdir(parent)
    except OSError:
        pass
    return os.path.lexists(entry)


def _m_opendir_fstat(parent, entry):
    try:
        fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fstat(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    return os.path.lexists(entry)


def _m_stat_parent(parent, entry):
    try:
        os.stat(parent)
    except OSError:
        pass
    return os.path.lexists(entry)


def _m_open_manifest(parent, entry):
    try:
        with open(os.path.join(entry, ENTRY_MANIFEST)):
            return True
    except OSError:
        return False


def _m_listdir_manifest(parent, entry):
    try:
        os.listdir(parent)
    except OSError:
        pass
    return _m_open_manifest(parent, entry)


_S2_CHECKS = {"none": _m_none, "listdir": _m_listdir,
              "opendir_fstat": _m_opendir_fstat, "stat_parent": _m_stat_parent,
              "open_manifest": _m_open_manifest,
              "listdir_manifest": _m_listdir_manifest}


# ==========================================================================
# run / launch
# ==========================================================================

def _worker(cfg, w):
    sys.path.insert(0, cfg.ivpm_src)
    os.environ["IVPM_CACHE_VERIFY"] = cfg.verify_level
    if cfg.adopt_wait is not None:
        os.environ["IVPM_CACHE_ADOPT_WAIT"] = str(cfg.adopt_wait)
    if not cfg.verbose:
        _quiet_ivpm()
    Participant(cfg, w).main()


def _quiet_ivpm():
    """Keep IVPM's per-entry notes out of the console; warnings still show.

    Everything that matters is in the event log, and N workers each printing
    "Linked ..." for every operation buries the warnings worth seeing.
    """
    try:
        from ivpm.msg import get_reporter
        from ivpm.diagnostics import Severity

        class _Sink:
            def emit(self, diag):
                if diag.severity >= Severity.WARNING:
                    print("[%s] %s" % (socket.gethostname(),
                                       diag.format(excerpt=False)),
                          file=sys.stderr, flush=True)
        get_reporter().sink = _Sink()
    except Exception:
        pass


def cmd_run(cfg):
    if cfg.host_list:
        hosts = cfg.host_list.split(",")
        me = socket.gethostname()
        short = me.split(".")[0]
        idx = [i for i, h in enumerate(hosts) if h in (me, short)
               or h.split(".")[0] == short]
        if not idx:
            sys.exit("this host (%s) is not in --host-list" % me)
        cfg.host_index, cfg.hosts = idx[0], len(hosts)
    if cfg.host_index is None or cfg.hosts is None:
        sys.exit("give --host-index and --hosts, or --host-list")
    if cfg.start_at is None:
        sys.exit("give --start-at (the same epoch time on every host); "
                 "'launch' computes one for you")
    rd = run_dir(cfg.root, cfg.run_id)
    os.makedirs(rd, exist_ok=True)
    sentinel = os.path.join(rd, SENTINEL)
    if not os.path.exists(sentinel):
        try:
            with open(sentinel, "x") as f:
                json.dump({"run_id": cfg.run_id, "created": time.time()}, f)
        except FileExistsError:
            pass
    slots = schedule(cfg)
    end = slots[-1].end if slots else cfg.start_at
    print("[%s] %d slot(s) from %s to %s (%.0f min); %d worker(s)"
          % (socket.gethostname(), len(slots),
             time.strftime("%H:%M:%S", time.localtime(cfg.start_at)),
             time.strftime("%H:%M:%S", time.localtime(end)),
             (end - cfg.start_at) / 60, cfg.workers), flush=True)
    if cfg.dry_schedule:
        for s in slots:
            print("  %s r%-3d %s +%ds" % (s.scenario, s.round, time.strftime(
                "%H:%M:%S", time.localtime(s.start)), s.period))
        return 0
    if time.time() > cfg.start_at:
        print("warning: --start-at is already in the past; early slots will "
              "be skipped", file=sys.stderr)
    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_worker, args=(cfg, w)) for w in range(cfg.workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    bad = [p.exitcode for p in procs if p.exitcode]
    print("[%s] done; %d worker(s) failed" % (socket.gethostname(), len(bad)),
          flush=True)
    return 1 if bad else 0


def cmd_launch(cfg, run_args):
    hosts = [h for h in cfg.hosts_csv.split(",") if h]
    start_at = int(time.time() + cfg.delay)
    script = os.path.abspath(__file__)
    procs = []
    for i, h in enumerate(hosts):
        remote = ("cd %s && %s %s run %s --host-index %d --hosts %d "
                  "--start-at %d" % (
                      _q(cfg.cwd), cfg.python, _q(script),
                      " ".join(_q(a) for a in run_args), i, len(hosts), start_at))
        cmd = cfg.ssh.split() + [h, remote]
        print("launch %s: %s" % (h, remote), flush=True)
        procs.append((h, subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True)))
    rc = 0
    import threading

    def pump(h, p):
        for line in p.stdout:
            sys.stdout.write("%s| %s" % (h, line))
            sys.stdout.flush()
    threads = [threading.Thread(target=pump, args=hp) for hp in procs]
    for t in threads:
        t.start()
    for (h, p), t in zip(procs, threads):
        p.wait()
        t.join()
        if p.returncode:
            print("%s exited %d" % (h, p.returncode), flush=True)
            rc = 1
    return rc


def _q(s):
    import shlex
    return shlex.quote(str(s))


# ==========================================================================
# analyze
# ==========================================================================

def load_events(rd):
    evs = []
    d = os.path.join(rd, "events")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with open(os.path.join(d, name)) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evs.append(json.loads(line))
                except ValueError:
                    evs.append({"kind": "corrupt", "file": name, "line": line[:200]})
    return evs


def cmd_analyze(cfg):
    rd = run_dir(cfg.root, cfg.run_id)
    sys.path.insert(0, cfg.ivpm_src)
    evs = load_events(rd)
    starts = {e["id"]: e for e in evs if e.get("kind") == "start"}
    results = [e for e in evs if e.get("kind") == "result"]
    skipped = [e for e in evs if e.get("kind") == "skipped"]
    ends = {e["id"] for e in evs if e.get("kind") == "end"}
    offset = {i: (s.get("clock_offset") or 0.0) for i, s in starts.items()}
    violations = {k: [] for k in ("I1", "I2", "I3", "I4", "I5", "I6", "I7",
                                  "I8", "I9")}
    report = {"run_dir": rd, "participants": len(starts),
              "hosts": sorted({s["host"] for s in starts.values()}),
              "unfinished": sorted(set(starts) - ends), "violations": violations}

    # --- I1: exactly one winner per S1/S2 publish -------------------------
    by_key = {}
    for e in results:
        if e.get("scenario") in ("S1", "S2") and e.get("key"):
            by_key.setdefault((e["scenario"], e["key"]), []).append(e)
    for (scen, key), es in sorted(by_key.items()):
        won = [e["id"] for e in es if e.get("outcome") == "won"]
        if scen == "S1" and len(won) != 1:
            violations["I1"].append({"key": key, "winners": won,
                                     "participants": len(es)})

    # --- I2/I3/I4/I8/I9 per event --------------------------------------
    for e in results:
        tag = {"scenario": e.get("scenario"), "round": e.get("round"),
               "id": e.get("id")}
        if e.get("outcome") == "error":
            violations["I2"].append(dict(tag, error=e.get("error") or e.get("rc"),
                                         errno=e.get("errno"),
                                         tail=(e.get("output_tail") or "")[-600:]))
        if e.get("content_ok") is False:
            violations["I3"].append(dict(tag, path=e.get("path"),
                                         error=e.get("content_error"),
                                         failures=e.get("content_failures")))
        if e.get("half_published"):
            violations["I4"].append(dict(tag, method=e.get("method"),
                                         path=e.get("path")))
        if e.get("outcome") == "adopted_after_wait":
            b = (e.get("budget") or {}).get("seconds")
            if b is not None and (e.get("waited_s") or 0) > b + 1:
                violations["I8"].append(dict(tag, waited_s=e["waited_s"],
                                             budget=b))
        if e.get("partial"):
            violations["I9"].append(dict(tag, trees=e.get("partial_trees")))

    # --- cache-side checks: I5, I6, I7 ---------------------------------
    cache_dir = os.path.join(rd, "cache")
    alt_events = {e.get("path"): e for e in results
                  if e.get("path") and ALT_MARKER in os.path.basename(e["path"])}
    for root, dirs, files in os.walk(cache_dir):
        for n in list(dirs):
            p = os.path.join(root, n)
            if ".staging." in n or ".gc." in n:
                violations["I5"].append(p)
                dirs.remove(n)
            elif ALT_MARKER in n:
                dirs.remove(n)
                try:
                    with open(os.path.join(p, DIVERGENCE_RECORD)) as f:
                        rec = json.load(f)
                except (OSError, ValueError) as ex:
                    violations["I6"].append({"path": p, "problem": repr(ex)})
                    continue
                ev = alt_events.get(p)
                if ev is None:
                    violations["I6"].append({"path": p, "problem":
                                             "no participant reported it"})
                elif ev.get("outcome") != rec.get("reason"):
                    violations["I6"].append({"path": p, "record": rec.get("reason"),
                                             "event": ev.get("outcome")})
            elif root == cache_dir or os.path.dirname(root) == cache_dir \
                    or os.path.basename(root).startswith("p."):
                continue
            else:
                dirs.remove(n)          # inside an entry; not machinery
    if not cfg.no_verify:
        try:
            from ivpm.cache import DirectoryCacheStore
            from ivpm import cache_verify as cv
            res = cv.verify_cache(DirectoryCacheStore(cache_dir), level="content")
            report["verify"] = {"status": res.status.value,
                                "by_problem": res.by_problem()}
            for f in res.findings:
                if f.problem in cv.SERVES_WRONG_CONTENT:
                    violations["I7"].append({"problem": f.problem.value,
                                             "path": f.path, "detail": f.detail})
        except Exception as ex:
            report["verify"] = {"error": repr(ex)}

    # --- descriptive stats ---------------------------------------------
    report["outcomes"] = _outcomes(results, starts)
    report["waits"] = _waits(results, starts)
    report["s2"] = _s2(results, offset)
    report["skew"] = _skew(results, offset)
    report["skipped"] = len(skipped)
    report["skipped_reasons"] = _count(e.get("reason", "?").split(":")[0]
                                       for e in skipped)
    report["divergent_trails"] = _trails(results)
    s4 = [e for e in results if e.get("scenario") == "S4"]
    report["s4"] = {
        "updaters": sum(1 for e in s4 if e.get("role") == "updater"),
        "churners": len({e["id"] for e in s4 if e.get("role") == "churner"}),
        "evicted": sum(e.get("removed") or 0 for e in s4),
        "dangling_after": sum(e.get("dangling_after") or 0 for e in s4),
    }
    report["clock_offsets"] = {i: round(o, 3) for i, o in offset.items()}

    _print_report(report, starts)
    if cfg.json:
        with open(cfg.json, "w") as f:
            json.dump(report, f, indent=2, sort_keys=True, default=str)
        print("\nJSON report: %s" % cfg.json)
    return 1 if any(violations.values()) else 0


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def _host_key(starts, e):
    s = starts.get(e.get("id"), {})
    m = s.get("mount") or {}
    return "%s (%s, %s %s)" % (e.get("host"), s.get("kernel", "?"),
                              m.get("fstype", "?"), m.get("mount_point", "?"))


def _outcomes(results, starts):
    out = {}
    for e in results:
        k = "%s %s" % (e.get("scenario"), _host_key(starts, e))
        out.setdefault(k, {})
        o = e.get("outcome", "?")
        out[k][o] = out[k].get(o, 0) + 1
    return out


def _waits(results, starts):
    out = {}
    for e in results:
        if e.get("waited_s") is None:
            continue
        k = _host_key(starts, e)
        out.setdefault(k, {"waits": [], "budget": None})
        out[k]["waits"].append(e["waited_s"])
        out[k]["budget"] = (e.get("budget") or {}).get("seconds")
    return {k: {"n": len(v["waits"]), "p50": pct(v["waits"], 50),
                "p95": pct(v["waits"], 95), "max": max(v["waits"]),
                "budget": v["budget"]} for k, v in out.items()}


def _s2(results, offset):
    pub = {}
    for e in results:
        if e.get("scenario") == "S2" and e.get("role") == "writer":
            t = e.get("publish_time") or e.get("t_return")
            if t:
                pub[e["round"]] = t + offset.get(e["id"], 0.0)
    per = {}
    for e in results:
        if e.get("scenario") != "S2" or e.get("role") != "reader":
            continue
        m = per.setdefault(e["method"], {"n": 0, "visible": [], "not_visible": 0,
                                         "over_budget": 0})
        m["n"] += 1
        b = (e.get("budget") or {}).get("seconds")
        if e.get("visible_at") is None:
            m["not_visible"] += 1
            continue
        t0 = pub.get(e["round"])
        if t0 is None:
            continue
        ttv = max(0.0, e["visible_at"] + offset.get(e["id"], 0.0) - t0)
        m["visible"].append(ttv)
        if b is not None and ttv > b:
            m["over_budget"] += 1
    return {k: {"n": v["n"], "visible": len(v["visible"]),
                "not_visible_within_limit": v["not_visible"],
                "over_budget": v["over_budget"],
                "p50": pct(v["visible"], 50), "p95": pct(v["visible"], 95),
                "max": max(v["visible"]) if v["visible"] else None}
            for k, v in sorted(per.items())}


def _skew(results, offset):
    """Spread of actual start times (server clock) per S1 round."""
    by_round = {}
    for e in results:
        if e.get("scenario") == "S1" and e.get("t_call"):
            by_round.setdefault(e["round"], []).append(
                e["t_call"] + offset.get(e["id"], 0.0))
    spreads = [max(v) - min(v) for v in by_round.values() if len(v) > 1]
    return {"rounds": len(spreads), "p50": pct(spreads, 50),
            "max": max(spreads) if spreads else None}


def _trails(results):
    pats = {}
    for e in results:
        if not str(e.get("outcome", "")).startswith("divergent"):
            continue
        seq = []
        for o in e.get("observations") or []:
            r = o.get("result")
            if seq and seq[-1][0] == r:
                seq[-1][1] += 1
            else:
                seq.append([r, 1])
        pat = " ".join("%s×%d" % (r, n) if n > 1 else str(r) for r, n in seq)
        key = "%s: %s" % (e["outcome"], pat)
        pats[key] = pats.get(key, 0) + 1
    return dict(sorted(pats.items(), key=lambda kv: -kv[1])[:10])


def _fmt(v):
    return "-" if v is None else ("%.2f" % v if isinstance(v, float) else str(v))


def _print_report(r, starts):
    print("Run: %s" % r["run_dir"])
    print("Participants: %d on %d host(s): %s" % (
        r["participants"], len(r["hosts"]), ", ".join(r["hosts"])))
    if r["unfinished"]:
        print("  did not finish: %s" % ", ".join(r["unfinished"]))
    print("Skipped slots: %d %s" % (r["skipped"], r["skipped_reasons"] or ""))
    sk = r["skew"]
    print("S1 start skew (server clock): p50 %s s, max %s s over %d round(s)"
          % (_fmt(sk["p50"]), _fmt(sk["max"]), sk["rounds"]))
    print("\nOutcomes")
    for k, v in sorted(r["outcomes"].items()):
        print("  %-60s %s" % (k, ", ".join("%s=%d" % kv for kv in sorted(v.items()))))
    if r["waits"]:
        print("\nLost-race waits (s)")
        for k, v in sorted(r["waits"].items()):
            print("  %-50s n=%d p50=%s p95=%s max=%s budget=%s" % (
                k, v["n"], _fmt(v["p50"]), _fmt(v["p95"]), _fmt(v["max"]),
                _fmt(v["budget"])))
    if r["s2"]:
        print("\nS2 time-to-visible by refresh method (s, server clock)")
        print("  %-18s %5s %7s %8s %7s %7s %7s %7s" % (
            "method", "n", "visible", "missing", ">budget", "p50", "p95", "max"))
        for k, v in r["s2"].items():
            print("  %-18s %5d %7d %8d %7d %7s %7s %7s" % (
                k, v["n"], v["visible"], v["not_visible_within_limit"],
                v["over_budget"], _fmt(v["p50"]), _fmt(v["p95"]), _fmt(v["max"])))
    if r["s4"]["updaters"]:
        c = r["s4"]
        print("\nS4 churn: %d update(s), %d churner(s) evicted %d entr(ies); "
              "%d link(s) dangling after a later eviction (expected)"
              % (c["updaters"], c["churners"], c["evicted"], c["dangling_after"]))
        if not c["churners"]:
            print("  (no churners: needs at least --churn-every participants)")
    if r["divergent_trails"]:
        print("\nDivergent probe trails (top)")
        for k, n in r["divergent_trails"].items():
            print("  %4d  %s" % (n, k[:150]))
    if "verify" in r:
        print("\nFinal cache verify: %s" % r["verify"])
    print("\nInvariants")
    names = {
        "I1": "exactly one winner per S1 race",
        "I2": "no participant errors",
        "I3": "every linked/returned tree has the expected content",
        "I4": "no half-published entry observed",
        "I5": "no staging/tombstone residue",
        "I6": "every divergent copy has a matching record",
        "I7": "cache verify (content): no blocking findings",
        "I8": "observed adopt wait within the budget",
        "I9": "no partially removed tree through a deps link (S4)",
    }
    for k in sorted(names):
        v = r["violations"][k]
        print("  %s %-55s %s" % (k, names[k], "ok" if not v else "FAIL (%d)" % len(v)))
        for item in v[:5]:
            print("       %s" % json.dumps(item, default=str)[:300])


# ==========================================================================
# clean
# ==========================================================================

def cmd_clean(cfg):
    rd = run_dir(cfg.root, cfg.run_id)
    if not os.path.isfile(os.path.join(rd, SENTINEL)):
        sys.exit("refusing to remove %s: no %s sentinel (not a stress run)"
                 % (rd, SENTINEL))
    make_writable_and_remove(rd)
    print("removed %s" % rd)
    return 0


# ==========================================================================
# CLI
# ==========================================================================

def _add_common(p):
    p.add_argument("--root", required=True,
                   help="shared directory holding stress runs (on the mount under test)")
    p.add_argument("--run-id", required=True)
    p.add_argument("--ivpm-src", default=REPO_SRC,
                   help="ivpm source tree to test (default: this checkout's src/)")


def _add_run(p):
    p.add_argument("--scenario", default="S1,S2",
                   help="comma-separated subset of S1..S5 (run in this order)")
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--workers", type=int, default=1, help="processes per host")
    p.add_argument("--files", type=int, default=10,
                   help="files per S1 tree (S2/S5 use a tenth)")
    p.add_argument("--file-size", type=int, default=256)
    p.add_argument("--lead", type=float, default=10,
                   help="seconds from slot start to the race (tree build + priming)")
    p.add_argument("--prime", type=float, default=5,
                   help="seconds of negative-lookup priming before the race")
    p.add_argument("--s1-cold", action="store_true",
                   help="alternate primed and cold (unprimed) S1 rounds")
    p.add_argument("--s1-period", type=float, default=90,
                   help="S1 slot length; must cover lead + the adopt budget")
    p.add_argument("--s2-period", type=float, default=145)
    p.add_argument("--poll-limit", type=float, default=130,
                   help="S2 readers give up after this long (2 x acdirmax + 10)")
    p.add_argument("--poll-interval", type=float, default=0.05)
    p.add_argument("--s3-period", type=float, default=180,
                   help="S3/S4 slot length")
    p.add_argument("--deps", type=int, default=20, help="S3/S4 dependencies")
    p.add_argument("--dep-files", type=int, default=20)
    p.add_argument("--churn-every", type=int, default=4,
                   help="S4: every Nth participant churns instead of updating")
    p.add_argument("--churn-sleep", type=float, default=0.5)
    p.add_argument("--s5-period", type=float, default=60)
    p.add_argument("--s5-entries", type=int, default=8)
    p.add_argument("--verify-level", default="content",
                   help="IVPM_CACHE_VERIFY for participants (content records merkles)")
    p.add_argument("--adopt-wait", type=float, default=None,
                   help="force IVPM_CACHE_ADOPT_WAIT (default: ivpm derives it)")
    p.add_argument("--local-scratch", default=tempfile.gettempdir(),
                   help="host-local scratch for S3 git remotes")
    p.add_argument("--keep-local", action="store_true")
    p.add_argument("--verbose", action="store_true",
                   help="show IVPM's notes from every participant")
    p.add_argument("--dry-schedule", action="store_true",
                   help="print the slot schedule and exit")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="cache_stress.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run this host's participants")
    _add_common(pr)
    _add_run(pr)
    pr.add_argument("--host-index", type=int, default=None)
    pr.add_argument("--hosts", type=int, default=None, help="total host count")
    pr.add_argument("--host-list", default=None,
                    help="comma-separated hosts; this host's index is its position")
    pr.add_argument("--start-at", type=float, default=None,
                    help="epoch seconds; identical on every host")

    pl = sub.add_parser("launch", help="ssh fan-out of 'run' to every host",
                        description="Everything after '--' is passed to 'run' "
                        "on every host (it must include --root and --run-id).")
    pl.add_argument("--hosts", dest="hosts_csv", required=True)
    pl.add_argument("--delay", type=float, default=60,
                    help="seconds from now to the shared start time")
    pl.add_argument("--ssh", default="ssh -o BatchMode=yes")
    pl.add_argument("--python", default="python3")
    pl.add_argument("--cwd", default=os.getcwd())

    pa = sub.add_parser("analyze", help="check invariants and report")
    _add_common(pa)
    pa.add_argument("--json", default=None, help="also write the report here")
    pa.add_argument("--no-verify", action="store_true",
                    help="skip the final 'cache verify --content' (I7)")

    pc = sub.add_parser("clean", help="remove a run")
    _add_common(pc)

    run_args = []
    if argv and argv[0] == "launch" and "--" in argv:
        i = argv.index("--")
        argv, run_args = argv[:i], argv[i + 1:]
    cfg = ap.parse_args(argv)

    if cfg.cmd == "launch":
        if not run_args:
            ap.error("launch needs '-- <run arguments>'")
        return cmd_launch(cfg, run_args)
    if cfg.cmd == "run":
        cfg.scenarios = [s.strip().upper() for s in cfg.scenario.split(",") if s.strip()]
        bad = [s for s in cfg.scenarios if s not in SCENARIOS]
        if bad:
            ap.error("unknown scenario(s): %s" % ", ".join(bad))
        cfg.ivpm_src = os.path.abspath(cfg.ivpm_src)
        cfg.args_echo = argv
        return cmd_run(cfg)
    if cfg.cmd == "analyze":
        return cmd_analyze(cfg)
    return cmd_clean(cfg)


if __name__ == "__main__":
    sys.exit(main())
