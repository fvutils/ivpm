"""
Unit tests for ivpm.handlers.handler_order.resolve_order.

Pure, offline tests: handlers are tiny PackageHandler subclasses constructed
inline. No network, no real packages. Covers named-phase ordering, relative
run_after/run_before constraints (handler- and phase-targets), the phase
barrier guarantee, deterministic tie-break, legacy-int band mapping, dangling
references (warn + drop), and cycle / bad-phase errors.
"""
import itertools
import logging
import unittest

from ivpm.handlers.package_handler import PackageHandler
from ivpm.handlers.handler_phases import HandlerPhase
from ivpm.handlers.handler_order import resolve_order, normalize_phase, HandlerOrderError


def mkhandler(handler_name, phase=HandlerPhase.INTEGRATE, run_after=None, run_before=None):
    """Build a PackageHandler subclass with the given ordering attributes."""
    return type(
        "H_" + handler_name.replace("-", "_"),
        (PackageHandler,),
        {
            "name": handler_name,
            "phase": phase,
            "run_after": list(run_after or []),
            "run_before": list(run_before or []),
        },
    )


def order_names(handlers):
    return [type(h).name for h in resolve_order(handlers)]


class TestNormalizePhase(unittest.TestCase):

    def test_named_phase_passthrough(self):
        self.assertEqual(normalize_phase(HandlerPhase.INSTALL), "install")

    def test_unknown_phase_name_raises(self):
        with self.assertRaises(HandlerOrderError):
            normalize_phase("bogus")

    def test_bool_rejected(self):
        with self.assertRaises(HandlerOrderError):
            normalize_phase(True)

    def test_legacy_int_bands(self):
        # Boundaries: <2 -> environment, 2..5 -> install, 6..9 -> integrate, >=10 -> finalize
        cases = {
            -1: HandlerPhase.ENVIRONMENT,
            0:  HandlerPhase.ENVIRONMENT,
            1:  HandlerPhase.ENVIRONMENT,
            2:  HandlerPhase.INSTALL,
            5:  HandlerPhase.INSTALL,
            6:  HandlerPhase.INTEGRATE,
            9:  HandlerPhase.INTEGRATE,
            10: HandlerPhase.FINALIZE,
            99: HandlerPhase.FINALIZE,
        }
        for n, expected in cases.items():
            self.assertEqual(normalize_phase(n), expected, "int %d" % n)


class TestResolveOrder(unittest.TestCase):

    def test_phase_ordering(self):
        a = mkhandler("a", phase=HandlerPhase.INTEGRATE)
        b = mkhandler("b", phase=HandlerPhase.ENVIRONMENT)
        c = mkhandler("c", phase=HandlerPhase.INSTALL)
        # Regardless of input order, phases come out environment -> install -> integrate.
        for perm in itertools.permutations([a(), b(), c()]):
            self.assertEqual(order_names(list(perm)), ["b", "c", "a"])

    def test_intra_phase_tiebreak_by_name(self):
        # Same phase, no constraints -> sorted by name, stable across input order.
        z = mkhandler("zebra", phase=HandlerPhase.INSTALL)
        a = mkhandler("alpha", phase=HandlerPhase.INSTALL)
        m = mkhandler("mango", phase=HandlerPhase.INSTALL)
        for perm in itertools.permutations([z(), a(), m()]):
            self.assertEqual(order_names(list(perm)), ["alpha", "mango", "zebra"])

    def test_run_after_handler_target(self):
        # b runs after a even though b sorts first by name and shares the phase.
        a = mkhandler("aaa", phase=HandlerPhase.INSTALL, run_after=[])
        b = mkhandler("bbb", phase=HandlerPhase.INSTALL, run_after=["zzz"])
        z = mkhandler("zzz", phase=HandlerPhase.INSTALL)
        self.assertEqual(order_names([a(), b(), z()]), ["aaa", "zzz", "bbb"])

    def test_run_before_handler_target(self):
        # z must run before a, overriding the by-name tie-break.
        a = mkhandler("aaa", phase=HandlerPhase.INSTALL)
        z = mkhandler("zzz", phase=HandlerPhase.INSTALL, run_before=["aaa"])
        self.assertEqual(order_names([a(), z()]), ["zzz", "aaa"])

    def test_run_after_phase_target(self):
        # An integrate handler asking to run after all install handlers.
        p1 = mkhandler("p1", phase=HandlerPhase.INSTALL)
        p2 = mkhandler("p2", phase=HandlerPhase.INSTALL)
        consumer = mkhandler("consumer", phase=HandlerPhase.INTEGRATE,
                             run_after=["phase:install"])
        names = order_names([consumer(), p1(), p2()])
        self.assertLess(names.index("p1"), names.index("consumer"))
        self.assertLess(names.index("p2"), names.index("consumer"))

    def test_run_before_phase_target(self):
        # An environment handler that must finish before any install handler.
        early = mkhandler("early", phase=HandlerPhase.ENVIRONMENT,
                          run_before=["phase:install"])
        inst = mkhandler("inst", phase=HandlerPhase.INSTALL)
        names = order_names([inst(), early()])
        self.assertLess(names.index("early"), names.index("inst"))

    def test_barrier_guarantee(self):
        # Every INSTALL handler must precede every INTEGRATE handler, for any input order.
        installs = [mkhandler(n, phase=HandlerPhase.INSTALL) for n in ("i1", "i2", "i3")]
        integrates = [mkhandler(n, phase=HandlerPhase.INTEGRATE) for n in ("g1", "g2")]
        handlers = [h() for h in installs + integrates]
        for perm in itertools.permutations(handlers):
            names = order_names(list(perm))
            last_install = max(names.index(n) for n in ("i1", "i2", "i3"))
            first_integrate = min(names.index(n) for n in ("g1", "g2"))
            self.assertLess(last_install, first_integrate)

    def test_dangling_reference_warns_and_drops(self):
        a = mkhandler("aaa", phase=HandlerPhase.INSTALL, run_after=["does-not-exist"])
        with self.assertLogs("ivpm.handlers.handler_order", level=logging.WARNING) as cm:
            names = order_names([a()])
        self.assertEqual(names, ["aaa"])
        self.assertTrue(any("does-not-exist" in m for m in cm.output))

    def test_cycle_raises(self):
        a = mkhandler("aaa", phase=HandlerPhase.INSTALL, run_after=["bbb"])
        b = mkhandler("bbb", phase=HandlerPhase.INSTALL, run_after=["aaa"])
        with self.assertRaises(HandlerOrderError) as ctx:
            resolve_order([a(), b()])
        msg = str(ctx.exception)
        self.assertIn("aaa", msg)
        self.assertIn("bbb", msg)

    def test_cross_phase_backward_constraint_raises(self):
        # An environment handler asking to run after an integrate handler conflicts
        # with the phase chain -> cycle.
        env = mkhandler("env", phase=HandlerPhase.ENVIRONMENT, run_after=["late"])
        late = mkhandler("late", phase=HandlerPhase.INTEGRATE)
        with self.assertRaises(HandlerOrderError):
            resolve_order([env(), late()])

    def test_unknown_phase_name_raises(self):
        bad = mkhandler("bad", phase="not-a-phase")
        with self.assertRaises(HandlerOrderError):
            resolve_order([bad()])

    def test_bad_phase_ref_in_constraint_raises(self):
        bad = mkhandler("bad", phase=HandlerPhase.INSTALL, run_after=["phase:nope"])
        with self.assertRaises(HandlerOrderError):
            resolve_order([bad()])

    def test_legacy_int_reproduces_order(self):
        # Mirror the historical built-in numbers and assert the pre-migration order.
        direnv  = mkhandler("direnv",  phase=0)
        dvflow  = mkhandler("dv-flow", phase=0)
        modules = mkhandler("modules", phase=1)
        python  = mkhandler("python",  phase=5)
        agents  = mkhandler("agents",  phase=6)
        node    = mkhandler("node",    phase=6)
        fusesoc = mkhandler("fusesoc", phase=10)
        handlers = [h() for h in (fusesoc, node, agents, python, modules, dvflow, direnv)]
        names = order_names(handlers)
        # environment band (0,0,1): direnv, dv-flow, modules
        self.assertEqual(names[:3], ["direnv", "dv-flow", "modules"])
        # install band (5): python
        self.assertEqual(names[3], "python")
        # integrate band (6,6): agents, node  (by-name tie-break)
        self.assertEqual(names[4:6], ["agents", "node"])
        # finalize band (10): fusesoc
        self.assertEqual(names[6], "fusesoc")


class TestBuiltinHandlerOrder(unittest.TestCase):
    """Regression guard on the resolved order of the real built-in handlers."""

    def _builtin_classes(self):
        from ivpm.handlers.package_handler_direnv import PackageHandlerDirenv
        from ivpm.handlers.package_handler_modules import PackageHandlerModules
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        from ivpm.handlers.package_handler_node import PackageHandlerNode
        from ivpm.handlers.package_handler_agents import PackageHandlerAgents
        from ivpm.handlers.package_handler_fusesoc import PackageHandlerFuseSoC
        from ivpm.handlers.package_handler_dv_flow import PackageHandlerDvFlow
        return [
            PackageHandlerDirenv, PackageHandlerModules, PackageHandlerPython,
            PackageHandlerNode, PackageHandlerAgents, PackageHandlerFuseSoC,
            PackageHandlerDvFlow,
        ]

    def test_builtin_resolved_order(self):
        classes = self._builtin_classes()
        # Try several input orderings; the resolved order must be invariant.
        import random
        rng = random.Random(1234)
        for _ in range(5):
            insts = [c() for c in classes]
            rng.shuffle(insts)
            names = order_names(insts)
            self.assertEqual(
                names,
                ["direnv", "modules", "python", "node", "agents", "dv-flow", "fusesoc"])

    def test_modules_after_direnv(self):
        classes = self._builtin_classes()
        names = order_names([c() for c in classes])
        self.assertLess(names.index("direnv"), names.index("modules"))

    def test_node_after_python(self):
        """A declared constraint, not the alphabetical tie-break it looks like.

        `npm install` runs a linked source package's `prepare` script, which is
        arbitrary build code -- and a TypeScript package generated by a Python
        tool is an ordinary shape, so `prepare` may invoke something from the
        venv. Both handlers sit in INSTALL, where ties break by name, so "node"
        used to sort first and such a build failed against a venv that did not
        exist yet. Asserted separately from the full order above because the
        reason is specific and the full-order assertion would not say it.
        """
        classes = self._builtin_classes()
        names = order_names([c() for c in classes])
        self.assertLess(names.index("python"), names.index("node"))


if __name__ == "__main__":
    unittest.main()
