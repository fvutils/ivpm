#****************************************************************************
#* pkg_preparer.py
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
"""Preparing a package's location before anything is written into it.

A *preparer* runs immediately before a package is populated, with the package's
resolved settings and its concrete target directory in hand.  It may configure
that location -- create the directory with a particular group and mode, apply an
ACL -- and it may refuse, which aborts the update.

Refusing falls out of preparing: a preparer that cannot create its directory
returns ``DENY``.  That is why there is no separate read-only "gate" concept.

IVPM ships no preparers.  The registry is empty by default and dispatch is a
no-op, so nothing changes for anyone who has not installed one.

See pkg-prepare-design.md Part II.
"""
import dataclasses as dc
import enum
from typing import ClassVar, List, Optional


class PrepareDenied(Exception):
    """Raised by the dispatcher when one or more preparers refused a package.

    Carries every refusal, not just the first: a package with three problems
    should report three.
    """

    def __init__(self, pkg_name: str, refusals: List['PrepareRefusal']):
        self.pkg_name = pkg_name
        self.refusals = list(refusals)
        super().__init__(self.format())

    def format(self) -> str:
        if len(self.refusals) == 1:
            r = self.refusals[0]
            msg = "package '%s' was refused by the '%s' preparer\n  %s" % (
                self.pkg_name, r.preparer, r.result.message)
            if r.result.hint:
                msg += "\n  Hint: %s" % r.result.hint
            return msg

        lines = ["package '%s' was refused by %d preparers:" % (
            self.pkg_name, len(self.refusals))]
        for r in self.refusals:
            lines.append("  [%s] %s" % (r.preparer, r.result.message))
            if r.result.hint:
                lines.append("      Hint: %s" % r.result.hint)
        return "\n".join(lines)


class PrepareOutcome(enum.Enum):
    OK = enum.auto()     # proceed
    WARN = enum.auto()   # proceed, but surface a warning
    DENY = enum.auto()   # abort this package, and so the update


@dc.dataclass(frozen=True)
class PrepareResult:
    outcome: PrepareOutcome
    message: str = ""
    hint: str = ""       # what the user should do about it

    @classmethod
    def ok(cls) -> 'PrepareResult':
        return cls(PrepareOutcome.OK)

    @classmethod
    def warn(cls, message: str, hint: str = "") -> 'PrepareResult':
        return cls(PrepareOutcome.WARN, message, hint)

    @classmethod
    def deny(cls, message: str, hint: str = "") -> 'PrepareResult':
        return cls(PrepareOutcome.DENY, message, hint)

    @property
    def denied(self) -> bool:
        return self.outcome is PrepareOutcome.DENY


@dc.dataclass(frozen=True)
class PrepareRefusal:
    """One preparer's refusal of one package."""
    preparer: str
    result: PrepareResult


@dc.dataclass
class PrepareRequest:
    """Everything a preparer is entitled to see about one package.

    Deliberately *not* frozen: ``prepare()`` mutates the filesystem at
    ``target_dir``, which is the point.  The package *spec* is off limits by
    contract -- a preparer prepares a location, it does not choose content.
    """

    pkg: object                  # the resolved Package (post process_options)
    decision: object             # LoadDecision: what is about to happen, and why
    target_dir: str              # exactly what will be written == pkg.path
    deps_dir: str                # the deps-dir of the scope this resolves into
    scope_key: str               # path relative to the root deps-dir
    update_info: object          # ProjectUpdateInfo (a scope view)
    config: dict                 # this preparer's own 'with:' block ({} if absent)
    cache_dir: Optional[str] = None   # where cached content would land, if any
    is_virtual: bool = False     # a factory source: occupies no directory

    @property
    def install_mode(self):
        return getattr(self.update_info, "install_mode", None)

    @property
    def args(self):
        """The CLI Namespace, for preparers that read common knobs."""
        return getattr(self.update_info, "args", None)

    def handler_config(self, name: str) -> dict:
        """Another component's ``with:`` block, for preparers that need it."""
        return (getattr(self.update_info, "handler_configs", {}) or {}).get(
            name, {}) or {}


@dc.dataclass
class PackagePreparer(object):
    """Base class for a pre-populate extension.

    Registered through the ``ivpm.pkg_preparers`` entry-point group.  Small on
    purpose: no phases, no run_before/run_after, no destroy hook, no lock
    contributions.  Something that needs those is a handler.
    """

    # --- metadata (ClassVar -- override with plain assignment) ---
    name: ClassVar[Optional[str]] = None
    description: ClassVar[Optional[str]] = None

    # Lower runs first.  Ties broken by name, so the order is total and stable.
    order: ClassVar[int] = 100

    # When False (the default), prepare() is called only for packages that are
    # about to be populated.  Set True for a policy check that must see every
    # declared dependency regardless of whether it is already resident; such a
    # preparer reads req.decision to branch.
    always: ClassVar[bool] = False

    def on_session_start(self, update_info) -> None:
        """Once, on the main thread, before any package is fetched and before
        the root deps-dir is created.

        Do expensive one-time work here -- reading the mount table, querying
        ACLs, fetching policy -- and cache it on ``self``; ``prepare()`` then
        runs per package against that cached state.  May raise ``PrepareDenied``
        to abort the whole run before anything is written.
        """
        pass

    def prepare(self, req: PrepareRequest) -> Optional[PrepareResult]:
        """Prepare (or refuse) one package's location.

        Called immediately before the package's content is written, with
        ``req.target_dir`` not yet populated.

        MUST be **idempotent**: it runs again on the next update for any package
        that is re-fetched or reconciled.  MUST be **thread-safe**: it runs on
        the fetch worker threads, up to ``--jobs`` at once.  ``os.umask()`` is
        process-global and must not be used here -- set a POSIX default ACL on
        the directory instead, which inherits permission bits as well as group.

        Return ``PrepareResult.ok()`` (or ``None``) to proceed.
        """
        return PrepareResult.ok()

    def on_session_end(self, update_info) -> None:
        """Once, on the main thread, after the fetch phase.  Optional."""
        pass

    @classmethod
    def preparer_info(cls):
        """Self-description for ``ivpm show preparers``."""
        from ..show.info_types import PreparerInfo
        return PreparerInfo(
            name=cls.name or "unknown",
            description=cls.description or "",
            order=cls.order,
            always=cls.always,
        )
