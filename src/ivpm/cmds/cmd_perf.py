#****************************************************************************
#* cmd_perf.py
#*
#* `ivpm perf` — inspect persisted performance records (deps/.ivpm/perf-*.json).
#*
#* Pure post-processing over the records written by an instrumented update;
#* no live run required.
#****************************************************************************
import json
import os
import sys

from ..proj_info import resolve_deps_dir
from .. import perf_report
from ..perf import list_record_paths, load_record, perf_dir


def _resolve_deps_dir(args) -> str:
    """Resolve the workspace deps dir (holding .ivpm/perf-*.json) from the
    project directory: via ivpm.yaml when present, otherwise by locating the
    lockfile in a direct sub-directory."""
    project_dir = getattr(args, "project_dir", None) or os.getcwd()
    return resolve_deps_dir(project_dir)


def _find_record(deps_dir: str, runid: str = None):
    """Return (path, record) for the named runid, or the newest when None."""
    paths = list_record_paths(deps_dir)
    if not paths:
        return None, None
    if runid is None:
        path = paths[0]
    else:
        # Accept a bare runid or a full/partial filename.
        target = "perf-%s.json" % runid
        matches = [p for p in paths
                   if os.path.basename(p) == target or runid in os.path.basename(p)]
        if not matches:
            return None, None
        path = matches[0]
    try:
        return path, load_record(path)
    except (OSError, ValueError):
        return path, None


class CmdPerf:
    """Handler for the `ivpm perf` subcommands."""

    def __call__(self, args):
        cmd = getattr(args, "perf_cmd", None)
        if cmd == "list":
            return self._list(args)
        elif cmd == "show":
            return self._show(args)
        elif cmd == "export":
            return self._export(args)
        elif cmd == "diff":
            return self._diff(args)
        else:
            print("Unknown perf command: %s" % cmd, file=sys.stderr)
            sys.exit(1)

    def _list(self, args):
        deps_dir = _resolve_deps_dir(args)
        sys.stdout.write(perf_report.render_list(deps_dir))

    def _show(self, args):
        deps_dir = _resolve_deps_dir(args)
        path, rec = _find_record(deps_dir, getattr(args, "runid", None))
        if rec is None:
            print("No perf record found under %s" % perf_dir(deps_dir),
                  file=sys.stderr)
            sys.exit(1)
        sys.stdout.write(perf_report.render(rec, full=not getattr(args, "compact", False)))

    def _export(self, args):
        deps_dir = _resolve_deps_dir(args)
        path, rec = _find_record(deps_dir, getattr(args, "runid", None))
        if rec is None:
            print("No perf record found under %s" % perf_dir(deps_dir),
                  file=sys.stderr)
            sys.exit(1)
        fmt = getattr(args, "format", "chrome")
        if fmt != "chrome":
            print("Unsupported export format: %s (only 'chrome')" % fmt,
                  file=sys.stderr)
            sys.exit(1)
        data = perf_report.to_chrome_trace(rec)
        out = getattr(args, "output", None)
        text = json.dumps(data)
        if out:
            with open(out, "w") as fp:
                fp.write(text)
            print("Wrote Chrome trace to %s (load in https://ui.perfetto.dev)"
                  % out, file=sys.stderr)
        else:
            sys.stdout.write(text)

    def _diff(self, args):
        deps_dir = _resolve_deps_dir(args)
        pa, ra = self._load_arg(deps_dir, args.run_a)
        pb, rb = self._load_arg(deps_dir, args.run_b)
        if ra is None or rb is None:
            print("Could not load both records for diff", file=sys.stderr)
            sys.exit(1)
        d = perf_report.diff(ra, rb)
        sys.stdout.write(perf_report.render_diff(
            d,
            ra.get("header", {}).get("runid", args.run_a),
            rb.get("header", {}).get("runid", args.run_b)))

    def _load_arg(self, deps_dir, ref):
        """A diff argument may be a file path or a runid."""
        if os.path.isfile(ref):
            try:
                return ref, load_record(ref)
            except (OSError, ValueError):
                return ref, None
        return _find_record(deps_dir, ref)
