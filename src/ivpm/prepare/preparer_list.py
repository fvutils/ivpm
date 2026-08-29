#****************************************************************************
#* preparer_list.py
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
"""Dispatching a package to every registered preparer."""
import dataclasses as dc
import logging
from typing import List, Optional

from .pkg_preparer import (
    PackagePreparer, PrepareDenied, PrepareOutcome, PrepareRefusal,
    PrepareRequest, PrepareResult)

_logger = logging.getLogger("ivpm.prepare.preparer_list")


class PreparerList(object):
    """Runs the registered preparers for one package, in a stable order.

    Every preparer is consulted, even after one has refused, so a package with
    several problems reports them together rather than one per re-run.
    """

    def __init__(self, preparers: Optional[List[PackagePreparer]] = None):
        self.preparers = list(preparers or [])

    def add(self, preparer: PackagePreparer):
        self.preparers.append(preparer)

    def __len__(self):
        return len(self.preparers)

    def ordered(self) -> List[PackagePreparer]:
        """Registered preparers by (order, name). Total and stable."""
        return sorted(
            self.preparers,
            key=lambda p: (getattr(type(p), "order", 100),
                           getattr(type(p), "name", None) or type(p).__name__))

    # ------------------------------------------------------------------ #
    # Session hooks                                                       #
    # ------------------------------------------------------------------ #

    def on_session_start(self, update_info) -> None:
        for p in self.ordered():
            p.on_session_start(update_info)

    def on_session_end(self, update_info) -> None:
        for p in self.ordered():
            try:
                p.on_session_end(update_info)
            except Exception as e:
                # The fetch is over and the workspace is populated; a teardown
                # failure must not fail an otherwise-successful update.
                _logger.warning("Preparer '%s' failed in on_session_end: %s",
                                self._name(p), e)

    # ------------------------------------------------------------------ #
    # Per-package dispatch                                                #
    # ------------------------------------------------------------------ #

    def prepare(self, req: PrepareRequest) -> None:
        """Run every applicable preparer for *req*.

        Raises ``PrepareDenied`` carrying all refusals if any preparer refused.
        """
        if not self.preparers:
            return

        refusals = []
        for p in self.ordered():
            if not self._applies(p, req):
                continue
            # Each preparer sees only its own 'with:' block, so a request is
            # rebuilt per preparer rather than shared. dc.replace copies the
            # (cheap, mostly-reference) fields; pkg/update_info stay identical.
            result = self._invoke(
                p, dc.replace(req, config=self._config_for(p, req)))
            if result is None or result.outcome is PrepareOutcome.OK:
                continue
            if result.outcome is PrepareOutcome.WARN:
                from ..msg import warning
                warning("%s: %s" % (self._name(p), result.message),
                        getattr(req.pkg, "srcinfo", None))
                continue
            refusals.append(PrepareRefusal(self._name(p), result))

        if refusals:
            raise PrepareDenied(getattr(req.pkg, "name", "<unknown>"), refusals)

    @staticmethod
    def _applies(preparer, req) -> bool:
        """Whether this preparer runs for this package.

        By default a preparer sees only packages that are about to be populated
        -- nothing is being written for a reused package, so there is nothing to
        prepare, and the common everything-already-present update pays nothing.
        A preparer declaring ``always = True`` is called regardless and reads
        ``req.decision`` to branch.
        """
        if getattr(type(preparer), "always", False):
            return True
        decision = req.decision
        return decision is None or not getattr(decision, "is_resident", False)

    def _config_for(self, preparer, req) -> dict:
        """This preparer's own ``with: <name>:`` block, or {}.

        Same mechanism plugin handlers use: the project's ``with:`` keys land in
        ``ProjectUpdateInfo.handler_configs``, package-level overlaid by the
        selected dep-set's. A preparer never has to reach into update_info for
        its own settings.
        """
        name = self._name(preparer)
        configs = getattr(req.update_info, "handler_configs", None) or {}
        return configs.get(name, {}) or {}

    def _invoke(self, preparer, req) -> Optional[PrepareResult]:
        try:
            return preparer.prepare(req)
        except PrepareDenied:
            # A preparer may raise the dispatcher's own exception to refuse.
            raise
        except Exception as e:
            # Unlike a handler's leaf callback (which is logged and swallowed),
            # a preparer that crashed did not prepare anything. Proceeding would
            # write content into a location whose configuration is unknown.
            _logger.debug("Preparer '%s' raised for %s", self._name(preparer),
                          getattr(req.pkg, "name", "?"), exc_info=True)
            return PrepareResult.deny(
                "the '%s' preparer failed: %s" % (self._name(preparer), e))

    @staticmethod
    def _name(preparer) -> str:
        return getattr(type(preparer), "name", None) or type(preparer).__name__
