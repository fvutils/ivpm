"""PackagePreparer: the pre-populate extension point (core, unwired).

Covers the dispatcher's contract -- ordering, `always` filtering, refusal
aggregation, and the deliberate difference from handler dispatch: a preparer
that raises unexpectedly *denies* rather than being logged and swallowed.

See pkg-prepare-design.md §4 / pkg-prepare-impl-plan.md P3.
"""
import unittest

from ivpm.load_plan import LoadAction, LoadDecision, LoadState
from ivpm.prepare import (
    PackagePreparer, PackagePreparerRgy, PrepareDenied, PrepareRequest,
    PrepareResult, PreparerList)


def _decision(action=LoadAction.FETCH, state=LoadState.ABSENT):
    return LoadDecision(action, state, "test")


def _request(name="p1", decision=None, config=None):
    class _Pkg:
        pass
    pkg = _Pkg()
    pkg.name = name
    pkg.srcinfo = None
    return PrepareRequest(
        pkg=pkg,
        decision=decision if decision is not None else _decision(),
        target_dir="/tmp/deps/%s" % name,
        deps_dir="/tmp/deps",
        scope_key=name,
        update_info=None,
        config=config or {})


class _Recorder(PackagePreparer):
    """Records calls on a shared class-level log so order can be asserted."""
    log = []

    def prepare(self, req):
        type(self).log.append(self.name)
        return PrepareResult.ok()


class TestEmptyRegistry(unittest.TestCase):

    def test_dispatch_is_a_noop(self):
        pl = PreparerList()
        self.assertEqual(len(pl), 0)
        pl.prepare(_request())          # must not raise
        pl.on_session_start(None)
        pl.on_session_end(None)

    def test_registry_ships_no_builtins(self):
        """IVPM registers no preparers of its own, so nothing changes for
        anyone who has not installed one."""
        rgy = PackagePreparerRgy()
        self.assertEqual(rgy.preparers, [])
        self.assertEqual(len(rgy.mkPreparerList()), 0)


class TestOrdering(unittest.TestCase):

    def setUp(self):
        _Recorder.log = []

    def test_order_then_name(self):
        class A(_Recorder):
            name, order = "aaa", 50

        class B(_Recorder):
            name, order = "bbb", 10

        class C(_Recorder):
            name, order = "ccc", 50

        # Registered deliberately out of order.
        pl = PreparerList([A(), C(), B()])
        pl.prepare(_request())
        self.assertEqual(_Recorder.log, ["bbb", "aaa", "ccc"])

    def test_order_is_stable_across_calls(self):
        class A(_Recorder):
            name, order = "aaa", 100

        class B(_Recorder):
            name, order = "bbb", 100

        pl = PreparerList([B(), A()])
        pl.prepare(_request())
        pl.prepare(_request("p2"))
        self.assertEqual(_Recorder.log, ["aaa", "bbb", "aaa", "bbb"])


class TestAggregation(unittest.TestCase):

    def test_all_preparers_run_even_after_a_refusal(self):
        seen = []

        class Denier(PackagePreparer):
            name, order = "denier", 10

            def prepare(self, req):
                seen.append("denier")
                return PrepareResult.deny("nope")

        class Later(PackagePreparer):
            name, order = "later", 20

            def prepare(self, req):
                seen.append("later")
                return PrepareResult.ok()

        pl = PreparerList([Denier(), Later()])
        with self.assertRaises(PrepareDenied):
            pl.prepare(_request())
        self.assertEqual(seen, ["denier", "later"],
                         "dispatch stopped at the first refusal")

    def test_multiple_refusals_are_reported_together(self):
        class D1(PackagePreparer):
            name, order = "d1", 10

            def prepare(self, req):
                return PrepareResult.deny("first problem")

        class D2(PackagePreparer):
            name, order = "d2", 20

            def prepare(self, req):
                return PrepareResult.deny("second problem", hint="try this")

        pl = PreparerList([D1(), D2()])
        with self.assertRaises(PrepareDenied) as ctx:
            pl.prepare(_request())

        self.assertEqual(len(ctx.exception.refusals), 2)
        msg = str(ctx.exception)
        self.assertIn("first problem", msg)
        self.assertIn("second problem", msg)
        self.assertIn("try this", msg)

    def test_single_refusal_names_preparer_and_package(self):
        class D(PackagePreparer):
            name = "disk-policy"

            def prepare(self, req):
                return PrepareResult.deny("not writable", hint="ask for access")

        pl = PreparerList([D()])
        with self.assertRaises(PrepareDenied) as ctx:
            pl.prepare(_request("fast-dsp"))

        msg = str(ctx.exception)
        self.assertIn("fast-dsp", msg)
        self.assertIn("disk-policy", msg)
        self.assertIn("not writable", msg)
        self.assertIn("ask for access", msg)

    def test_none_is_treated_as_ok(self):
        class Quiet(PackagePreparer):
            name = "quiet"

            def prepare(self, req):
                return None

        PreparerList([Quiet()]).prepare(_request())  # must not raise

    def test_warn_proceeds(self):
        class Warner(PackagePreparer):
            name = "warner"

            def prepare(self, req):
                return PrepareResult.warn("something is odd")

        PreparerList([Warner()]).prepare(_request())  # must not raise


class TestFailureIsDenial(unittest.TestCase):
    """A preparer that crashed did not prepare anything."""

    def test_unexpected_exception_denies(self):
        class Exploding(PackagePreparer):
            name = "exploding"

            def prepare(self, req):
                raise RuntimeError("kaboom")

        pl = PreparerList([Exploding()])
        with self.assertRaises(PrepareDenied) as ctx:
            pl.prepare(_request())
        self.assertIn("kaboom", str(ctx.exception))

    def test_a_crash_does_not_stop_the_other_preparers(self):
        seen = []

        class Exploding(PackagePreparer):
            name, order = "exploding", 10

            def prepare(self, req):
                raise RuntimeError("kaboom")

        class Later(PackagePreparer):
            name, order = "later", 20

            def prepare(self, req):
                seen.append("later")
                return PrepareResult.ok()

        with self.assertRaises(PrepareDenied):
            PreparerList([Exploding(), Later()]).prepare(_request())
        self.assertEqual(seen, ["later"])

    def test_preparer_may_raise_prepare_denied_directly(self):
        class Refuser(PackagePreparer):
            name = "refuser"

            def prepare(self, req):
                raise PrepareDenied("p1", [])

        with self.assertRaises(PrepareDenied):
            PreparerList([Refuser()]).prepare(_request())


class TestAlwaysFlag(unittest.TestCase):
    """By default a preparer sees only packages about to be populated."""

    def _seen_for(self, always, decision):
        seen = []

        class P(PackagePreparer):
            name = "p"

        P.always = always
        P.prepare = lambda self, req: seen.append(req.decision.action) or None

        PreparerList([P()]).prepare(_request(decision=decision))
        return seen

    def test_default_skips_reused_packages(self):
        reuse = _decision(LoadAction.REUSE, LoadState.RESIDENT_MATCHING)
        self.assertEqual(self._seen_for(False, reuse), [])

    def test_default_runs_for_fetch(self):
        fetch = _decision(LoadAction.FETCH, LoadState.ABSENT)
        self.assertEqual(self._seen_for(False, fetch), [LoadAction.FETCH])

    def test_default_runs_for_reconcile(self):
        rec = _decision(LoadAction.RECONCILE, LoadState.PATCHED)
        self.assertEqual(self._seen_for(False, rec), [LoadAction.RECONCILE])

    def test_always_runs_for_reused_packages(self):
        reuse = _decision(LoadAction.REUSE, LoadState.RESIDENT_MATCHING)
        self.assertEqual(self._seen_for(True, reuse), [LoadAction.REUSE])


class TestSessionHooks(unittest.TestCase):

    def test_session_start_propagates_failure(self):
        """A preparer rejecting the workspace must abort the run."""
        class P(PackagePreparer):
            name = "p"

            def on_session_start(self, update_info):
                raise PrepareDenied("<workspace>", [])

        with self.assertRaises(PrepareDenied):
            PreparerList([P()]).on_session_start(None)

    def test_session_end_failure_does_not_fail_the_update(self):
        """The workspace is already populated; teardown must not undo that."""
        class P(PackagePreparer):
            name = "p"

            def on_session_end(self, update_info):
                raise RuntimeError("teardown blew up")

        PreparerList([P()]).on_session_end(None)  # must not raise


class TestRequest(unittest.TestCase):

    def test_handler_config_reads_another_components_with_block(self):
        class _UI:
            handler_configs = {"python": {"venv": "uv"}}

        req = _request()
        req.update_info = _UI()
        self.assertEqual(req.handler_config("python"), {"venv": "uv"})
        self.assertEqual(req.handler_config("absent"), {})

    def test_config_is_the_preparers_own_block(self):
        req = _request(config={"default-group": "eng"})
        self.assertEqual(req.config, {"default-group": "eng"})


class TestRegistry(unittest.TestCase):

    def test_provenance_is_recorded(self):
        class P(PackagePreparer):
            name = "p"
            description = "a preparer"

        rgy = PackagePreparerRgy()
        rgy.addPreparer(P, origin="acme.ext", version="1.2",
                        provider="acme-ivpm")
        infos = rgy.preparer_infos()
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].name, "p")
        self.assertEqual(infos[0].origin, "acme.ext")
        self.assertEqual(infos[0].version, "1.2")
        self.assertEqual(infos[0].provider, "acme-ivpm")

    def test_mkPreparerList_instantiates_each(self):
        class P(PackagePreparer):
            name = "p"

        rgy = PackagePreparerRgy()
        rgy.addPreparer(P)
        pl = rgy.mkPreparerList()
        self.assertEqual(len(pl), 1)
        self.assertIsInstance(pl.preparers[0], P)

    def test_entry_point_load_failure_is_fatal(self):
        """Unlike a handler, a preparer that fails to load aborts the command."""
        from ivpm.prepare.pkg_preparer_rgy import PreparerLoadError

        class _BadEP:
            name = "bad"
            value = "nonexistent.module:Nope"

            def load(self):
                raise ImportError("no such module")

        rgy = PackagePreparerRgy()

        def _fake_entry_points(group=None):
            return [_BadEP()]

        # _load_plugins imports entry_points inside the function, so patching
        # the module attribute here takes effect for the call below.
        import importlib.metadata as md
        saved = md.entry_points
        md.entry_points = _fake_entry_points
        try:
            with self.assertRaises(PreparerLoadError) as ctx:
                rgy._load_plugins()
        finally:
            md.entry_points = saved

        self.assertIn("bad", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
