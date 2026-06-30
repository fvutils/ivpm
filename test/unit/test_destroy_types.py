"""
Phase 1 tests for `ivpm destroy`: the verdict/result dataclasses and enums.

Pure construction tests — no I/O, no orchestration.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

import unittest

from ivpm.pkg_remove import (
    SafetyLevel, RemoveOutcome, SafetyReason, RemovalSafety,
    PkgRemoveResult, DestroyReport,
)
from ivpm.project_ops_info import ProjectRemoveInfo


class TestDestroyTypes(unittest.TestCase):

    def test_safety_level_members(self):
        self.assertEqual(
            {l.name for l in SafetyLevel},
            {"SAFE", "UNVERIFIABLE", "BLOCKED"})

    def test_remove_outcome_members(self):
        self.assertEqual(
            {o.name for o in RemoveOutcome},
            {"REMOVED", "SKIPPED", "BLOCKED", "MANUAL"})

    def test_safety_reason_defaults(self):
        r = SafetyReason("modified")
        self.assertEqual(r.kind, "modified")
        self.assertEqual(r.items, [])
        self.assertIsNone(r.count)
        self.assertEqual(r.data, {})
        self.assertIsNone(r.label)

    def test_safety_reason_independent_mutable_defaults(self):
        a = SafetyReason("modified")
        b = SafetyReason("untracked")
        a.items.append("x")
        a.data["k"] = "v"
        self.assertEqual(b.items, [])
        self.assertEqual(b.data, {})

    def test_removal_safety_defaults(self):
        v = RemovalSafety(SafetyLevel.SAFE)
        self.assertEqual(v.level, SafetyLevel.SAFE)
        self.assertEqual(v.reasons, [])

    def test_removal_safety_with_reasons(self):
        v = RemovalSafety(SafetyLevel.BLOCKED, [
            SafetyReason("modified", items=["a.py", "b.py"]),
            SafetyReason("unpushed", count=3, data={"upstream": "origin/x"}),
        ])
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertEqual(len(v.reasons), 2)
        self.assertEqual(v.reasons[0].items, ["a.py", "b.py"])
        self.assertEqual(v.reasons[1].data["upstream"], "origin/x")

    def test_pkg_remove_result_defaults(self):
        r = PkgRemoveResult(
            name="mylib", src_type="git", path="/tmp/mylib",
            removal="rmtree", outcome=RemoveOutcome.REMOVED)
        self.assertEqual(r.removal, "rmtree")
        self.assertEqual(r.outcome, RemoveOutcome.REMOVED)
        self.assertIsNone(r.blocked_reason)
        self.assertEqual(r.next_steps, [])
        self.assertIsNone(r.error)
        self.assertEqual(r.removed_paths, [])

    def test_destroy_report_defaults(self):
        rep = DestroyReport(mode="full", target="/ws", deps_dir="/ws/deps")
        self.assertEqual(rep.mode, "full")
        self.assertEqual(rep.gate, {})
        self.assertFalse(rep.blocked)
        self.assertEqual(rep.results, [])
        self.assertFalse(rep.dry_run)
        self.assertFalse(rep.forced)

    def test_project_remove_info_defaults(self):
        info = ProjectRemoveInfo(args=None, deps_dir="/ws/deps")
        self.assertFalse(info.dry_run)
        self.assertFalse(info.force)
        self.assertFalse(info.deps_only)
        self.assertFalse(info.keep_venv)
        self.assertIsNone(info.progress)
        self.assertIsNone(info.event_dispatcher)


if __name__ == "__main__":
    unittest.main()
