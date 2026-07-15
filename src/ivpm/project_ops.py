#****************************************************************************
#* project_ops.py
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import logging
import os
import json
import dataclasses as dc
from typing import Tuple, List, Optional
from .package import Package
from .package_updater import PackageUpdater
from .handlers.package_handler_rgy import PackageHandlerRgy
from .project_ops_info import ProjectUpdateInfo, ProjectBuildInfo
from .update_event import UpdateEventDispatcher
from .update_tui import create_update_tui, RichUpdateTUI
from .utils import fatal, note, warning
from .package_lock import write_lock, check_lock_changes

_logger = logging.getLogger("ivpm.project_ops")


@dc.dataclass
class ProjectOps(object):
    root_dir : str
    debug : bool = False

    def update(self,
               dep_set=None,   # str | List[str] | None — one or more dep-sets
               force_py_install : bool = False,
               skip_venv : bool = False,
               args = None,
               lock_file : str = None,
               refresh_all : bool = False,
               force : bool = False,
               cli_overrides = None,
               from_manifest : str = None,
               deps_dir_override : str = None,
               timing : bool = False):
        from .update_event import UpdateEvent, UpdateEventType
        from .perf import PerfCollector

        if from_manifest is not None and lock_file is not None:
            fatal("--from and --lock-file are mutually exclusive "
                  "(both specify an alternate manifest source)")

        # Get log level from args for TUI selection
        log_level = getattr(args, 'log_level', 'NONE')
        verbose = getattr(args, 'verbose', 0)

        # Create event dispatcher and TUI
        event_dispatcher = UpdateEventDispatcher()
        tui = create_update_tui(log_level, verbose=verbose)
        event_dispatcher.add_listener(tui)

        # Determine if we should suppress output (Rich TUI mode)
        suppress_output = isinstance(tui, RichUpdateTUI)

        # Start the TUI (and install its diagnostic sink) before reading any
        # project metadata, so informational notes emitted during _init() are
        # filtered rather than printed above the progress display.
        if isinstance(tui, RichUpdateTUI):
            tui.start()

        # Performance-span collection is always on; the --timing flag (P5) only
        # gates the after-run display. The root span wraps the whole operation
        # so total wall-clock is a single span and every phase parents under it.
        perf = PerfCollector(enabled=True)
        root_span = perf.open_span("update")
        # Expose the collector for after-run persistence/report (P5) and tests.
        self._perf = perf
        # Pre-declared so the finally block can persist even if _init() raises.
        deps_dir = None
        updater = None

        try:
            with perf.span("init"):
                proj_info, deps_dir, dep_sets, source_manifest = self._init(
                    dep_set, cli_overrides=cli_overrides,
                    from_manifest=from_manifest,
                    deps_dir_override=deps_dir_override)

            _logger.info("Processing root package %s", proj_info.name)

            if self.debug:
                for self.dep_set in proj_info.dep_set_m.keys():
                    _logger.debug("DepSet: %s", self.dep_set)
                    for d in proj_info.dep_set_m[self.dep_set].packages.keys():
                        _logger.debug("  Package: %s", d)

            if lock_file:
                # Reproduction mode: use lock file as the sole package source
                from .package_lock import IvpmLockReader
                note("Reproducing workspace from lock file: %s" % lock_file)
                lock_reader = IvpmLockReader(lock_file)
                ds = lock_reader.build_packages_info()
            else:
                with perf.span("depset.resolve"):
                    dep_sets, ds = self._getDepSets(proj_info, dep_sets)

                # Change detection: compare current specs against existing lock
                if not refresh_all and not force:
                    with perf.span("lock.detect_changes"):
                        diffs = check_lock_changes(deps_dir, ds.packages)
                    if diffs:
                        note("The following packages have changed specs vs package-lock.json:")
                        for name, diff in diffs.items():
                            note("  %s: run with --refresh-all to re-fetch" % name)
                        note("No packages re-fetched. Use --refresh-all to update.")

            pkg_handler = PackageHandlerRgy.inst().mkHandler()
            updater = PackageUpdater(deps_dir, pkg_handler, args=args)

            # Plumb root-project info onto the updater's update_info (the
            # instance packages receive) so the session cache provider knows
            # who is making the references.
            updater.update_info.project_name    = proj_info.name
            updater.update_info.project_version = proj_info.version
            updater.update_info.project_dir     = self.root_dir
            updater.update_info.disable_cache   = getattr(args, "no_cache", False)
            updater.update_info.perf            = perf
            # Construct the session cache provider eagerly, before parallel
            # package loads, so the memoized getter never races.
            updater.update_info.get_cache_provider()

            # Configure deps-source on update_info (if any was requested, or
            # auto-detected from a parent git worktree)
            self._configure_deps_source(updater.update_info, args, proj_info)

            # Configure event dispatcher on update_info
            updater.update_info.event_dispatcher = event_dispatcher
            
            # Suppress subprocess output when using Rich TUI
            updater.update_info.suppress_output = suppress_output
            updater.update_info._tui_ref = tui

            # Load existing lock data so packages can detect spec changes
            _lock_path = os.path.join(deps_dir, "package-lock.json")
            if os.path.isfile(_lock_path):
                try:
                    from .package_lock import read_lock as _read_lock
                    updater.update_info.lock_data = _read_lock(_lock_path)
                except Exception:
                    _logger.debug("Could not read lock file for change detection")

            # Build the handler update_info (with dispatcher wired in)
            handler_update_info = ProjectUpdateInfo(
                args, deps_dir,
                project_dir=self.root_dir,
                project_name=proj_info.name,
                force_py_install=force_py_install,
                skip_venv=skip_venv,
                suppress_output=suppress_output,
                event_dispatcher=event_dispatcher,
                python_config=proj_info.python_config,
                node_config=proj_info.node_config,
                env_settings=proj_info.env_settings,
            )
            handler_update_info.handler_configs = proj_info.handler_configs
            handler_update_info._tui_ref = tui
            handler_update_info.perf = perf

            # Load previous handler state from ivpm.json (for stale-entry cleanup)
            _ivpm_json_path = os.path.join(deps_dir, "ivpm.json")
            _prev_ivpm = {}
            if os.path.isfile(_ivpm_json_path):
                try:
                    with open(_ivpm_json_path) as _fp:
                        _prev_ivpm = json.load(_fp)
                except Exception:
                    pass
            handler_update_info.handler_state = _prev_ivpm.get("handlers", {})

            # Root pre-load: let handlers initialise before any packages are fetched
            with perf.span("handler.pre_load"):
                pkg_handler.on_root_pre_load(handler_update_info)

            # Prevent an attempt to load the top-level project as a depedency
            updater.all_pkgs[proj_info.name] = None
            # The fetch phase runs packages on worker threads; hand its span_id
            # to update_info so per-package spans parent onto it across the
            # executor boundary.
            with perf.span("fetch") as fetch_span:
                updater.update_info._fetch_parent_id = fetch_span.span_id
                pkgs_info = updater.update(ds)

            _logger.debug("Setup-deps: %s", str(pkgs_info.setup_deps))

            # Root post-load: handlers do their main work (venv, pip install, envrc, etc.)
            with perf.span("handler.post_load"):
                pkg_handler.on_root_post_load(handler_update_info)

            # Signal update complete
            updater.update_info.update_complete()

            # Write package-lock.json with resolved package versions. When the
            # workspace was driven by --from, record where it came from (and
            # which dep-set(s) were installed) so the workspace is
            # self-describing without a local ivpm.yaml.
            if source_manifest is not None:
                if dep_sets is not None and len(dep_sets) > 1:
                    source_manifest["dep_sets"] = list(dep_sets)
                elif dep_sets:
                    source_manifest["dep_set"] = dep_sets[0]
            with perf.span("lock.write"):
                handler_contributions = pkg_handler.get_lock_entries(deps_dir)
                write_lock(deps_dir, updater.all_pkgs, handler_contributions,
                           source_manifest=source_manifest)

                # Write ivpm.json with dep-set(s) and handler state. "dep-set"
                # always names the primary set (back-compat); "dep-sets" records
                # the full list when more than one was installed.
                ivpm_json = {"dep-set": dep_sets[0] if dep_sets else None}
                if dep_sets is not None and len(dep_sets) > 1:
                    ivpm_json["dep-sets"] = list(dep_sets)
                if proj_info.resolved_vars:
                    ivpm_json["vars"] = proj_info.resolved_vars
                state_contributions = pkg_handler.get_state_entries()
                if state_contributions:
                    ivpm_json["handlers"] = state_contributions
                with open(os.path.join(deps_dir, "ivpm.json"), "w") as fp:
                    json.dump(ivpm_json, fp)
        finally:
            # Close the root span so total wall-clock is recorded even on an
            # exception.
            perf.close_span(root_span)
            # Ensure TUI is stopped on exception
            if isinstance(tui, RichUpdateTUI):
                tui.stop()
            # After the TUI has torn down: persist the record and, with
            # --timing, print the breakdown (so it survives the TUI). Best
            # effort -- perf logging must never break an update.
            try:
                self._persist_and_report(perf, root_span, deps_dir, updater, timing)
            except Exception:
                _logger.debug("perf persist/report failed", exc_info=True)

    def _persist_and_report(self, perf, root_span, deps_dir, updater, timing):
        """Persist the perf record under deps/.ivpm/, prune to the retention
        limit, and print the breakdown when --timing is set."""
        if deps_dir is None:
            return
        from .perf import write_record, prune_perf_dir
        mp = getattr(getattr(updater, "update_info", None), "max_parallel", None)
        header = {
            "runid": perf.runid(),
            "total_wall": root_span.duration,
            "max_parallel": mp,
        }
        record = perf.to_json(header=header)
        write_record(deps_dir, record, perf.runid())
        # Retention: keep the newest N (IVPM_PERF_KEEP override; default 20).
        try:
            keep = int(os.environ.get("IVPM_PERF_KEEP", "20"))
        except ValueError:
            keep = 20
        prune_perf_dir(deps_dir, keep)
        if timing:
            from .perf_report import render
            print(render(record))

    def build(self, dep_set : str = None, args = None, debug : bool = False):
        proj_info, deps_dir, dep_sets, _ = self._init(dep_set)

        dep_set = dep_sets[0] if dep_sets else None
        dep_set, ds = self._getDepSet(proj_info, dep_set)

        pkg_handler = PackageHandlerRgy.inst().mkHandler()
        updater = PackageUpdater(deps_dir, pkg_handler, args=args, load=False)

        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info = updater.update(ds)

        # Now, run the actual build operation
        build_info = ProjectBuildInfo(args, deps_dir, debug=debug)

        pkg_handler.build(build_info)

        pass

    def status(self, dep_set: str = None, args=None):
        from .proj_info import ProjInfo
        from .pkg_status import PkgVcsStatus
        from .project_ops_info import ProjectStatusInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock

        proj_info = ProjInfo.mkFromProj(self.root_dir)
        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
        lock_path = os.path.join(deps_dir, "package-lock.json")

        if not os.path.isfile(lock_path):
            fatal("package-lock.json not found in %s — run 'ivpm update' first" % deps_dir)

        lock = read_lock(lock_path)
        packages = lock.get("packages", {})

        status_info = ProjectStatusInfo(args=args, deps_dir=deps_dir)
        rgy = PkgTypeRgy.inst()

        results = []
        for name, entry in packages.items():
            src = entry.get("src", "")
            if not rgy.hasPkgType(src):
                results.append(PkgVcsStatus(
                    name=name,
                    src_type=src,
                    path=os.path.join(deps_dir, name),
                    vcs="none",
                    from_deps_source=entry.get("from_deps_source"),
                ))
                continue

            pkg = rgy.mkPackage(src, name, entry, None)
            pkg.path = os.path.join(deps_dir, name)
            result = pkg.status(status_info)
            if result is None:
                result = PkgVcsStatus(
                    name=name,
                    src_type=src,
                    path=pkg.path,
                    vcs="none",
                )
            if entry.get("from_deps_source"):
                result.from_deps_source = entry["from_deps_source"]
            results.append(result)

        # Recompute (do not persist) whether each deps-source package resolves
        # into the current main git worktree, so status can mark those as
        # "(auto: worktree)" vs a user-requested "(deps-source)".
        self._mark_worktree_provenance(results, proj_info.deps_dir)

        root_status = self._compute_root_status(lock)

        return root_status, sorted(results, key=lambda r: r.name)

    def _compute_root_status(self, lock):
        """Describe the root project, or return None to omit it (design §6).

        The provider is taken from the lock's recorded ``root`` block when
        present, else discovered by probing installed clone providers.  Never
        raises: any failure yields None so the dependency table still renders.
        """
        from .clone.clone_provider_rgy import CloneProviderRgy

        recorded = (lock.get("root") or {}).get("provider")
        provider = CloneProviderRgy.inst().resolve_root(self.root_dir, recorded)
        if provider is None:
            return None
        try:
            st = provider.root_status(self.root_dir)
        except Exception as e:
            _logger.debug("root_status(%s) failed: %s", self.root_dir, e)
            return None
        if st is not None:
            st.is_root = True
            st.provider = provider.provider_info().name
            if not st.name:
                st.name = "(root)"
        return st

    def _mark_worktree_provenance(self, results, deps_dir_name):
        """Set ``deps_source_auto`` on any result whose ``from_deps_source``
        resolves under the main git worktree's deps-dir."""
        if not any(getattr(r, "from_deps_source", None) for r in results):
            return
        from .git_worktree import detect_main_worktree
        main = detect_main_worktree(self.root_dir)
        if main is None:
            return
        main_deps = os.path.realpath(os.path.join(main, deps_dir_name))
        for r in results:
            src = getattr(r, "from_deps_source", None)
            if not src:
                continue
            real = os.path.realpath(src)
            if real == main_deps or real.startswith(main_deps + os.sep):
                r.deps_source_auto = True

    # ====================================================================== #
    # destroy — tear down a workspace (root + imports) or just the imports.   #
    # See destroy-design.md. The gate is delegated (each source classifies    #
    # ITSELF via removal_safety()); teardown is delegated (remove()); this    #
    # method only aggregates and sequences. It returns structured data — the  #
    # front-end (CmdDestroy) owns all presentation and the confirm prompt.    #
    # ====================================================================== #

    def _destroy_context(self, args):
        """Resolve + validate the target and build the package list from the
        lock (the authoritative record of what is on disk). Shared by
        destroy_plan() and destroy_apply() so both gate identically."""
        from .proj_info import ProjInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock
        from .project_ops_info import ProjectRemoveInfo

        import multiprocessing

        deps_only = bool(getattr(args, "deps_only", False)) if args else False
        force     = bool(getattr(args, "force", False))     if args else False
        dry_run   = bool(getattr(args, "dry_run", False))   if args else False
        keep_venv = bool(getattr(args, "keep_venv", False)) if args else False
        jobs      = int(getattr(args, "jobs", 0) or 0)      if args else 0
        if jobs <= 0:
            jobs = multiprocessing.cpu_count()

        target = os.path.realpath(self.root_dir)
        if not os.path.isdir(target):
            fatal("destroy target does not exist: %s" % target)

        proj_info = ProjInfo.mkFromProj(target)
        deps_dir_name = proj_info.deps_dir if proj_info is not None else "packages"
        deps_dir = os.path.join(target, deps_dir_name)
        lock_path = os.path.join(deps_dir, "package-lock.json")
        has_yaml = os.path.isfile(os.path.join(target, "ivpm.yaml"))
        has_lock = os.path.isfile(lock_path)

        # Refusal 1: not an IVPM workspace.
        if not has_yaml and not has_lock:
            fatal("%s is not an IVPM workspace (no ivpm.yaml or "
                  "package-lock.json); refusing to destroy" % target)

        mode = "deps-only" if deps_only else "full"

        # Refusal 2: full destroy of cwd or an ancestor of cwd.
        if mode == "full":
            cwd = os.path.realpath(os.getcwd())
            if target == cwd or (cwd + os.sep).startswith(target + os.sep):
                fatal("refusing to destroy %s: it is the current directory or "
                      "an ancestor of it.\n"
                      "cd elsewhere, or use --deps-only to remove just the "
                      "imports." % target)

        # Build the package list from the lock (includes transitive deps).
        packages = []
        if has_lock:
            lock = read_lock(lock_path)
            rgy = PkgTypeRgy.inst()
            for name, entry in lock.get("packages", {}).items():
                src = entry.get("src", "")
                # Normalize http-archive aliases the same way sync() does.
                if src in ("tgz", "txz", "zip", "jar", "http"):
                    src = "url"
                if rgy.hasPkgType(src):
                    pkg = rgy.mkPackage(src, name, entry, None)
                else:
                    pkg = Package(name)
                    pkg.src_type = src or "non-vcs"
                pkg.path = os.path.join(deps_dir, name)
                if entry.get("from_deps_source"):
                    pkg.from_deps_source = entry["from_deps_source"]
                packages.append(pkg)

        remove_info = ProjectRemoveInfo(
            args=args, deps_dir=deps_dir,
            dry_run=dry_run, force=force, deps_only=deps_only,
            keep_venv=keep_venv,
            progress=getattr(args, "_destroy_progress", None) if args else None)

        return dict(
            proj_info=proj_info, target=target, deps_dir=deps_dir,
            deps_dir_name=deps_dir_name, packages=packages,
            remove_info=remove_info, mode=mode, force=force,
            dry_run=dry_run, has_lock=has_lock, jobs=jobs)

    @staticmethod
    def _parallel_map(items, fn, max_workers, on_start=None, on_done=None):
        """Run ``fn(item)`` across a thread pool, returning results in input
        order. ``on_start(item)`` fires (in the worker) when an item begins and
        ``on_done(item, result)`` when it finishes — both for live progress.
        ``fn`` must not raise (wrap its own errors into a result), so a single
        failure never poisons the batch. Falls back to a sequential pass when
        there is nothing to gain from threads."""
        import concurrent.futures as cf

        n = len(items)
        results = [None] * n

        if max_workers <= 1 or n <= 1:
            for i, it in enumerate(items):
                if on_start:
                    on_start(it)
                results[i] = fn(it)
                if on_done:
                    on_done(it, results[i])
            return results

        def _work(i, it):
            if on_start:
                on_start(it)
            return i, fn(it)

        with cf.ThreadPoolExecutor(max_workers=min(max_workers, n)) as ex:
            futs = [ex.submit(_work, i, it) for i, it in enumerate(items)]
            for fut in cf.as_completed(futs):
                i, res = fut.result()
                results[i] = res
                if on_done:
                    on_done(items[i], res)
        return results

    def _destroy_root_pkg(self, ctx):
        """The Package used to gate the root project in full mode. A git root
        carries unpushed commits like any dep, so it must pass the same gate."""
        from .pkg_types.package_git import PackageGit
        target = ctx["target"]
        if os.path.isdir(os.path.join(target, ".git")):
            pkg = PackageGit(name="<root>")
        else:
            pkg = Package("<root>")
            pkg.src_type = "dir"
        pkg.path = target
        return pkg

    def _destroy_gate(self, ctx, progress=None):
        """Call removal_safety() on every package (and, in full mode, the root),
        in parallel. Returns (gate dict, blocked bool). destroy only aggregates;
        each source classifies itself. When ``progress`` is given, emits
        on_gate_start/on_gate_result per package for the live display."""
        from .pkg_remove import (RemovalSafety, SafetyLevel, SafetyReason)

        pkgs = list(ctx["packages"])
        if ctx["mode"] == "full":
            pkgs.append(self._destroy_root_pkg(ctx))

        remove_info = ctx["remove_info"]

        def _fn(pkg):
            try:
                return pkg.removal_safety(remove_info)
            except Exception as e:
                # A crashing gate must not silently allow deletion: block it
                # (the user can still --force). Listed with the error.
                return RemovalSafety(SafetyLevel.BLOCKED,
                    [SafetyReason("gate-error", label=str(e))])

        on_start = (lambda p: progress.on_gate_start(p.name)) if progress else None
        on_done = ((lambda p, r: progress.on_gate_result(p.name, r))
                   if progress else None)

        verdicts = self._parallel_map(pkgs, _fn, ctx["jobs"], on_start, on_done)

        gate = {pkg.name: v for pkg, v in zip(pkgs, verdicts)}
        blocked = (not ctx["force"]) and any(
            v.level == SafetyLevel.BLOCKED for v in gate.values())
        return gate, blocked

    def destroy_plan(self, args=None):
        """Read-only: resolve, validate, gate, and (for --dry-run) compute the
        planned per-package teardown. Mutates nothing. Returns a DestroyReport
        the front-end renders (blocking report or dry-run plan)."""
        from .pkg_remove import DestroyReport
        ctx = self._destroy_context(args)
        progress = getattr(ctx["remove_info"], "progress", None)
        gate, blocked = self._destroy_gate(ctx, progress=progress)
        report = DestroyReport(
            mode=ctx["mode"], target=ctx["target"], deps_dir=ctx["deps_dir"],
            gate=gate, blocked=blocked, dry_run=ctx["dry_run"],
            forced=ctx["force"])
        if blocked:
            return report
        if ctx["dry_run"]:
            # Planned teardown (no mutation); cheap stats, kept sequential.
            report.results = [p.remove(ctx["remove_info"])
                              for p in ctx["packages"]]
        return report

    def destroy_apply(self, args=None):
        """Mutating: re-gate (never mutate when blocked), then tear down each
        package (best-effort-continue), run handler on_destroy(), remove
        lock/state, and finally remove deps_dir / the root tree (full mode).
        Returns the final DestroyReport for the summary."""
        from .pkg_remove import DestroyReport, RemoveOutcome, PkgRemoveResult
        from .package import _rmtree_force

        ctx = self._destroy_context(args)
        # Re-gate SILENTLY (no progress): this is a TOCTOU safety re-check
        # between plan/confirm and mutation; the visible gate ran in the plan.
        gate, blocked = self._destroy_gate(ctx, progress=None)
        report = DestroyReport(
            mode=ctx["mode"], target=ctx["target"], deps_dir=ctx["deps_dir"],
            gate=gate, blocked=blocked, dry_run=False, forced=ctx["force"])
        if blocked:
            return report      # safety: never mutate while the gate blocks

        remove_info = ctx["remove_info"]
        progress = getattr(remove_info, "progress", None)

        # Teardown each package IN PARALLEL — one provider error does not abort
        # the others. Per-package remove() is independent today; the deps_dir /
        # root rmtree below is the barrier that runs after all packages.
        # NOTE: when worktree/submodule teardown overrides land (post-MVP), a
        # child must be removed before its parent — at that point this map needs
        # leaf-first ordering, not a flat parallel sweep.
        def _fn(pkg):
            try:
                return pkg.remove(remove_info)
            except Exception as e:
                return PkgRemoveResult(
                    name=pkg.name,
                    src_type=str(getattr(pkg, "src_type", "") or "non-vcs"),
                    path=pkg.path, removal="error",
                    outcome=RemoveOutcome.MANUAL, error=str(e))

        on_start = (lambda p: progress.on_remove_start(p.name)) if progress else None
        on_done = ((lambda p, r: progress.on_remove_result(r))
                   if progress else None)
        report.results = self._parallel_map(
            ctx["packages"], _fn, ctx["jobs"], on_start, on_done)

        # Handler teardown (venv, node_modules, ...).
        try:
            handler = PackageHandlerRgy.inst().mkHandler()
            handler.on_destroy(remove_info)
        except Exception as e:
            warning("handler teardown error: %s" % e)

        # Remove lock/state under deps_dir.
        for fname in ("package-lock.json", "ivpm.json"):
            p = os.path.join(ctx["deps_dir"], fname)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError as e:
                    warning("could not remove %s: %s" % (p, e))

        if ctx["mode"] == "deps-only":
            # Leave the (emptied) deps_dir; prune only if it is now empty.
            try:
                if os.path.isdir(ctx["deps_dir"]) and not os.listdir(ctx["deps_dir"]):
                    os.rmdir(ctx["deps_dir"])
            except OSError:
                pass
        else:
            # full: remove deps_dir, then the ROOT tree LAST (it can be a
            # worktree/submodule parent and can carry unpushed commits of its
            # own — already gated above).
            if os.path.isdir(ctx["deps_dir"]):
                _rmtree_force(ctx["deps_dir"])
            if os.path.isdir(ctx["target"]):
                _rmtree_force(ctx["target"])

        return report

    def sync(self, dep_set: str = None, args=None):
        import asyncio
        import multiprocessing
        from .pkg_sync import PkgSyncResult, SyncOutcome
        from .project_ops_info import ProjectSyncInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock, patch_lock_after_sync
        from .proj_info import ProjInfo

        proj_info = ProjInfo.mkFromProj(self.root_dir)
        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
        lock_path = os.path.join(deps_dir, "package-lock.json")

        if not os.path.isfile(lock_path):
            fatal("package-lock.json not found in %s — run 'ivpm update' first" % deps_dir)

        lock = read_lock(lock_path)
        packages = lock.get("packages", {})

        dry_run          = getattr(args, "dry_run",          False) if args is not None else False
        packages_filter  = getattr(args, "packages_filter",  None)  if args is not None else None
        max_parallel     = getattr(args, "jobs",             0)     if args is not None else 0
        progress         = getattr(args, "_sync_progress",   None)  if args is not None else None

        sync_info = ProjectSyncInfo(
            args=args,
            deps_dir=deps_dir,
            dry_run=dry_run,
            packages_filter=packages_filter,
            max_parallel=max_parallel or 0,
            progress=progress,
        )
        rgy = PkgTypeRgy.inst()

        # Filter the package list once.
        pkg_items = [
            (name, entry) for name, entry in packages.items()
            if not packages_filter or name in packages_filter
        ]

        if not pkg_items:
            return []

        # ── Parallel execution ─────────────────────────────────────────────
        n_workers = sync_info.max_parallel or multiprocessing.cpu_count()

        async def _run_all():
            semaphore = asyncio.Semaphore(n_workers)
            loop = asyncio.get_event_loop()

            async def _run_one(name, entry):
                async with semaphore:
                    if progress:
                        progress.on_pkg_start(name)
                    src = entry.get("src", "")
                    # Normalize src aliases that appear in lock files but aren't
                    # registered directly (http archive types → "url").
                    if src in ("tgz", "txz", "zip", "jar", "http"):
                        src = "url"
                    # Packages materialized from a deps-source are read-only mirrors
                    # of someone else's tree — there is nothing to sync.
                    if entry.get("from_deps_source"):
                        result = PkgSyncResult(
                            name=name, src_type=src,
                            path=os.path.join(deps_dir, name),
                            outcome=SyncOutcome.SKIPPED,
                            skipped_reason="materialized from deps-source %s" %
                                entry["from_deps_source"],
                            next_steps=[
                                "Re-run 'ivpm update' without --deps-source, "
                                "or point at a refreshed parent.",
                            ],
                        )
                    elif not rgy.hasPkgType(src):
                        result = PkgSyncResult(
                            name=name, src_type=src,
                            path=os.path.join(deps_dir, name),
                            outcome=SyncOutcome.SKIPPED,
                            skipped_reason=src or "non-git",
                        )
                    else:
                        pkg = rgy.mkPackage(src, name, entry, None)
                        pkg.path = os.path.join(deps_dir, name)
                        result = await loop.run_in_executor(
                            None, pkg.sync, sync_info
                        )
                    if progress:
                        progress.on_pkg_result(result)
                    return result

            return await asyncio.gather(
                *[_run_one(n, e) for n, e in pkg_items],
                return_exceptions=True,
            )

        raw = asyncio.run(_run_all())

        results = []
        for i, r in enumerate(raw):
            if isinstance(r, Exception):
                name = pkg_items[i][0]
                entry = pkg_items[i][1]
                err_result = PkgSyncResult(
                    name=name,
                    src_type=entry.get("src", ""),
                    path=os.path.join(deps_dir, name),
                    outcome=SyncOutcome.ERROR,
                    error=str(r),
                )
                if progress:
                    progress.on_pkg_result(err_result)
                results.append(err_result)
            else:
                results.append(r)

        if not dry_run:
            patch_lock_after_sync(lock_path, results)

        return sorted(results, key=lambda r: r.name)

    def _configure_deps_source(self, update_info, args, proj_info=None):
        """Build a DepsSource from --deps-source flags / IVPM_DEPS_SOURCE env
        and attach it (and the requested materialization mode) to update_info.

        When neither is supplied and ivpm is running inside a linked git
        worktree, the main worktree's deps-dir is auto-configured as a
        (lock-verified) deps-source so unchanged packages resolve from local
        disk instead of the network. Auto-detection can be disabled with
        --no-worktree-deps-source.
        """
        from .deps_source import DepsSource

        paths = list(getattr(args, "deps_source", None) or [])
        if not paths:
            env = os.environ.get("IVPM_DEPS_SOURCE", "")
            if env:
                paths = [p for p in env.split(os.pathsep) if p]

        # Auto-detect a parent git worktree only when the user supplied no
        # explicit deps-source. Explicit sources always win.
        auto = False
        if not paths and not getattr(args, "no_worktree_deps_source", False) \
                and proj_info is not None:
            from .git_worktree import detect_main_worktree
            main = detect_main_worktree(self.root_dir)
            if main is not None:
                cand = os.path.join(main, proj_info.deps_dir)
                if os.path.isdir(cand):
                    paths = [cand]
                    auto = True
                    note("git worktree detected: sourcing unchanged packages "
                         "from main worktree (%s)" % cand)
                    if not os.path.isfile(os.path.join(cand, "package-lock.json")):
                        note("  main worktree has no package-lock.json; packages "
                             "will be re-fetched (run 'ivpm update' there first "
                             "to enable reuse)")

        if not paths:
            return

        for p in paths:
            if not os.path.isdir(p):
                fatal("--deps-source path is not a directory: %s" % p)

        # Auto-detected worktree sources are lock-verified (never trusted by
        # name): a divergent branch dependency must fall through to a fetch.
        trust = bool(getattr(args, "trust_deps_source", False))
        mode = getattr(args, "deps_source_mode", None) or "link"
        update_info.deps_source = DepsSource.from_args(paths, trust=trust)
        update_info.deps_source_mode = mode
        update_info.deps_source_auto = auto

    def _init(self, dep_set=None, cli_overrides=None,
              from_manifest : str = None,
              deps_dir_override : str = None) -> Tuple['ProjInfo', str, List[str], 'dict']:
        from .proj_info import ProjInfo

        # Normalize the requested dep-set(s) to a list (or None for "default").
        # Callers may pass a single name (build), a list (update), or None.
        if dep_set is None:
            req_dep_sets = None
        elif isinstance(dep_set, str):
            req_dep_sets = [dep_set]
        else:
            req_dep_sets = list(dep_set)

        # The directory used to look up persisted state precedes knowing the
        # manifest's own deps-dir; honor an explicit --deps-dir, else "packages".
        _pre_deps_dir_name = deps_dir_override or "packages"

        source_manifest = None

        if from_manifest is not None:
            # A workspace has exactly one driving manifest — refuse to shadow a
            # local ivpm.yaml (see remote-manifest-design.md "Persistence").
            from .ivpm_yaml_reader import IvpmYamlReader
            from .remote import fetch_manifest

            if os.path.isfile(os.path.join(self.root_dir, "ivpm.yaml")):
                fatal("Cannot use --from here: %s already has an ivpm.yaml. "
                      "Run in another directory, or edit that manifest to "
                      "reference the external source (%s)." % (
                          self.root_dir, from_manifest))

            fetched = fetch_manifest(from_manifest)
            try:
                with open(fetched.local_path) as fp:
                    proj_info = IvpmYamlReader().read(
                        fp, fetched.local_path,
                        cli_overrides=cli_overrides,
                        persisted_vars={},      # external manifest: start clean
                        allow_include=not fetched.is_remote)
            finally:
                fetched.cleanup()
            source_manifest = {"from": fetched.origin}
        else:
            # Load persisted variables from a previous run
            persisted_vars = {}
            _pre_deps_dir = os.path.join(self.root_dir, _pre_deps_dir_name)
            _pre_ivpm_json_path = os.path.join(_pre_deps_dir, "ivpm.json")
            if os.path.isfile(_pre_ivpm_json_path):
                try:
                    with open(_pre_ivpm_json_path) as _fp:
                        _pre_ivpm = json.load(_fp)
                        persisted_vars = _pre_ivpm.get("vars", {})
                except Exception:
                    pass

            proj_info = ProjInfo.mkFromProj(
                self.root_dir,
                cli_overrides=cli_overrides,
                persisted_vars=persisted_vars)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        # Precedence: --deps-dir (CLI) > manifest deps-dir > "packages"
        deps_dir_name = deps_dir_override or proj_info.deps_dir
        deps_dir = os.path.join(self.root_dir, deps_dir_name)

        ivpm_json = {}

        if os.path.isfile(os.path.join(deps_dir, "ivpm.json")):
            with open(os.path.join(deps_dir, "ivpm.json"), "r") as fp:
                try:
                    ivpm_json = json.load(fp)
                except Exception as e:
                    warning("failed to read ivpm.json: %s" % str(e))

        # Recover the dep-set(s) recorded by a previous run. "dep-sets" (list)
        # takes precedence over the legacy single "dep-set" key.
        persisted_dep_sets = None
        if "dep-sets" in ivpm_json.keys():
            persisted_dep_sets = list(ivpm_json["dep-sets"])
        elif ivpm_json.get("dep-set") is not None:
            persisted_dep_sets = [ivpm_json["dep-set"]]

        if persisted_dep_sets is not None:
            if req_dep_sets is None:
                req_dep_sets = persisted_dep_sets
            elif set(req_dep_sets) != set(persisted_dep_sets):
                fatal("Attempting to update with a different dep-set than previously used")

        return (proj_info, deps_dir, req_dep_sets, source_manifest)
    
    def _getDepSet(self, proj_info, dep_set):
        if dep_set is None:
            # Priority: 1) default-dep-set setting, 2) first dep-set in file
            if proj_info.default_dep_set is not None:
                dep_set = proj_info.default_dep_set
            elif len(proj_info.dep_set_m.keys()) > 0:
                dep_set = list(proj_info.dep_set_m.keys())[0]
            else:
                fatal("No dependency sets defined in project")

        if dep_set not in proj_info.dep_set_m.keys():
            raise Exception("Dep-set %s is not present" % dep_set)
        else:
            ds = proj_info.dep_set_m[dep_set]

        return dep_set, ds

    def _getDepSets(self, proj_info, dep_sets):
        """Resolve one or more requested dep-sets into a single PackagesInfo.

        Returns ``(names, ds)`` where *names* is the ordered list of resolved
        dep-set names and *ds* is the dep-set to install. With a single
        selection (or the default) the manifest's own PackagesInfo is returned
        unchanged; with several, their packages are merged into one.
        """
        from .packages_info import PackagesInfo

        if not dep_sets:
            name, ds = self._getDepSet(proj_info, None)
            return [name], ds

        if len(dep_sets) == 1:
            name, ds = self._getDepSet(proj_info, dep_sets[0])
            return [name], ds

        merged = PackagesInfo("+".join(dep_sets))
        for name in dep_sets:
            _, ds = self._getDepSet(proj_info, name)
            # Later dep-sets win on name collisions; a shared package pulled by
            # more than one set is installed once.
            merged.packages.update(ds.packages)
            merged.setup_deps.update(ds.setup_deps)
            merged.options.update(ds.options)

        return list(dep_sets), merged
