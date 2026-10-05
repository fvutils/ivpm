"""Lost-race adoption: wait for the winner, else publish a divergent copy.

Companion to cache-lost-race-design.md, Part A.  The scenario throughout: our
publish rename fails ENOTEMPTY (another process won), and this host may or may
not be able to see the winner's entry yet.
"""
import errno
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOTDIR, 'src'))

from ivpm import cache_adopt as ca
from ivpm import cache_verify as cv
from ivpm.cache import DirectoryCacheStore
from ivpm.cache_provider import CacheContext, DirectoryCacheProvider
from ivpm.diagnostics import CollectingSink, DiagnosticReporter, Severity
from ivpm.msg import set_reporter
from ivpm.utils import safe_version_key


class _Pkg:
    def __init__(self, name, **kw):
        self.name = name
        self.cache = True
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeSession:
    def __init__(self):
        self.divergent = []
        self.events = []
        self.perf = None
        self.event_dispatcher = self

    def dispatch(self, event):
        self.events.append(event)

    def report_cache_divergent(self, path=None):
        self.divergent.append(path)


class _Base(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.deps_dir = os.path.join(self.test_dir, "deps")
        os.makedirs(self.deps_dir)
        self.store = DirectoryCacheStore(self.cache_dir)
        self.version_dir = self.store.get_version_cache_dir("pkg", "v1")
        self.sink = CollectingSink()
        self._prev_reporter = set_reporter(DiagnosticReporter(self.sink))
        env = patch.dict(os.environ, {"IVPM_CACHE_ADOPT_WAIT": "0"})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        set_reporter(self._prev_reporter)
        for root, dirs, files in os.walk(self.test_dir):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
        try:
            os.chmod(self.test_dir, 0o755)
        except OSError:
            pass
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # --- helpers --------------------------------------------------------

    def _source(self, tag="ours", content="ours"):
        src = os.path.join(self.test_dir, "src_%s" % tag)
        os.makedirs(os.path.join(src, "sub"))
        with open(os.path.join(src, "content.txt"), "w") as f:
            f.write(content)
        with open(os.path.join(src, "sub", "more.txt"), "w") as f:
            f.write(content * 3)
        return src

    def _make_winner(self, package="pkg", version="v1"):
        """A complete, sealed-looking winner at the canonical path."""
        os.makedirs(self.version_dir)
        with open(os.path.join(self.version_dir, "content.txt"), "w") as f:
            f.write("winner")
        with open(self.store.entry_manifest_path(self.version_dir), "w") as f:
            json.dump({"schema": 1, "package": package,
                       "version": safe_version_key(version)}, f)

    def _lose_race(self, publish_winner=False):
        """Patch os.rename so the final publish fails ENOTEMPTY."""
        real_rename = os.rename
        vd = self.version_dir

        def rename(a, b):
            if b != vd:
                return real_rename(a, b)
            if publish_winner:
                self._make_winner()
            raise OSError(errno.ENOTEMPTY, "Directory not empty")
        return patch("ivpm.cache.os.rename", side_effect=rename)

    def _names(self):
        d = os.path.dirname(self.version_dir)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def _alts(self):
        return [n for n in self._names()
                if ca.ALT_MARKER in n and not n.endswith(".meta.json")]

    def _residue(self):
        return [n for n in self._names() if ".staging." in n]

    def _warnings(self):
        return [d.format(excerpt=False) for d in self.sink.records
                if d.severity is Severity.WARNING]


# --- outcomes ---------------------------------------------------------------

class TestAdoption(_Base):
    def test_winner_visible_immediately_is_adopted(self):
        src = self._source()
        with self._lose_race(publish_winner=True):
            got = self.store.store_version("pkg", "v1", src)
        self.assertEqual(got, self.version_dir)
        self.assertEqual(self._alts(), [])
        self.assertEqual(self._residue(), [])
        self.assertFalse(os.path.exists(src))
        self.assertEqual(self._warnings(), [])

    def test_winner_visible_after_wait_is_adopted(self):
        os.environ["IVPM_CACHE_ADOPT_WAIT"] = "10"
        src = self._source()
        real_probe = ca.probe_entry
        calls = []

        def probe(*a, **kw):
            calls.append(1)
            if len(calls) < 3:
                return "ENOENT"
            return real_probe(*a, **kw)

        with self._lose_race(publish_winner=True), \
                patch("ivpm.cache_adopt.probe_entry", side_effect=probe):
            got = self.store.store_version("pkg", "v1", src)
        self.assertEqual(got, self.version_dir)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self._alts(), [])
        self.assertEqual(self._residue(), [])

    def test_legacy_manifestless_winner_is_usable(self):
        src = self._source()
        real_rename = os.rename

        def rename(a, b):
            if b != self.version_dir:
                return real_rename(a, b)
            os.makedirs(b)
            with open(os.path.join(b, "old.txt"), "w") as f:
                f.write("from an older ivpm")
            raise OSError(errno.ENOTEMPTY, "Directory not empty")

        with patch("ivpm.cache.os.rename", side_effect=rename):
            got = self.store.store_version("pkg", "v1", src)
        self.assertEqual(got, self.version_dir)
        self.assertEqual(self._alts(), [])


class TestDivergent(_Base):
    def test_never_visible_publishes_divergent_copy_with_record(self):
        src = self._source()
        with self._lose_race():
            got = self.store.store_version(
                "pkg", "v1", src, context={"workspace": "/ws"})

        self.assertNotEqual(got, self.version_dir)
        self.assertTrue(os.path.basename(got).startswith("v1" + ca.ALT_MARKER))
        self.assertEqual(self._alts(), [os.path.basename(got)])
        self.assertEqual(self._residue(), [])
        self.assertFalse(os.path.exists(src))
        with open(os.path.join(got, "content.txt")) as f:
            self.assertEqual(f.read(), "ours")

        rec = self.store.read_divergence_record(got)
        self.assertEqual(rec["reason"], ca.DIVERGENT_NOT_VISIBLE)
        self.assertEqual(rec["package"], "pkg")
        self.assertEqual(rec["version"], "v1")
        self.assertEqual(rec["canonical"], self.version_dir)
        self.assertEqual(rec["path"], got)
        self.assertEqual(rec["rename_errno"], "ENOTEMPTY")
        self.assertEqual(rec["workspace"], "/ws")
        self.assertEqual(rec["budget"]["seconds"], 0.0)
        self.assertTrue(rec["observations"])
        self.assertEqual(rec["observations"][0]["result"], "ENOENT")
        for k in ("host", "pid", "ivpm", "kernel", "created"):
            self.assertIn(k, rec)

        # Still sealed, with its own sidecar so it ages like any entry.
        self.assertFalse(os.stat(got).st_mode & 0o222)
        self.assertTrue(os.path.isfile(got + DirectoryCacheStore._META_SUFFIX))
        # The canonical key is untouched: later runs still look there.
        self.assertFalse(self.store.has_version("pkg", "v1"))

        warnings = self._warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn(got, warnings[0])
        self.assertIn(ca.DIVERGENCE_RECORD, warnings[0])

    def test_unreadable_winner_stops_immediately(self):
        os.environ["IVPM_CACHE_ADOPT_WAIT"] = "60"
        src = self._source()
        with self._lose_race(), \
                patch("ivpm.cache_adopt.probe_entry", return_value="EACCES"):
            t0 = time.monotonic()
            got = self.store.store_version("pkg", "v1", src)
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 5)
        rec = self.store.read_divergence_record(got)
        self.assertEqual(rec["reason"], ca.DIVERGENT_UNREADABLE)
        self.assertEqual(len(rec["observations"]), 1)
        self.assertIn("permission", self._warnings()[0])

    def test_ctrl_c_mid_wait_leaves_nothing_behind(self):
        os.environ["IVPM_CACHE_ADOPT_WAIT"] = "60"
        src = self._source()

        def interrupt(_):
            raise KeyboardInterrupt()

        with self._lose_race(), \
                patch("ivpm.cache_adopt.time.sleep", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.store_version("pkg", "v1", src)
        self.assertEqual(self._alts(), [])
        self.assertEqual(self._residue(), [])
        self.assertFalse(os.path.exists(src))

    def test_failed_divergent_rename_falls_back_to_uncached_copy(self):
        src = self._source()
        real_rename = os.rename

        def rename(a, b):
            if b == self.version_dir:
                raise OSError(errno.ENOTEMPTY, "Directory not empty")
            if ca.ALT_MARKER in b:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_rename(a, b)

        ctx = CacheContext(root_name=None, root_version=None,
                           root_dir=self.test_dir, deps_dir=self.deps_dir)
        prov = DirectoryCacheProvider(ctx, self.store)
        pkg = _Pkg("pkg")
        with patch("ivpm.cache.os.rename", side_effect=rename):
            tree = prov.store(pkg, "v1", src)
        self.assertIn(".staging.", os.path.basename(tree))
        link = prov.materialize(pkg, "v1")
        self.assertFalse(os.path.islink(link))
        with open(os.path.join(link, "content.txt")) as f:
            self.assertEqual(f.read(), "ours")
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(self._residue(), [])
        # Writable, so a later run can replace it with a link.
        self.assertTrue(os.stat(link).st_mode & 0o200)


class TestProvider(_Base):
    def _provider(self, session=None):
        ctx = CacheContext(root_name=None, root_version=None,
                           root_dir=self.test_dir, deps_dir=self.deps_dir)
        prov = DirectoryCacheProvider(ctx, self.store)
        prov.session = session
        return prov

    def test_materialize_links_the_divergent_copy(self):
        session = _FakeSession()
        prov = self._provider(session)
        pkg = _Pkg("pkg")
        with self._lose_race():
            got = prov.store(pkg, "v1", self._source())
        link = prov.materialize(pkg, "v1")
        self.assertEqual(os.readlink(link), got)
        self.assertEqual(session.divergent, [got])

        # A later run that finds the canonical entry links there instead.
        self._make_winner()
        prov2 = self._provider()
        self.assertTrue(prov2.lookup(pkg, "v1").is_hit)
        link = prov2.materialize(pkg, "v1")
        self.assertEqual(os.readlink(link), self.version_dir)

    def test_progress_is_reported_while_waiting(self):
        os.environ["IVPM_CACHE_ADOPT_WAIT"] = "2.5"
        session = _FakeSession()
        prov = self._provider(session)
        with self._lose_race():
            prov.store(_Pkg("pkg"), "v1", self._source())
        msgs = [e.task_message for e in session.events]
        self.assertTrue(msgs)
        self.assertTrue(all(e.package_name == "pkg" for e in session.events))
        self.assertIn("waiting for cache entry", msgs[0])
        self.assertIn("/ 2s)", msgs[-1])

    def test_touch_linked_target_refreshes_divergent_sidecar(self):
        with self._lose_race():
            got = self.store.store_version("pkg", "v1", self._source())
        meta_path = got + DirectoryCacheStore._META_SUFFIX
        with open(meta_path, "w") as f:
            json.dump({"schema": 1, "stored": 1.0, "last_linked": 1.0}, f)
        self.assertTrue(self.store.touch_linked_target(got))
        with open(meta_path) as f:
            self.assertGreater(json.load(f)["last_linked"], 1.0)


# --- the wait loop itself ---------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


class _Monitor(ca.AdoptMonitor):
    def __init__(self):
        self.progress_calls = []
        self.started_with = None
        self.result = None

    def started(self, budget):
        self.started_with = budget

    def progress(self, elapsed, budget, detail):
        self.progress_calls.append(elapsed)

    def finished(self, result):
        self.result = result


class TestAwaitWinner(unittest.TestCase):
    def setUp(self):
        self.sink = CollectingSink()
        self._prev = set_reporter(DiagnosticReporter(self.sink))

    def tearDown(self):
        set_reporter(self._prev)

    def _run(self, budget, results):
        clk, mon = _Clock(), _Monitor()
        it = iter(results)
        res = ca.await_winner(
            "/nonexistent/pkg/v1", "pkg", "v1",
            ca.AdoptBudget(budget, "test"), manifest_name="m.json",
            monitor=mon, clock=clk.clock, sleep=clk.sleep,
            probe=lambda: next(it, "ENOENT"))
        return res, mon, clk

    def test_poll_schedule(self):
        self.assertEqual(list(ca.poll_times(0)), [0])
        self.assertEqual(list(ca.poll_times(7)), [0, 0.25, 0.5, 1, 2, 4, 6, 7])

    def test_adopted_after_k_probes(self):
        res, mon, clk = self._run(70, ["ENOENT", "ENOENT", ca.USABLE])
        self.assertEqual(res.outcome, ca.ADOPTED_AFTER_WAIT)
        self.assertEqual(len(res.observations), 3)
        self.assertAlmostEqual(res.waited_s, 0.5)
        self.assertIs(mon.result, res)

    def test_progress_is_monotonic_and_at_most_once_a_second(self):
        res, mon, clk = self._run(12, [])
        self.assertEqual(res.outcome, ca.DIVERGENT_NOT_VISIBLE)
        self.assertAlmostEqual(res.waited_s, 12)
        calls = mon.progress_calls
        self.assertGreaterEqual(len(calls), 10)
        self.assertEqual(calls, sorted(calls))
        self.assertTrue(all(b - a >= 1.0 - 1e-9 for a, b in zip(calls, calls[1:])))
        notes = [d.format(excerpt=False) for d in self.sink.records]
        self.assertTrue(any("waiting up to 12s" in n for n in notes))
        self.assertTrue(any("still waiting" in n for n in notes))

    def test_terminal_probe_results_stop_the_wait(self):
        for got, outcome in (("EACCES", ca.DIVERGENT_UNREADABLE),
                             ("EPERM", ca.DIVERGENT_UNREADABLE),
                             ("manifest-mismatch", ca.DIVERGENT_MISMATCH),
                             ("not-a-directory", ca.DIVERGENT_MISMATCH)):
            res, mon, clk = self._run(70, [got])
            self.assertEqual(res.outcome, outcome, got)
            self.assertEqual(clk.now, 0.0)

    def test_zero_budget_probes_once(self):
        res, mon, clk = self._run(0, [])
        self.assertEqual(res.outcome, ca.DIVERGENT_NOT_VISIBLE)
        self.assertEqual(len(res.observations), 1)
        res, _, _ = self._run(0, [ca.USABLE])
        self.assertEqual(res.outcome, ca.ADOPTED)


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.entry = os.path.join(self.d, "v1")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _probe(self, legacy_ok=True):
        return ca.probe_entry(self.entry, "pkg", "v1", "m.json", legacy_ok)

    def test_states(self):
        self.assertEqual(self._probe(), "ENOENT")
        os.makedirs(self.entry)
        self.assertEqual(self._probe(), "no-manifest")
        with open(os.path.join(self.entry, "f"), "w") as f:
            f.write("x")
        self.assertEqual(self._probe(), ca.USABLE)
        self.assertEqual(self._probe(legacy_ok=False), "no-manifest")
        with open(os.path.join(self.entry, "m.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(self._probe(), "manifest-unparseable")
        with open(os.path.join(self.entry, "m.json"), "w") as f:
            json.dump({"package": "other", "version": "v1"}, f)
        self.assertEqual(self._probe(), "manifest-mismatch")
        with open(os.path.join(self.entry, "m.json"), "w") as f:
            json.dump({"package": "pkg", "version": "v1"}, f)
        self.assertEqual(self._probe(), ca.USABLE)

    def test_symlink_is_not_an_entry(self):
        os.makedirs(os.path.join(self.d, "real"))
        os.symlink(os.path.join(self.d, "real"), self.entry)
        self.assertEqual(self._probe(), "not-a-directory")


# --- budget -----------------------------------------------------------------

_MOUNTINFO = """\
22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw
40 22 0:50 / /proj/default rw,relatime shared:2 - nfs srv:/vol1 rw,vers=3,rsize=1048576,hard,proto=tcp
41 22 0:51 / /proj/acdir rw,relatime shared:3 - nfs srv:/vol2 rw,vers=3,acdirmin=5,acdirmax=20
42 22 0:52 / /proj/actimeo rw,relatime shared:4 - nfs4 srv:/vol3 rw,vers=4.1,actimeo=3
43 22 0:53 / /proj/noac rw,relatime shared:5 - nfs srv:/vol4 rw,vers=3,noac
44 22 0:54 / /proj/lookup rw,relatime shared:6 - nfs srv:/vol5 rw,vers=3,lookupcache=positive
45 22 0:55 / /proj/lustre rw,relatime shared:7 - lustre mgs@tcp:/fs rw
46 40 0:56 / /proj/default/nested\\040dir rw shared:8 - nfs srv:/vol6 rw,acdirmax=7
"""


class TestBudget(unittest.TestCase):
    def setUp(self):
        self.mounts = ca.parse_mountinfo(_MOUNTINFO)

    def _budget(self, path):
        return ca.budget_for_mount(ca.find_mount(path, self.mounts))

    def test_table(self):
        cases = [
            ("/proj/default/cache", 70, "mount:default"),
            ("/proj/acdir/c", 30, "mount:acdirmax=20"),
            ("/proj/actimeo/c", 13, "mount:actimeo=3"),
            ("/proj/noac/c", ca.SHORT_BUDGET_S, "mount:noac"),
            ("/proj/lookup/c", ca.SHORT_BUDGET_S, "mount:lookupcache=positive"),
            ("/proj/lustre/c", ca.FALLBACK_BUDGET_S, "fallback:lustre"),
            ("/home/u/cache", ca.SHORT_BUDGET_S, "mount:local"),
            ("/proj/default/nested dir/c", 17, "mount:acdirmax=7"),
            ("/proj/defaultX/c", ca.SHORT_BUDGET_S, "mount:local"),
        ]
        for path, seconds, source in cases:
            b = self._budget(path)
            self.assertEqual((b.seconds, b.source), (seconds, source), path)

    def test_no_mount_is_fallback(self):
        self.assertEqual(ca.budget_for_mount(None).source, "fallback")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IVPM_CACHE_ADOPT_WAIT", None)
            with patch("ivpm.site_config._load_config_files", return_value=[]):
                b = ca.resolve_adopt_budget("/x", mountinfo_path="/nonexistent/mi")
        self.assertEqual((b.seconds, b.source), (ca.FALLBACK_BUDGET_S, "fallback"))

    def test_env_and_config_override(self):
        with patch.dict(os.environ, {"IVPM_CACHE_ADOPT_WAIT": "12"}):
            b = ca.resolve_adopt_budget("/x", mountinfo_path="/nonexistent/mi")
        self.assertEqual((b.seconds, b.source), (12, "env:IVPM_CACHE_ADOPT_WAIT"))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IVPM_CACHE_ADOPT_WAIT", None)
            with patch("ivpm.site_config._load_config_files",
                       return_value=[("/etc/ivpm.yaml", {"cache-adopt-wait": 33})]):
                b = ca.resolve_adopt_budget("/x", mountinfo_path="/nonexistent/mi")
        self.assertEqual((b.seconds, b.source), (33, "config:/etc/ivpm.yaml"))

    def test_bad_values_are_ignored(self):
        from ivpm.site_config import parse_cache_adopt_wait
        for bad in ("soon", "-1", "nan", "inf"):
            self.assertIsNone(parse_cache_adopt_wait(bad), bad)
        self.assertEqual(parse_cache_adopt_wait("0"), 0.0)
        self.assertEqual(parse_cache_adopt_wait(2.5), 2.5)

    def test_marker_cannot_appear_in_a_real_key(self):
        self.assertNotIn(ca.ALT_MARKER, safe_version_key("v1~alt~deadbeef"))
        self.assertNotIn("~", safe_version_key("a~b"))


# --- cache machinery recognizes divergent copies ----------------------------

class TestMachinery(_Base):
    def _divergent(self):
        with self._lose_race():
            return self.store.store_version("pkg", "v1", self._source())

    def test_verify_reports_divergent_copy_without_evicting_it(self):
        os.environ["IVPM_CACHE_VERIFY"] = "content"
        try:
            got = self._divergent()
            res = cv.verify_cache(self.store, level="content")
        finally:
            del os.environ["IVPM_CACHE_VERIFY"]
        problems = [f.problem for f in res.findings]
        self.assertEqual(problems, [cv.Problem.DIVERGENT_COPY])
        self.assertEqual(res.status, cv.Status.HEALTHY)
        f = res.findings[0]
        self.assertEqual(f.path, got)
        self.assertIn(ca.DIVERGENT_NOT_VISIBLE, f.detail)

        report = cv.repair_cache(self.store, level="content")
        self.assertTrue(os.path.isdir(got))
        self.assertEqual(report.repaired, 0)

    def test_divergent_copy_without_record(self):
        got = self._divergent()
        rec = os.path.join(got, ca.DIVERGENCE_RECORD)
        os.chmod(got, 0o755)
        os.remove(rec)
        os.chmod(got, 0o555)
        res = cv.verify_cache(self.store, level="shape")
        self.assertEqual([f.problem for f in res.findings],
                         [cv.Problem.DIVERGENT_NO_RECORD])

    def test_damaged_divergent_copy_evicts_the_copy_not_the_canonical(self):
        got = self._divergent()
        self._make_winner()
        os.chmod(os.path.join(got, "sub"), 0o755)
        os.remove(os.path.join(got, "sub", "more.txt"))
        res = cv.verify_cache(self.store, level="shape")
        self.assertIn(cv.Problem.SHAPE_MISMATCH, [f.problem for f in res.findings])
        cv.repair_cache(self.store, level="shape")
        self.assertFalse(os.path.exists(got))
        self.assertTrue(os.path.isdir(self.version_dir))

    def test_cache_info_lists_divergent_separately(self):
        got = self._divergent()
        info = self.store.get_cache_info()
        pkg = info["packages"][0]
        self.assertEqual(pkg["versions"], [])
        self.assertEqual(len(pkg["divergent"]), 1)
        d = pkg["divergent"][0]
        self.assertEqual(d["name"], os.path.basename(got))
        self.assertEqual(d["version"], "v1")
        self.assertEqual(d["reason"], ca.DIVERGENT_NOT_VISIBLE)

    def test_clean_ages_out_divergent_copies(self):
        got = self._divergent()
        self.assertEqual(self.store.clean_older_than(7), 0)
        self.assertTrue(os.path.isdir(got))

        old = time.time() - 30 * 86400
        with open(got + DirectoryCacheStore._META_SUFFIX, "w") as f:
            json.dump({"schema": 1, "stored": old, "last_linked": old}, f)
        os.utime(got, (old, old))
        self.assertEqual(self.store.clean_older_than(7, dry_run=True), 1)
        self.assertTrue(os.path.isdir(got))
        self.assertEqual(self.store.clean_older_than(7), 1)
        self.assertFalse(os.path.exists(got))
        self.assertFalse(os.path.exists(got + DirectoryCacheStore._META_SUFFIX))

    def test_clean_divergent_removes_all_regardless_of_age(self):
        got = self._divergent()
        self._make_winner()
        self.assertEqual(self.store.clean_older_than(7, divergent=True), 1)
        self.assertFalse(os.path.exists(got))
        self.assertTrue(os.path.isdir(self.version_dir))

    def test_staging_sweep_never_touches_divergent_copies(self):
        got = self._divergent()
        old = time.time() - 30 * 86400
        os.utime(got, (old, old))
        self.store._sweep_stale_staging(os.path.dirname(got))
        self.store._sweep_stale_tombs(os.path.dirname(got))
        self.assertTrue(os.path.isdir(got))


if __name__ == "__main__":
    unittest.main()
