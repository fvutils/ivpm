"""Clone-preparer registry: registration, provenance, and plugin loading."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.clone.clone_preparer import ClonePreparer, ClonePrepareResult
from ivpm.clone.clone_preparer_rgy import (
    ClonePreparerRgy, ClonePreparerLoadError, CLONE_PREPARER_GROUP)


class _EP(object):
    def __init__(self, name, value, load):
        self.name = name
        self.value = value
        self._load = load
        self.dist = None

    def load(self):
        return self._load()


class _P(ClonePreparer):
    name = "p"
    description = "a clone preparer"
    order = 7

    def check(self, req):
        return ClonePrepareResult.ok()


def _with_entry_points(eps, fn):
    # _load_plugins imports entry_points from ivpm._compat inside the function,
    # so patching the module attribute takes effect for the call below.
    import ivpm._compat as compat
    saved = compat.entry_points
    seen = []

    def _fake(group=None):
        seen.append(group)
        return list(eps)
    compat.entry_points = _fake
    try:
        fn()
    finally:
        compat.entry_points = saved
    return seen


class TestClonePreparerRgy(TestBase):

    def tearDown(self):
        ClonePreparerRgy._inst = None
        super().tearDown()

    def test_empty_by_default(self):
        rgy = ClonePreparerRgy()
        groups = _with_entry_points([], rgy._load)
        self.assertEqual([CLONE_PREPARER_GROUP], groups)
        self.assertEqual("ivpm.clone_preparers", CLONE_PREPARER_GROUP)
        self.assertEqual(0, len(rgy.mkPreparerList()))

    def test_provenance_is_recorded(self):
        rgy = ClonePreparerRgy()
        rgy.addPreparer(_P, origin="acme.ext", version="1.2",
                        provider="acme-ivpm")
        infos = rgy.preparer_infos()
        self.assertEqual(1, len(infos))
        info = infos[0]
        self.assertEqual(("p", "a clone preparer", 7, ["check"]),
                         (info.name, info.description, info.order, info.stages))
        self.assertEqual(("acme.ext", "1.2", "acme-ivpm"),
                         (info.origin, info.version, info.provider))

    def test_plugin_loaded(self):
        rgy = ClonePreparerRgy()
        _with_entry_points([_EP("p", "acme:P", lambda: _P)], rgy._load)
        self.assertEqual(["p"], rgy.names())
        pl = rgy.mkPreparerList()
        self.assertIsInstance(pl.preparers[0], _P)

    def test_duplicate_entry_point_registered_once(self):
        rgy = ClonePreparerRgy()
        _with_entry_points([_EP("p", "acme:P", lambda: _P),
                            _EP("p2", "acme:P", lambda: _P)], rgy._load)
        self.assertEqual(["p"], rgy.names())

    def test_load_failure_is_fatal(self):
        def _bad():
            raise ImportError("no such module")
        rgy = ClonePreparerRgy()
        with self.assertRaises(ClonePreparerLoadError) as ctx:
            _with_entry_points([_EP("bad", "nope:X", _bad)], rgy._load)
        text = str(ctx.exception)
        self.assertIn("bad", text)
        self.assertIn("will not clone without it", text)

    def test_inst_not_cached_after_load_failure(self):
        def _bad():
            raise ImportError("no such module")
        with self.assertRaises(ClonePreparerLoadError):
            _with_entry_points([_EP("bad", "nope:X", _bad)],
                               ClonePreparerRgy.inst)
        self.assertIsNone(ClonePreparerRgy._inst)


if __name__ == "__main__":
    unittest.main()
