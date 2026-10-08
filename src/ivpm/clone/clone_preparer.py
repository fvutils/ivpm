#****************************************************************************
#* clone_preparer.py
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
"""Checking and preparing a workspace location before ``ivpm clone`` writes.

A *clone preparer* is the root-project counterpart of a package preparer.  It
runs before the clone provider writes anything, in two stages:

* ``check()`` -- every applicable preparer decides whether the clone may
  proceed.  Writes nothing.
* ``prepare()`` -- runs only when no preparer refused.  May create the
  workspace directory (and missing ancestors) and set metadata on it (group,
  mode, ACL), but must leave it empty: the provider populates it.

IVPM ships no clone preparers and never interprets site policy itself; the
registry is empty by default and dispatch is a no-op.

See "Contributing a Clone Preparer" in docs/source/extending_ivpm.rst.
"""
import dataclasses as dc
import enum
import hashlib
import io
import os
from typing import Callable, ClassVar, Dict, List, Optional


class ClonePrepareOutcome(enum.Enum):
    OK = enum.auto()     # proceed
    WARN = enum.auto()   # proceed, but surface a warning
    DENY = enum.auto()   # refuse the clone


@dc.dataclass(frozen=True)
class ClonePrepareResult:
    outcome: ClonePrepareOutcome
    message: str = ""
    hint: str = ""       # what the user should do about it

    @classmethod
    def ok(cls) -> 'ClonePrepareResult':
        return cls(ClonePrepareOutcome.OK)

    @classmethod
    def warn(cls, message: str, hint: str = "") -> 'ClonePrepareResult':
        return cls(ClonePrepareOutcome.WARN, message, hint)

    @classmethod
    def deny(cls, message: str, hint: str = "") -> 'ClonePrepareResult':
        return cls(ClonePrepareOutcome.DENY, message, hint)

    @property
    def denied(self) -> bool:
        return self.outcome is ClonePrepareOutcome.DENY


@dc.dataclass(frozen=True)
class ClonePrepareRefusal:
    """One preparer's refusal of the clone, and the stage it happened in.

    ``stage`` is ``"check"``, ``"prepare"``, or ``"core"`` for a refusal IVPM
    raised on a preparer's behalf (e.g. a prepared directory left non-empty).
    """
    preparer: str
    stage: str
    result: ClonePrepareResult


class CloneDenied(Exception):
    """Raised when one or more clone preparers refused the clone.

    Carries every refusal, not just the first, so a workspace with several
    problems reports them together.
    """

    def __init__(self, src: str, refusals: List[ClonePrepareRefusal]):
        self.src = src
        self.refusals = list(refusals)
        super().__init__(self.format())

    def format(self) -> str:
        if len(self.refusals) == 1:
            r = self.refusals[0]
            msg = "clone of '%s' was refused by the '%s' clone preparer\n  %s" % (
                self.src, r.preparer, r.result.message)
            if r.result.hint:
                msg += "\n  Hint: %s" % r.result.hint
            return msg

        lines = ["clone of '%s' was refused by %d clone preparers:" % (
            self.src, len(self.refusals))]
        for r in self.refusals:
            lines.append("  [%s] %s" % (r.preparer, r.result.message))
            if r.result.hint:
                lines.append("      Hint: %s" % r.result.hint)
        return "\n".join(lines)


class ManifestLoadError(Exception):
    """The source's ivpm.yaml exists but could not be loaded.

    Raised by ``ClonePrepareRequest.load_root_package()`` (and the helpers built
    on it) for malformed YAML, a missing or out-of-tree ``include:``, an unknown
    ``-d`` dep-set, and the like.  The text is the diagnostic IVPM would have
    printed; nothing is printed when it is raised.  A preparer should normally
    turn it into a WARN: refusing would stop someone cloning a broken branch in
    order to fix it.
    """


def _no_read(path: str) -> Optional[str]:
    return None


class _SourceFS(object):
    """Maps ``<root>/<rel>`` onto ``read_file(rel)`` for IvpmYamlReader.

    Reads are memoized, so a file probed by ``isfile()`` and then opened is
    fetched once.  Paths outside *root* do not exist.
    """

    def __init__(self, root: str, read_file: Callable[[str], Optional[str]]):
        self._root = root
        self._read_file = read_file
        self._text: Dict[str, Optional[str]] = {}

    def _rel(self, path: str) -> Optional[str]:
        rel = os.path.relpath(os.path.normpath(path), self._root)
        if os.path.isabs(rel) or rel == "." or rel == ".." \
                or rel.startswith(".." + os.sep):
            return None
        return rel.replace(os.sep, "/")

    def text(self, path: str) -> Optional[str]:
        rel = self._rel(path)
        if rel is None:
            return None
        if rel not in self._text:
            self._text[rel] = self._read_file(rel)
        return self._text[rel]

    def isfile(self, path: str) -> bool:
        return self.text(path) is not None

    def open(self, path: str):
        text = self.text(path)
        if text is None:
            raise FileNotFoundError(path)
        fp = io.StringIO(text)
        fp.name = path
        return fp

    def md5(self, path: str) -> str:
        return hashlib.md5(
            (self.text(path) or "").encode("utf-8", "surrogateescape")).hexdigest()


class _ManifestState(object):
    """The root manifest, loaded at most once per clone.  Shared (not copied)
    between the per-preparer views of one request."""

    def __init__(self):
        self.tried = False
        self.proj_info = None
        self.error: Optional[ManifestLoadError] = None


def _diag_text(e, records) -> str:
    """The diagnostic(s) behind a failed load, as IVPM would print them."""
    from ..diagnostics import Severity
    errs = [d.format(excerpt=False) for d in records
            if d.severity >= Severity.ERROR]
    return "\n".join(errs) if errs else str(e)


@dc.dataclass
class ClonePrepareRequest:
    """Everything a clone preparer is entitled to see about one clone.

    Built by CmdClone after the target directory is resolved and before the
    provider runs.
    """

    src: str                        # locator as typed
    url: str                        # after apply_git_url_map()
    provider: str                   # resolved clone-provider name
    # Absolute.  Usually does not exist yet, or is empty; with --here (or a
    # git reuse / clone in place) it may already hold content.
    target_dir: str
    target_nonempty: bool           # snapshot, taken before any prepare()
    branch: Optional[str]
    tag: Optional[str]
    revision: Optional[str]
    dep_set: Optional[List[str]]    # flatten_dep_sets(args.dep_set)
    cli_overrides: dict             # parse_definitions(args.definitions)
    args: object                    # the full CLI Namespace
    # Always None for now: preparers run before the progress TUI starts, so a
    # prompt goes straight to the terminal.
    prompt_callback: Optional[Callable] = None
    # Text of a file (relative to the source root) at the requested ref,
    # without cloning; None when absent or the provider cannot read remotely.
    read_file: Callable[[str], Optional[str]] = _no_read
    # --- internal ---
    # The memoized manifest load, shared by every preparer's view of the
    # request, and the preparer this view was handed to (bound by the
    # dispatcher; the default for config()).
    _manifest: _ManifestState = dc.field(
        default_factory=_ManifestState, repr=False, compare=False)
    _preparer_name: Optional[str] = dc.field(
        default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ #
    # Manifest helpers.  All three read through read_file(), so they see the
    # manifest at the requested ref -- the one the post-clone update will use.

    def load_root_package(self):
        """The source's root ivpm.yaml, with its ``include:`` files merged and
        ``${{var}}`` references resolved (``-D`` applies), as a ``ProjInfo``.

        None when the source has no ivpm.yaml or the provider cannot read files
        before cloning.  Raises ``ManifestLoadError`` when it exists but does not
        load.  Parsed once per clone, however many preparers ask.

        The manifest is read as ``<target_dir>/ivpm.yaml`` -- where it will land
        -- so ``include:`` paths resolve as they will after the clone.  An
        include outside the source tree cannot be read and is an error here.
        """
        st = self._manifest
        if not st.tried:
            st.tried = True
            try:
                st.proj_info = self._load_root_package()
            except ManifestLoadError as e:
                st.error = e
        if st.error is not None:
            raise st.error
        return st.proj_info

    def _load_root_package(self):
        from ..diagnostics import CollectingSink, DiagnosticReporter
        from ..ivpm_yaml_reader import IvpmYamlReader
        from .. import msg

        fs = _SourceFS(self.target_dir, self.read_file)
        path = os.path.join(self.target_dir, "ivpm.yaml")
        if not fs.isfile(path):
            return None

        # Diagnostics are captured, not printed: the post-clone update reads
        # this manifest again and reports on it then.  A fatal() raises once it
        # has been recorded, so the captured text is the error.
        sink = CollectingSink()
        prev = msg.set_reporter(DiagnosticReporter(sink))
        try:
            with fs.open(path) as fp:
                return IvpmYamlReader(fs=fs).read(
                    fp, path,
                    cli_overrides=self.cli_overrides or {},
                    persisted_vars={},
                    is_root=False)
        except Exception as e:
            raise ManifestLoadError(_diag_text(e, sink.records)) from e
        finally:
            msg.set_reporter(prev)

    def _select(self):
        """``(names, PackagesInfo)`` for the dep-set(s) the update will install,
        or None without a manifest."""
        proj_info = self.load_root_package()
        if proj_info is None:
            return None
        from ..diagnostics import CollectingSink, DiagnosticReporter
        from ..project_ops import ProjectOps
        from .. import msg

        sink = CollectingSink()
        prev = msg.set_reporter(DiagnosticReporter(sink))
        try:
            return ProjectOps._getDepSets(
                proj_info, self.dep_set,
                origin=("the -d/--dep-set command-line option"
                        if self.dep_set else None))
        except Exception as e:
            raise ManifestLoadError(_diag_text(e, sink.records)) from e
        finally:
            msg.set_reporter(prev)

    def selected_dep_sets(self) -> Optional[List[str]]:
        """The dep-set name(s) the post-clone update will install: the ``-d``
        values, else ``default-dep-set``, else the manifest's first dep-set.
        None when there is no manifest.  An unknown ``-d`` name raises
        ``ManifestLoadError``."""
        sel = self._select()
        return None if sel is None else list(sel[0])

    def config(self, name: Optional[str] = None) -> dict:
        """The ``with: <name>:`` block, as the post-clone update will see it:
        package-level, overlaid by the selected dep-set(s)' own ``with:``.

        *name* defaults to the calling preparer's name.  ``{}`` when there is
        no manifest or no such block.  Core never interprets the block -- it is
        the preparer's own configuration.  Raises ``ManifestLoadError`` as
        ``load_root_package()`` does.
        """
        if name is None:
            name = self._preparer_name
        if name is None:
            raise ValueError("config() needs a name outside a clone preparer")
        sel = self._select()
        if sel is None:
            return {}
        from ..diagnostics import CollectingSink, DiagnosticReporter
        from ..ivpm_yaml_reader import resolve_effective_with
        from .. import msg

        sink = CollectingSink()
        prev = msg.set_reporter(DiagnosticReporter(sink))
        try:
            _, _, handler_configs, _ = resolve_effective_with(
                self._manifest.proj_info, sel[1])
        except Exception as e:
            raise ManifestLoadError(_diag_text(e, sink.records)) from e
        finally:
            msg.set_reporter(prev)
        return dict(handler_configs.get(name, {}) or {})


class ClonePreparer(object):
    """Base class for a pre-clone extension.

    Registered through the ``ivpm.clone_preparers`` entry-point group.  One
    instance is created per clone, so state cached in ``check()`` is visible to
    ``prepare()``.  Override ``check()``, ``prepare()``, or both.
    """

    # --- metadata (ClassVar -- override with plain assignment) ---
    name: ClassVar[Optional[str]] = None
    description: ClassVar[Optional[str]] = None

    # Lower runs first.  Ties broken by name, so the order is total and stable.
    order: ClassVar[int] = 100

    def applies(self, req: ClonePrepareRequest) -> bool:
        """Whether this preparer participates in this clone (both stages)."""
        return True

    def check(self, req: ClonePrepareRequest) -> Optional[ClonePrepareResult]:
        """Decide whether the clone may proceed.

        MUST NOT write anything.  Every applicable preparer's ``check()`` runs
        before any ``prepare()``.  Return ``ClonePrepareResult.ok()`` (or
        ``None``) to proceed; return ``warn()`` to fail open when information
        is missing.  Raising refuses the clone.
        """
        return ClonePrepareResult.ok()

    def prepare(self, req: ClonePrepareRequest) -> Optional[ClonePrepareResult]:
        """Set up the workspace location.  Called only when no check refused.

        May create ``req.target_dir`` (and missing ancestors) and set metadata
        on it -- group, setgid, a default ACL.  MUST leave ``target_dir`` empty;
        IVPM refuses the clone otherwise.  Do not change the umask (it is
        process-wide); use setgid and a default ACL instead.  Directories that
        did not exist before ``prepare()`` are removed again, if still empty,
        should the clone fail.
        """
        return ClonePrepareResult.ok()

    @classmethod
    def stages(cls) -> List[str]:
        """The stages this preparer implements (overrides)."""
        out = []
        if cls.check is not ClonePreparer.check:
            out.append("check")
        if cls.prepare is not ClonePreparer.prepare:
            out.append("prepare")
        return out

    @classmethod
    def preparer_info(cls):
        """Self-description for ``ivpm show clone-preparers``."""
        from ..show.info_types import ClonePreparerInfo
        return ClonePreparerInfo(
            name=cls.name or cls.__name__,
            description=cls.description or "",
            order=cls.order,
            stages=cls.stages(),
        )
