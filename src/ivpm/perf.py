#****************************************************************************
#* perf.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
"""
Thread-safe performance-span collector for update operations.

A :class:`Span` is one timed phase (``git.clone``, ``pip.install``, ...),
optionally tagged with the package it belongs to and carrying arbitrary
``meta``.  Spans nest via ``parent_id`` so the flat list a run produces
reconstructs into a call-tree (:meth:`PerfCollector.to_tree`) and, later, a
timeline for a trace viewer.

The collector is deliberately decoupled from the TUI and the event dispatcher:
it accumulates independently so a report can be rendered *after* the TUI tears
down, and persisted for later inspection.

Concurrency: the update fetch phase runs packages on worker threads (an executor
under an ``asyncio`` semaphore), so every mutation is guarded by a lock and the
current-parent stack is thread-local.  A parent opened on one thread (the
``fetch`` phase, on the asyncio thread) is linked to children opened on worker
threads via an explicit ``parent_id`` argument -- the thread-local stack alone
cannot see across that boundary.
"""
import contextlib
import dataclasses as dc
import glob
import json
import os
import threading
import time
from typing import Dict, List, Optional


@dc.dataclass
class Span:
    """One timed phase of an operation."""
    category: str
    package: Optional[str] = None
    start: float = 0.0
    end: Optional[float] = None
    thread: Optional[int] = None
    parent_id: Optional[int] = None
    span_id: int = 0
    meta: dict = dc.field(default_factory=dict)

    @property
    def duration(self) -> Optional[float]:
        """Elapsed seconds, or ``None`` while the span is still open (an open
        span at serialization time means the run did not complete -- e.g. a
        crash mid-phase)."""
        if self.end is None:
            return None
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "category": self.category,
            "package": self.package,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "thread": self.thread,
            "meta": self.meta,
        }


# A detached span handed back when collection is disabled.  Never recorded;
# assigning to its ``.meta`` is harmless.
_DISABLED_SPAN = Span(category="<disabled>", span_id=-1)


@contextlib.contextmanager
def _null_span():
    yield _DISABLED_SPAN


def span_or_null(perf: Optional['PerfCollector'], category: str,
                 package: Optional[str] = None,
                 parent_id: Optional[int] = None, **meta):
    """Return a span context manager, or a no-op when ``perf`` is None.

    Lets instrumentation sites that may run without a collector (other code
    paths, tests) write ``with span_or_null(update_info.perf, ...) as s:`` and
    still assign ``s.meta[...]`` unconditionally (the disabled span absorbs it).
    """
    if perf is None:
        return _null_span()
    return perf.span(category, package=package, parent_id=parent_id, **meta)


class PerfCollector:
    """Thread-safe accumulator of :class:`Span` records.

    Collection is always cheap (a couple of ``time.monotonic()`` reads and one
    lock-guarded append per span); callers gate *display*, not collection, so a
    record can be persisted every run.  Constructing with ``enabled=False``
    turns :meth:`span`/:meth:`open_span` into near-zero-cost no-ops.
    """

    SCHEMA = 1

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        # Absolute wall-clock anchor for the human timestamp / runid.  Durations
        # use time.monotonic() (immune to wall-clock adjustment); this single
        # time.time() ties the monotonic timeline back to a real date.
        self.wall_start = time.time()
        self._mono_start = time.monotonic()
        self._lock = threading.Lock()
        self._next_id = 1
        self._spans: List[Span] = []
        # Per-thread stack of currently-open spans, so nested span() calls on
        # the same thread parent correctly without threading state through.
        self._local = threading.local()

    # -- stack helpers (thread-local) --------------------------------------

    def _stack(self) -> List[Span]:
        st = getattr(self._local, "stack", None)
        if st is None:
            st = []
            self._local.stack = st
        return st

    def current_parent_id(self) -> Optional[int]:
        """span_id of the innermost open span on the *calling* thread, or None.

        Capture this on the thread that owns a parent span before dispatching
        work to another thread, then pass it as ``parent_id`` to the child's
        first :meth:`open_span`/:meth:`span` so the tree survives the handoff.
        """
        st = self._stack()
        return st[-1].span_id if st else None

    # -- span lifecycle ----------------------------------------------------

    def open_span(self, category: str, package: Optional[str] = None,
                  parent_id: Optional[int] = None, **meta) -> Span:
        """Open a span and return it.  Use this (paired with :meth:`close_span`)
        where enter/exit straddle an ``await`` or a thread boundary; otherwise
        prefer the :meth:`span` context manager.

        ``parent_id`` overrides the thread-local parent -- required for the
        cross-thread handoff.  When omitted, the innermost open span on this
        thread is the parent.
        """
        if not self.enabled:
            return _DISABLED_SPAN
        with self._lock:
            span_id = self._next_id
            self._next_id += 1
        if parent_id is None:
            parent_id = self.current_parent_id()
        span = Span(
            category=category,
            package=package,
            start=time.monotonic(),
            thread=threading.get_ident(),
            parent_id=parent_id,
            span_id=span_id,
            meta=dict(meta),
        )
        self._stack().append(span)
        return span

    def close_span(self, span: Span):
        """Close a span opened with :meth:`open_span` and record it.

        Pops the span from the calling thread's stack (by identity, tolerating
        mild mis-nesting) and appends it to the shared record under the lock.
        """
        if not self.enabled or span is _DISABLED_SPAN:
            return
        span.end = time.monotonic()
        st = self._stack()
        if st and st[-1] is span:
            st.pop()
        else:
            try:
                st.remove(span)
            except ValueError:
                pass
        with self._lock:
            self._spans.append(span)

    @contextlib.contextmanager
    def span(self, category: str, package: Optional[str] = None,
             parent_id: Optional[int] = None, **meta):
        """Context-manager form: open on enter, close on exit (even on error).

        Yields the :class:`Span` so the body can enrich ``.meta``.
        """
        span = self.open_span(category, package=package,
                              parent_id=parent_id, **meta)
        try:
            yield span
        finally:
            self.close_span(span)

    # -- readout -----------------------------------------------------------

    @property
    def spans(self) -> List[Span]:
        with self._lock:
            return list(self._spans)

    def to_tree(self) -> List[dict]:
        """Reconstruct the nesting from ``parent_id`` into a list of root nodes.

        Each node is ``{"span": Span, "children": [node, ...]}``.  A span whose
        ``parent_id`` names no recorded span (e.g. an orphan from a dropped
        cross-thread handoff) is promoted to a root, so no span is ever lost.
        Children are ordered by ``start``.
        """
        spans = self.spans
        nodes: Dict[int, dict] = {
            s.span_id: {"span": s, "children": []} for s in spans}
        roots: List[dict] = []
        for s in spans:
            node = nodes[s.span_id]
            parent = nodes.get(s.parent_id) if s.parent_id is not None else None
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)
        for node in nodes.values():
            node["children"].sort(key=lambda n: n["span"].start)
        roots.sort(key=lambda n: n["span"].start)
        return roots

    def runid(self) -> str:
        """A filesystem- and sort-friendly run identifier: local time of the run
        start plus this process's pid (``YYYYMMDD-HHMMSS-<pid>``)."""
        return (time.strftime("%Y%m%d-%H%M%S", time.localtime(self.wall_start))
                + "-%d" % os.getpid())

    def to_json(self, header: Optional[dict] = None) -> dict:
        """Serialize to the persisted record schema.

        ``header`` (run metadata: runid, max_parallel, tool versions, ...) is
        merged in by the caller at persist time.
        """
        data = {
            "schema": self.SCHEMA,
            "wall_start": self.wall_start,
            "spans": [s.to_dict() for s in self.spans],
        }
        if header:
            data["header"] = header
        return data


# --- persistence ----------------------------------------------------------
#
# Records live in ``<deps_dir>/.ivpm/perf-<runid>.json``.  Every instrumented
# run writes one; retention prunes to the newest N so the directory does not
# grow without bound.

PERF_SUBDIR = ".ivpm"
_PERF_GLOB = "perf-*.json"


def perf_dir(deps_dir: str) -> str:
    return os.path.join(deps_dir, PERF_SUBDIR)


def write_record(deps_dir: str, data: dict, runid: str) -> str:
    """Atomically write a record to ``<deps_dir>/.ivpm/perf-<runid>.json``.

    Best-effort: returns the path on success. Any failure is the caller's to
    tolerate (perf logging must never break an update).
    """
    d = perf_dir(deps_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "perf-%s.json" % runid)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as fp:
        json.dump(data, fp)
    os.replace(tmp, path)
    return path


def list_record_paths(deps_dir: str) -> List[str]:
    """Record paths, newest first (by mtime)."""
    paths = glob.glob(os.path.join(perf_dir(deps_dir), _PERF_GLOB))
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


def prune_perf_dir(deps_dir: str, keep: int) -> int:
    """Delete all but the newest ``keep`` records. ``keep<=0`` disables pruning.
    Returns the number removed."""
    if keep is None or keep <= 0:
        return 0
    paths = list_record_paths(deps_dir)
    removed = 0
    for old in paths[keep:]:
        try:
            os.remove(old)
            removed += 1
        except OSError:
            pass
    return removed


def load_record(path: str) -> dict:
    with open(path) as fp:
        return json.load(fp)
