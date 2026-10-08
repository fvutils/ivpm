#****************************************************************************
#* clone_preparer_list.py
#*
#* Copyright 2025 Matthew Ballance and Contributors
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
"""Dispatching one clone to every registered clone preparer.

Two stages: every ``check()`` first, then -- only if nobody refused -- each
``prepare()``.  IVPM's own duties are generic: ordering, collecting refusals,
verifying a prepared directory was left empty, and removing directories that
appeared during ``prepare()`` if the clone does not go ahead.
"""
import logging
import dataclasses as dc
import os
from typing import List, Optional

from .clone_preparer import (
    ClonePreparer, ClonePrepareOutcome, ClonePrepareRefusal,
    ClonePrepareRequest, ClonePrepareResult, CloneDenied)

_logger = logging.getLogger("ivpm.clone.clone_preparer_list")


class PathSnapshot(object):
    """Which components of a path did not exist before ``prepare()`` ran.

    ``rollback()`` removes those that have since appeared, deepest first, but
    only while they are empty real directories: it never deletes content and
    never touches a directory that existed beforehand.  Idempotent.
    """

    def __init__(self, target_dir: str):
        self.target_dir = os.path.abspath(target_dir)
        self.was_nonempty = (os.path.isdir(self.target_dir)
                             and bool(os.listdir(self.target_dir)))
        self.created_candidates = []     # deepest first
        p = self.target_dir
        while not os.path.lexists(p):
            self.created_candidates.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent

    def rollback(self) -> None:
        for p in self.created_candidates:
            if os.path.islink(p) or not os.path.isdir(p):
                continue
            try:
                os.rmdir(p)          # fails, harmlessly, when not empty
                _logger.debug("rolled back prepared directory %s", p)
            except OSError:
                pass


class NullSnapshot(object):
    """Stand-in when no clone preparer is installed: nothing to roll back."""

    def rollback(self) -> None:
        pass


class ClonePreparerList(object):
    """Runs the registered clone preparers for one clone, in a stable order."""

    def __init__(self, preparers: Optional[List[ClonePreparer]] = None):
        self.preparers = list(preparers or [])

    def add(self, preparer: ClonePreparer):
        self.preparers.append(preparer)

    def __len__(self):
        return len(self.preparers)

    def ordered(self) -> List[ClonePreparer]:
        """Registered preparers by (order, name). Total and stable."""
        return sorted(
            self.preparers,
            key=lambda p: (getattr(type(p), "order", 100), self._name(p)))

    def run(self, req: ClonePrepareRequest):
        """Check, then prepare.  Returns the snapshot to roll back should the
        clone itself fail; raises ``CloneDenied`` carrying every refusal."""
        if not self.preparers:
            return NullSnapshot()

        active, refusals = self._applicable(req)

        # Stage 1: every check, nothing written. All refusals are collected.
        refusals += self._stage(active, "check", req, stop_on_deny=False)
        if refusals:
            raise CloneDenied(req.src, refusals)

        # Stage 2: prepare. Stop at the first refusal -- preparing on top of a
        # refused state is exactly what running the checks first avoids.
        snap = PathSnapshot(req.target_dir)
        try:
            refusals = self._stage(active, "prepare", req, stop_on_deny=True)
            if not refusals:
                refusals = self._verify_empty(snap)
            if refusals:
                raise CloneDenied(req.src, refusals)
        except BaseException:
            snap.rollback()
            raise
        return snap

    # ------------------------------------------------------------------ #

    def _applicable(self, req):
        active, refusals = [], []
        for p in self.ordered():
            try:
                if p.applies(self._view(p, req)):
                    active.append(p)
            except (Exception, SystemExit) as e:
                # A preparer that cannot say whether it applies cannot vouch
                # for the clone either.
                refusals.append(self._crashed(p, "check", "applies", e))
        return active, refusals

    def _stage(self, active, stage, req, stop_on_deny):
        refusals = []
        for p in active:
            for r in self._invoke(p, stage, req):
                refusals.append(r)
            if refusals and stop_on_deny:
                break
        return refusals

    def _invoke(self, preparer, stage, req) -> List[ClonePrepareRefusal]:
        name = self._name(preparer)
        try:
            result = getattr(preparer, stage)(self._view(preparer, req))
        except CloneDenied as e:
            # A preparer may raise the dispatcher's own exception to refuse.
            return list(e.refusals)
        except (Exception, SystemExit) as e:
            # Fail closed: a preparer that crashed reached no verdict, and in
            # the prepare stage left the location in an unknown state.
            return [self._crashed(preparer, stage, stage, e)]

        if result is None or result.outcome is ClonePrepareOutcome.OK:
            return []
        if result.outcome is ClonePrepareOutcome.WARN:
            from ..msg import warning
            msg = "%s: %s" % (name, result.message)
            if result.hint:
                msg += "\n  Hint: %s" % result.hint
            warning(msg)
            return []
        return [ClonePrepareRefusal(name, stage, result)]

    def _crashed(self, preparer, stage, method, e) -> ClonePrepareRefusal:
        name = self._name(preparer)
        _logger.debug("Clone preparer '%s' raised in %s()", name, method,
                      exc_info=True)
        return ClonePrepareRefusal(name, stage, ClonePrepareResult.deny(
            "the '%s' clone preparer failed in %s(): %s: %s"
            % (name, method, type(e).__name__, e),
            "a clone preparer that cannot reach a verdict refuses the clone; "
            "it can return WARN instead to fail open"))

    @staticmethod
    def _verify_empty(snap: PathSnapshot) -> List[ClonePrepareRefusal]:
        """A prepared directory must reach the provider empty."""
        if snap.was_nonempty or not os.path.isdir(snap.target_dir):
            return []
        entries = sorted(os.listdir(snap.target_dir))
        if not entries:
            return []
        shown = ", ".join(entries[:5]) + (", ..." if len(entries) > 5 else "")
        return [ClonePrepareRefusal("ivpm", "core", ClonePrepareResult.deny(
            "clone preparer(s) left entries in '%s' (%s); a clone preparer may "
            "set up the directory but must not populate it. The directory has "
            "been left in place for inspection." % (snap.target_dir, shown),
            "fix the clone preparer, or remove the directory and retry"))]

    @classmethod
    def _view(cls, preparer, req):
        """*req* as handed to *preparer*: ``config()`` defaults to its own
        ``with:`` block.  The manifest load stays shared between views."""
        return dc.replace(req, _preparer_name=cls._name(preparer))

    @staticmethod
    def _name(preparer) -> str:
        return getattr(type(preparer), "name", None) or type(preparer).__name__
