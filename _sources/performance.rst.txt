###########
Performance
###########

Overview
========

Every ``ivpm update`` records fine-grained timing of where it spends time —
parsing YAML, resolving commit hashes, waiting for a worker slot, cloning on a
cache miss, storing to and materializing from the cache, and creating the
Python virtual environment / installing packages. The timing survives the
progress display, so it can be inspected after a TUI run and compared across
runs to catch regressions.

Collection is always on and has negligible overhead. The ``--timing`` flag only
controls whether a breakdown is *printed*; a machine-readable record is written
every run regardless.

Seeing the breakdown
====================

Add ``--timing`` (alias ``--profile``) to print a report after the update::

    ivpm update --timing

The report has four panels:

**LONG POLE** — the critical path: the sequence of phases that determines total
wall-clock. Because packages are fetched in parallel, the fetch phase
contributes only its slowest (last-finishing) package's chain. This is the
"what should I optimize first" view.

**HOT SPOTS** — cumulative self-time per category (like a profiler's flat
profile). A category can dominate here — e.g. ``git.clone`` summed across many
packages — even when no single instance is the long pole, which is a signal to
enable caching fleet-wide.

**PACKAGES** — per-package fetch total, dominant sub-phase, cache hit/miss, and
queue-wait (time spent waiting for a worker slot).

**WATERFALL** — a Gantt-style timeline: one lane per package (plus a ``main``
lane for global phases) on a shared time axis, drawing only the leaf operations
(clone, submodule, pip install, …). Queue-wait is shown as ``·`` so a package's
wait for a worker slot is visible before its work. A legend maps the glyphs.
Reveals serialization and phases that cannot overlap (e.g. the venv/pip phase
cannot start until the fetch phase finishes).

Persisted records
=================

Each run writes ``deps/.ivpm/perf-<runid>.json`` (``runid`` is the run's start
time plus pid). The newest 20 records are kept; older ones are pruned
automatically. Set ``IVPM_PERF_KEEP=N`` to change the retention count (``0``
disables pruning).

Inspecting records with ``ivpm perf``
=====================================

``ivpm perf`` reads the persisted records — no live run required:

``ivpm perf list``
    List available records, newest first.

``ivpm perf show [runid]``
    Print the four-panel report for a record (default: the newest). Add
    ``--compact`` to omit the waterfall.

``ivpm perf export [runid] --format chrome [-o FILE]``
    Emit Chrome Trace Event JSON. Load it in https://ui.perfetto.dev or
    ``chrome://tracing`` for a zoomable flamegraph and timeline.

``ivpm perf diff <A> <B>``
    Compare two records (runids or file paths) — per-category self-time deltas,
    new/vanished phases, and the total wall-clock change. Use it to answer
    "what got slower since last week / on this branch."

All commands accept ``-p/--project-dir`` (default: the current directory).
