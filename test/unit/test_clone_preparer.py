"""Clone preparers: result types, two-stage dispatch, and rollback.

Exercises ClonePreparerList directly (no CmdClone); see
test_clone_preparers_cmd.py for the `ivpm clone` integration.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.clone.clone_preparer import (
    ClonePreparer, ClonePrepareRequest, ClonePrepareResult,
    ClonePrepareOutcome, ClonePrepareRefusal, CloneDenied)
from ivpm.clone.clone_preparer_list import (
    ClonePreparerList, PathSnapshot, NullSnapshot)
from ivpm.yamlsrc import SrcLoaderError


def _req(target_dir, **kw):
    fields = dict(
        src="https://example.com/o/r.git", url="https://example.com/o/r.git",
        provider="git", target_dir=target_dir,
        target_nonempty=(os.path.isdir(target_dir)
                         and bool(os.listdir(target_dir))),
        branch=None, tag=None, revision=None, dep_set=None,
        cli_overrides={}, args=None)
    fields.update(kw)
    return ClonePrepareRequest(**fields)


def _mk(name, log, order=100, check=None, prepare=None, applies=True):
    """Build a ClonePreparer subclass that logs '<name>.<stage>' calls.

    *check* / *prepare* are callables ``(req) -> result`` (or raise); None
    keeps the base-class default (OK)."""
    attrs = {"name": name, "order": order}

    def _applies(self, req):
        if callable(applies):
            return applies(req)
        return applies
    attrs["applies"] = _applies

    if check is not None:
        def _check(self, req):
            log.append("%s.check" % name)
            return check(req)
        attrs["check"] = _check
    if prepare is not None:
        def _prepare(self, req):
            log.append("%s.prepare" % name)
            return prepare(req)
        attrs["prepare"] = _prepare
    return type("P_%s" % name, (ClonePreparer,), attrs)


def _ok(req):
    return ClonePrepareResult.ok()


def _deny(msg, hint=""):
    return lambda req: ClonePrepareResult.deny(msg, hint)


def _raise(exc):
    def _f(req):
        raise exc
    return _f


class TestResultTypes(TestBase):

    def test_result_constructors(self):
        self.assertIs(ClonePrepareResult.ok().outcome, ClonePrepareOutcome.OK)
        w = ClonePrepareResult.warn("m", "h")
        self.assertIs(w.outcome, ClonePrepareOutcome.WARN)
        self.assertEqual(("m", "h"), (w.message, w.hint))
        d = ClonePrepareResult.deny("m")
        self.assertTrue(d.denied)
        self.assertFalse(w.denied)

    def test_denied_format_single(self):
        e = CloneDenied("u", [ClonePrepareRefusal(
            "er", "check", ClonePrepareResult.deny("not ER", "use /proj/er"))])
        text = e.format()
        self.assertIn("refused by the 'er' clone preparer", text)
        self.assertIn("not ER", text)
        self.assertIn("Hint: use /proj/er", text)

    def test_denied_format_multi(self):
        e = CloneDenied("u", [
            ClonePrepareRefusal("a", "check", ClonePrepareResult.deny("ma", "ha")),
            ClonePrepareRefusal("b", "check", ClonePrepareResult.deny("mb"))])
        text = e.format()
        self.assertIn("refused by 2 clone preparers", text)
        self.assertIn("[a] ma", text)
        self.assertIn("Hint: ha", text)
        self.assertIn("[b] mb", text)

    def test_stages_and_info(self):
        log = []
        Both = _mk("both", log, check=_ok, prepare=_ok)
        CheckOnly = _mk("c", log, check=_ok)
        PrepOnly = _mk("p", log, prepare=_ok)
        self.assertEqual(["check", "prepare"], Both.stages())
        self.assertEqual(["check"], CheckOnly.stages())
        self.assertEqual(["prepare"], PrepOnly.stages())
        info = Both.preparer_info()
        self.assertEqual("both", info.name)
        self.assertEqual(["check", "prepare"], info.stages)


class TestDispatch(TestBase):

    def setUp(self):
        super().setUp()
        self.target = os.path.join(self.testdir, "ws")
        self.log = []

    def _run(self, *classes):
        return ClonePreparerList([c() for c in classes]).run(_req(self.target))

    def test_empty_list_is_noop(self):
        snap = ClonePreparerList([]).run(_req(self.target))
        self.assertIsInstance(snap, NullSnapshot)

    def test_order_respected(self):
        self._run(_mk("z", self.log, order=50, check=_ok, prepare=_ok),
                  _mk("b", self.log, order=100, check=_ok, prepare=_ok),
                  _mk("a", self.log, order=100, check=_ok, prepare=_ok))
        self.assertEqual(
            ["z.check", "a.check", "b.check",
             "z.prepare", "a.prepare", "b.prepare"], self.log)

    def test_all_checks_before_any_prepare(self):
        self._run(_mk("a", self.log, check=_ok, prepare=_ok),
                  _mk("b", self.log, check=_ok, prepare=_ok))
        self.assertEqual(["a.check", "b.check", "a.prepare", "b.prepare"],
                         self.log)

    def test_check_deny_skips_all_prepare(self):
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, check=_ok, prepare=_ok),
                      _mk("b", self.log, check=_deny("no"), prepare=_ok))
        self.assertEqual(["a.check", "b.check"], self.log)
        self.assertEqual("check", ctx.exception.refusals[0].stage)

    def test_all_check_denies_collected(self):
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, check=_deny("ma")),
                      _mk("b", self.log, check=_deny("mb")),
                      _mk("c", self.log, check=_deny("mc")))
        self.assertEqual(["a", "b", "c"],
                         [r.preparer for r in ctx.exception.refusals])

    def test_prepare_deny_stops_and_rolls_back(self):
        def _mkdir(req):
            os.makedirs(req.target_dir)
            return ClonePrepareResult.ok()
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, order=1, prepare=_mkdir),
                      _mk("b", self.log, order=2, prepare=_deny("bad disk")),
                      _mk("c", self.log, order=3, prepare=_ok))
        self.assertEqual(["a.prepare", "b.prepare"], self.log)
        self.assertEqual("prepare", ctx.exception.refusals[0].stage)
        self.assertFalse(os.path.exists(self.target))

    def test_prepare_raises_rolls_back(self):
        def _mkdir_then_fail(req):
            os.makedirs(req.target_dir)
            raise OSError("chgrp failed")
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, prepare=_mkdir_then_fail))
        self.assertFalse(os.path.exists(self.target))
        msg = ctx.exception.refusals[0].result.message
        self.assertIn("failed in prepare()", msg)
        self.assertIn("chgrp failed", msg)

    def test_keyboardinterrupt_propagates_and_rolls_back(self):
        def _mkdir_then_interrupt(req):
            os.makedirs(req.target_dir)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self._run(_mk("a", self.log, prepare=_mkdir_then_interrupt))
        self.assertFalse(os.path.exists(self.target))

    def test_keyboardinterrupt_in_check_propagates(self):
        with self.assertRaises(KeyboardInterrupt):
            self._run(_mk("a", self.log, check=_raise(KeyboardInterrupt())))

    def test_applies_false_skips_both_stages(self):
        self._run(_mk("a", self.log, check=_ok, prepare=_ok, applies=False))
        self.assertEqual([], self.log)

    def test_applies_raises_denies(self):
        def _boom(req):
            raise ValueError("cannot tell")
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, check=_ok, applies=_boom),
                      _mk("b", self.log, check=_ok))
        self.assertIn("applies()", ctx.exception.refusals[0].result.message)
        # The remaining checks still run, so every problem is reported.
        self.assertEqual(["b.check"], self.log)

    def test_none_is_ok(self):
        self._run(_mk("a", self.log, check=lambda r: None,
                      prepare=lambda r: None))
        self.assertEqual(["a.check", "a.prepare"], self.log)

    def test_warn_prints_and_continues(self):
        with mock.patch("ivpm.msg.warning") as warn:
            self._run(_mk("a", self.log,
                          check=lambda r: ClonePrepareResult.warn(
                              "no manifest", "check network"),
                          prepare=_ok))
        self.assertEqual(["a.check", "a.prepare"], self.log)
        text = warn.call_args[0][0]
        self.assertIn("a: no manifest", text)
        self.assertIn("Hint: check network", text)

    def test_exception_is_deny(self):
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, check=_raise(RuntimeError("boom"))))
        msg = ctx.exception.refusals[0].result.message
        self.assertIn("'a' clone preparer failed in check()", msg)
        self.assertIn("RuntimeError: boom", msg)

    def test_srcloadererror_is_deny(self):
        with self.assertRaises(CloneDenied):
            self._run(_mk("a", self.log, check=_raise(SrcLoaderError("bad"))))

    def test_systemexit_is_deny(self):
        with self.assertRaises(CloneDenied):
            self._run(_mk("a", self.log, check=_raise(SystemExit(3))))

    def test_raised_clonedenied_merged(self):
        inner = CloneDenied("u", [ClonePrepareRefusal(
            "a", "check", ClonePrepareResult.deny("inner"))])
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, check=_raise(inner)),
                      _mk("b", self.log, check=_deny("outer")))
        self.assertEqual(["inner", "outer"],
                         [r.result.message for r in ctx.exception.refusals])

    def test_prepare_must_leave_target_empty(self):
        def _populate(req):
            os.makedirs(req.target_dir)
            open(os.path.join(req.target_dir, "marker"), "w").close()
            return ClonePrepareResult.ok()
        with self.assertRaises(CloneDenied) as ctx:
            self._run(_mk("a", self.log, prepare=_populate))
        r = ctx.exception.refusals[0]
        self.assertEqual("core", r.stage)
        self.assertIn("marker", r.result.message)
        # Not empty, so not removed: left for inspection.
        self.assertTrue(os.path.isfile(os.path.join(self.target, "marker")))

    def test_verify_empty_skipped_when_target_nonempty(self):
        os.makedirs(self.target)
        open(os.path.join(self.target, "existing"), "w").close()
        ClonePreparerList([_mk("a", self.log, prepare=_ok)()]).run(
            _req(self.target))
        self.assertEqual(["a.prepare"], self.log)

    def test_successful_prepare_returns_snapshot(self):
        def _mkdir(req):
            os.makedirs(req.target_dir)
        snap = self._run(_mk("a", self.log, prepare=_mkdir))
        self.assertTrue(os.path.isdir(self.target))
        snap.rollback()
        self.assertFalse(os.path.exists(self.target))


class TestPathSnapshot(TestBase):

    def test_records_missing_chain(self):
        target = os.path.join(self.testdir, "a", "b", "ws")
        snap = PathSnapshot(target)
        self.assertEqual(
            [target, os.path.join(self.testdir, "a", "b"),
             os.path.join(self.testdir, "a")],
            snap.created_candidates)
        self.assertFalse(snap.was_nonempty)

    def test_rollback_removes_created_empty_chain(self):
        target = os.path.join(self.testdir, "a", "b", "ws")
        snap = PathSnapshot(target)
        os.makedirs(target)
        snap.rollback()
        self.assertFalse(os.path.exists(os.path.join(self.testdir, "a")))
        self.assertTrue(os.path.isdir(self.testdir))

    def test_rollback_keeps_nonempty(self):
        target = os.path.join(self.testdir, "a", "ws")
        snap = PathSnapshot(target)
        os.makedirs(target)
        open(os.path.join(target, "f"), "w").close()
        snap.rollback()
        self.assertTrue(os.path.isfile(os.path.join(target, "f")))

    def test_rollback_never_removes_preexisting(self):
        target = os.path.join(self.testdir, "ws")
        os.makedirs(target)
        snap = PathSnapshot(target)
        self.assertEqual([], snap.created_candidates)
        snap.rollback()
        self.assertTrue(os.path.isdir(target))

    def test_rollback_ignores_symlink(self):
        real = os.path.join(self.testdir, "real")
        os.makedirs(real)
        target = os.path.join(self.testdir, "ws")
        snap = PathSnapshot(target)
        os.symlink(real, target)
        snap.rollback()
        self.assertTrue(os.path.islink(target))
        self.assertTrue(os.path.isdir(real))

    def test_rollback_idempotent(self):
        target = os.path.join(self.testdir, "ws")
        snap = PathSnapshot(target)
        os.makedirs(target)
        snap.rollback()
        snap.rollback()
        self.assertFalse(os.path.exists(target))

    def test_was_nonempty(self):
        target = os.path.join(self.testdir, "ws")
        os.makedirs(target)
        open(os.path.join(target, "f"), "w").close()
        self.assertTrue(PathSnapshot(target).was_nonempty)


if __name__ == "__main__":
    unittest.main()
