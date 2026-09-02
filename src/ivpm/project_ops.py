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
from .utils import fatal, info, note, warning
from .package_lock import write_lock, _entry_name

_logger = logging.getLogger("ivpm.project_ops")


# Lock fields worth naming in the drift report, in the order a reader wants
# them. 'commit_requested' is the one the original bug report was about: a
# changed pin has to say what it changed *from*, or the note is unactionable.
_DRIFT_FIELDS = (
    ("url", "url"),
    ("branch", "branch"),
    ("tag", "tag"),
    ("commit_requested", "commit"),
    ("version_requested", "version"),
    ("path", "path"),
    ("patchset_id", "patches"),
)


def _abbrev(value) -> str:
    """Commit hashes are unreadable at full length; everything else is not."""
    text = "(none)" if value is None else str(value)
    if len(text) == 40 and all(c in "0123456789abcdef" for c in text):
        return text[:7]
    return text


def _describe_drift(drift: dict) -> str:
    """`` (commit 2c2094c -> dfaad41)`` for a drift record, or '' if nothing
    nameable changed (an extension source comparing on fields we don't know)."""
    if not drift:
        return ""
    current = drift.get("current") or {}
    locked = drift.get("locked") or {}
    changes = [
        "%s %s -> %s" % (label, _abbrev(locked.get(key)), _abbrev(current.get(key)))
        for key, label in _DRIFT_FIELDS
        if key in current and current.get(key) != locked.get(key)
    ]
    return " (%s)" % ", ".join(changes) if changes else ""


def _apply_effective_settings(infos, **settings) -> None:
    """Apply the resolved project settings to every update-info in *infos*.

    An update runs with two ProjectUpdateInfo instances -- the updater's (seen
    by leaf callbacks and source providers, via a scope view) and the handler's
    (seen by the root-phase callbacks). The settings resolved from the project's
    'with:' block belong on both; routing them through one call is what keeps
    the two from drifting apart.
    """
    for info in infos:
        for key, value in settings.items():
            setattr(info, key, value)


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
               default_config : dict = None,
               handler_overlay : dict = None,
               timing : bool = False,
               # -- 'ivpm install' (tool-directory) inputs --------------------
               # A pre-merged root synthesized from several --from sources.
               # When given, no manifest is read: it *is* the manifest.
               merged_proj_info = None,
               # TOOLCHAIN when the deps-dir is the root. Threaded to handlers
               # so project-scoped output is suppressed.
               install_mode = None,
               # The 'install' block recorded in the lock (sources, mode,
               # collision resolutions) so a bare re-run can replay it.
               install_record : dict = None,
               # Name of the variable packages.envrc exports for the deps-dir
               # (None -> just IVPM_PACKAGES).
               root_var : str = None):
        from .update_event import UpdateEvent, UpdateEventType
        from .perf import PerfCollector
        from .project_ops_info import InstallMode

        if install_mode is None:
            install_mode = InstallMode.WORKSPACE

        if from_manifest is not None and lock_file is not None:
            fatal("--from and --lock-file are mutually exclusive "
                  "(both specify an alternate manifest source)")

        # Bare workspace (no ivpm.yaml): if the caller supplied no config,
        # reproduce the provider's forwarded config from the lock's root record
        # so `ivpm update` can refresh a workspace created by `ivpm clone`.
        default_config, handler_overlay = self._reproduce_bare_config(
            default_config, handler_overlay, from_manifest, lock_file)

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
                    deps_dir_override=deps_dir_override,
                    default_config=default_config,
                    merged_proj_info=merged_proj_info)

            # Merge any clone-provided handler overlay into the effective
            # handler_configs before the handler update-info is built from them
            # below.
            if handler_overlay:
                self._apply_handler_overlay(proj_info, handler_overlay)

            _logger.info("Processing root package %s", proj_info.name)

            if self.debug:
                for self.dep_set in proj_info.dep_set_m.keys():
                    _logger.debug("DepSet: %s", self.dep_set)
                    for d in proj_info.dep_set_m[self.dep_set].packages.keys():
                        _logger.debug("  Package: %s", d)

            scope_pins = {}
            if lock_file:
                # Reproduction mode: use lock file as the sole package source
                from .package_lock import IvpmLockReader
                note("Reproducing workspace from lock file: %s" % lock_file)
                lock_reader = IvpmLockReader(lock_file)
                ds = lock_reader.build_packages_info()
                # Nested entries describe a sub-tree the boundary's own
                # manifest rebuilds; carry them as version pins so the rebuilt
                # tree lands on the locked identities.
                scope_pins = lock_reader.scope_pins()
            else:
                with perf.span("depset.resolve"):
                    dep_sets, ds = self._getDepSets(proj_info, dep_sets)

                # Spec-vs-lock drift used to be detected here, before resolution,
                # against the root dep-set only -- so drift in a transitive
                # dependency was never reported at all. The load planner now
                # classifies every package in every scope as it is decided; the
                # report is emitted after the fetch phase (see below).

            pkg_handler = PackageHandlerRgy.inst().mkHandler()
            updater = PackageUpdater(deps_dir, pkg_handler, args=args,
                                     deps_mode=proj_info.deps_mode)
            updater.scope_pins = scope_pins

            # Plumb root-project info onto the updater's update_info (the
            # instance packages receive) so the session cache provider knows
            # who is making the references.
            updater.update_info.project_name    = proj_info.name
            updater.update_info.project_version = proj_info.version
            updater.update_info.project_dir     = self.root_dir
            updater.update_info.install_mode    = install_mode
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

            # --force implies --refresh-all: it exists to suppress the safety
            # errors a refresh raises, which is meaningless without one.
            updater.update_info.refresh_all = bool(refresh_all or force)
            updater.update_info.force = bool(force)

            # Build the load planner now that lock_data is populated and before
            # any parallel package load, so its memo never races. It decides,
            # per package, whether the package needs loading -- replacing the
            # per-provider "does the directory exist" checks.
            updater.update_info.get_load_planner()

            # Compute the effective handler config for this update: the
            # package-level 'with:' overlaid by the selected dep-set's own
            # 'with:' (dep-set wins). When the selected dep-set declares no
            # 'with:', the package-level parsed configs are used verbatim.
            from .ivpm_yaml_reader import resolve_effective_with
            effective_py_config, effective_node_config, \
                effective_handler_configs, effective_env = \
                    resolve_effective_with(proj_info, ds)

            # Build the handler update_info (with dispatcher wired in)
            handler_update_info = ProjectUpdateInfo(
                args, deps_dir,
                install_mode=install_mode,
                project_dir=self.root_dir,
                project_name=proj_info.name,
                force_py_install=force_py_install,
                skip_venv=skip_venv,
                suppress_output=suppress_output,
                event_dispatcher=event_dispatcher,
            )
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

            # The effective project settings go on BOTH update-info objects.
            #
            # updater.update_info is what every leaf callback and source
            # provider receives (package_updater._update_pkg passes a scope view
            # of it); handler_update_info is what the root-phase handler
            # callbacks receive. Applying the settings through one helper is
            # what stops the two from drifting: they previously landed only on
            # handler_update_info, so anything reading handler_configs at leaf
            # time silently saw {}.
            _apply_effective_settings(
                (updater.update_info, handler_update_info),
                python_config=effective_py_config,
                node_config=effective_node_config,
                handler_configs=effective_handler_configs,
                env_settings=effective_env,
                root_var=root_var,
                handler_state=_prev_ivpm.get("handlers", {}))

            # Session start for pre-populate preparers. Deliberately ahead of
            # the fetch AND of the root deps-dir being created, so a preparer
            # that rejects the whole workspace (read-only filesystem, exhausted
            # quota) does so once, before anything is written.
            with perf.span("prepare.session_start"):
                updater.preparers.on_session_start(handler_update_info)

            # Root pre-load: let handlers initialise before any packages are fetched
            with perf.span("handler.pre_load"):
                pkg_handler.on_root_pre_load(handler_update_info)

            # Prevent an attempt to load the top-level project as a depedency
            updater.exclude_root_project(proj_info.name)
            # The fetch phase runs packages on worker threads; hand its span_id
            # to update_info so per-package spans parent onto it across the
            # executor boundary.
            with perf.span("fetch") as fetch_span:
                updater.update_info._fetch_parent_id = fetch_span.span_id
                pkgs_info = updater.update(ds)

            with perf.span("prepare.session_end"):
                updater.preparers.on_session_end(handler_update_info)

            # Drift report. Emitted after the fetch because that is when every
            # package -- including transitive ones and every nested scope -- has
            # been decided. By this point each of these has been re-materialized
            # to match its new spec (a drifted package that could not be safely
            # replaced raised instead, so we never get here reporting one we
            # silently skipped). The report says what moved, and to what.
            _drifted = updater.update_info.get_load_planner().drifted()
            if _drifted:
                note("Re-fetched %d package(s) whose spec changed since "
                     "package-lock.json was written:" % len(_drifted))
                for _key in sorted(_drifted.keys()):
                    note("  %s%s" % (_key, _describe_drift(_drifted[_key])))

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
                           source_manifest=source_manifest,
                           install_record=install_record)

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
            # Render errors the TUI deferred, now that the display, its summary
            # and the timing breakdown are all out of the way -- so the reason
            # the update failed is the last thing on screen. __main__ flushes
            # too (no-op after this); doing it here as well means callers that
            # drive ProjectOps directly don't lose the diagnostics.
            if isinstance(tui, RichUpdateTUI):
                from .msg import flush_deferred_errors
                flush_deferred_errors()

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
        updater = PackageUpdater(deps_dir, pkg_handler, args=args, load=False,
                                 deps_mode=proj_info.deps_mode)

        # Prevent an attempt to load the top-level project as a depedency
        updater.exclude_root_project(proj_info.name)
        pkgs_info = updater.update(ds)

        # Now, run the actual build operation
        build_info = ProjectBuildInfo(args, deps_dir, debug=debug)

        pkg_handler.build(build_info)

        pass

    def _resolve_deps_dir(self, root_dir: str = None, walk: bool = False):
        """Resolve ``(proj_info, deps_dir)`` for read-only operations.

        With a root ``ivpm.yaml`` the deps-dir comes from the manifest (as
        before).  Otherwise, when the directory is *itself* a deps-dir (a shared
        tool directory, where the deps-dir is the root) it is used directly.
        Failing that, for a "bare" workspace (a workspace created by an ``ivpm
        clone`` provider from a source with no ``ivpm.yaml``) the deps-dir is
        discovered among the children via :func:`find_ivpm_deps_dir`.  Returns
        ``proj_info=None`` in the bare and tool-directory cases, and
        ``deps_dir=None`` when nothing is found.

        When *walk* is set and none of the above match at *root_dir*, the search
        repeats up the ancestor chain, nearest match winning.  Callers pass
        ``walk=True`` only when the starting directory was defaulted from the
        cwd -- an explicitly named directory is never resolved to an ancestor.
        """
        from .proj_info import ProjInfo
        from .package_lock import find_ivpm_deps_dir
        from .utils import is_filesystem_root

        root = os.path.abspath(root_dir if root_dir is not None else self.root_dir)

        while True:
            proj_info = ProjInfo.mkFromProj(root)
            if proj_info is not None:
                return proj_info, os.path.join(root, proj_info.deps_dir)
            deps_dir = find_ivpm_deps_dir(root)
            if deps_dir is not None:
                return None, deps_dir
            if not walk or is_filesystem_root(root):
                return None, None
            parent = os.path.dirname(root)
            if parent == "" or parent == root:
                return None, None
            root = parent

    def _load_root_config_from_lock(self):
        """Return ``(default_package, handler_overlay)`` persisted in the lock's
        ``root.config`` block, or ``None`` when unavailable.

        This is what makes a bare workspace self-describing: ``ivpm clone``
        stamps the provider's ``CloneRootConfig`` here, and ``ivpm update`` reads
        it back to reconstruct the driving config."""
        from .package_lock import find_ivpm_deps_dir, read_lock

        deps_dir = find_ivpm_deps_dir(self.root_dir)
        if deps_dir is None:
            return None
        lock_path = os.path.join(deps_dir, "package-lock.json")
        try:
            lock = read_lock(lock_path)
        except Exception as e:
            _logger.debug("could not read lock for root.config reproduce: %s", e)
            return None
        cfg = (lock.get("root") or {}).get("config")
        if not cfg:
            return None
        return cfg.get("default_package"), cfg.get("handler_overlay")

    def _reproduce_bare_config(self, default_config, handler_overlay,
                               from_manifest, lock_file):
        """Reproduce a bare workspace's driving config from the lock when needed.

        Returns the ``(default_config, handler_overlay)`` to use.  Only acts when
        the caller supplied neither, the workspace has no ``ivpm.yaml``, and we
        are not in ``--from`` / ``--lock-file`` mode; otherwise the inputs are
        returned unchanged."""
        if (default_config is not None or handler_overlay is not None
                or from_manifest is not None or lock_file is not None):
            return default_config, handler_overlay
        if os.path.isfile(os.path.join(self.root_dir, "ivpm.yaml")):
            return default_config, handler_overlay

        persisted = self._load_root_config_from_lock()
        if persisted is None:
            return default_config, handler_overlay

        dc, ho = persisted
        if dc is not None or ho is not None:
            note("Reproducing workspace configuration from package-lock.json "
                 "root record")
        return dc, ho

    def status(self, dep_set: str = None, args=None, walk: bool = False):
        from .pkg_status import PkgVcsStatus
        from .project_ops_info import ProjectStatusInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock

        proj_info, deps_dir = self._resolve_deps_dir(walk=walk)
        if deps_dir is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml) or a "
                  "package-lock.json under %s" % self.root_dir)

        lock_path = os.path.join(deps_dir, "package-lock.json")

        if not os.path.isfile(lock_path):
            fatal("package-lock.json not found in %s — run 'ivpm update' first" % deps_dir)

        lock = read_lock(lock_path)
        packages = lock.get("packages", {})

        status_info = ProjectStatusInfo(args=args, deps_dir=deps_dir)
        rgy = PkgTypeRgy.inst()

        results = []
        # Lock keys are scope paths -- a bare name in a flat workspace, else
        # the package's path relative to the deps-dir. So the key doubles as
        # the location AND as the label that keeps two same-named packages in
        # different scopes distinguishable.
        for key, entry in packages.items():
            src = entry.get("src", "")
            pkg_name = _entry_name(key, entry)
            if not rgy.hasPkgType(src):
                results.append(PkgVcsStatus(
                    name=key,
                    src_type=src,
                    path=os.path.join(deps_dir, key),
                    vcs="none",
                    from_deps_source=entry.get("from_deps_source"),
                ))
                continue

            pkg = rgy.mkPackage(src, pkg_name, entry, None)
            pkg.path = os.path.join(deps_dir, key)
            pkg.scope_key = key
            result = pkg.status(status_info)
            if result is None:
                result = PkgVcsStatus(
                    name=key,
                    src_type=src,
                    path=pkg.path,
                    vcs="none",
                )
            else:
                result.name = key
            if entry.get("from_deps_source"):
                result.from_deps_source = entry["from_deps_source"]
            results.append(result)

        # Recompute (do not persist) whether each deps-source package resolves
        # into the current main git worktree, so status can mark those as
        # "(auto: worktree)" vs a user-requested "(deps-source)".
        deps_dir_name = (proj_info.deps_dir if proj_info is not None
                         else os.path.basename(deps_dir))
        self._mark_worktree_provenance(results, deps_dir_name)

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

        # Resolve the deps-dir from the manifest, else discover it from a lock
        # (a bare workspace may use a non-default deps-dir name).
        proj_info, deps_dir = self._resolve_deps_dir(target)
        if deps_dir is None:
            deps_dir = os.path.join(target, "packages")
        deps_dir_name = os.path.basename(deps_dir)
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
            # Keyed by scope path, so the safety gate runs over every package
            # in the tree -- nested ones included -- and each pkg.path is the
            # location the key names.
            for key, entry in lock.get("packages", {}).items():
                src = entry.get("src", "")
                name = _entry_name(key, entry)
                # Normalize http-archive aliases the same way sync() does.
                if src in ("tgz", "txz", "zip", "jar", "http"):
                    src = "url"
                if rgy.hasPkgType(src):
                    pkg = rgy.mkPackage(src, name, entry, None)
                else:
                    pkg = Package(name)
                    pkg.src_type = src or "non-vcs"
                pkg.path = os.path.join(deps_dir, key)
                pkg.scope_key = key
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

        from .handlers.scope_keys import pkg_key

        on_start = (lambda p: progress.on_gate_start(pkg_key(p))) if progress else None
        on_done = ((lambda p, r: progress.on_gate_result(pkg_key(p), r))
                   if progress else None)

        verdicts = self._parallel_map(pkgs, _fn, ctx["jobs"], on_start, on_done)

        # Keyed by scope path, not bare name: two same-named packages in
        # different scopes must both appear, or a BLOCKED verdict on one of
        # them could be silently overwritten by a SAFE verdict on the other.
        gate = {pkg_key(pkg): v for pkg, v in zip(pkgs, verdicts)}
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
        # the others. The deps_dir / root rmtree below is the barrier that runs
        # after all packages.
        #
        # Ordering is LEAF-FIRST by scope depth: a nested package physically
        # lives inside its boundary's directory, so removing the two
        # concurrently races (the parent's rmtree walks a tree another thread
        # is deleting). Packages at the same depth are independent and still
        # run in parallel. A flat workspace is all one depth, so this is
        # exactly the previous single parallel sweep.
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

        from .handlers.scope_keys import pkg_key
        by_depth = {}
        for pkg in ctx["packages"]:
            by_depth.setdefault(pkg_key(pkg).count("/"), []).append(pkg)
        report.results = []
        for depth in sorted(by_depth.keys(), reverse=True):
            report.results.extend(self._parallel_map(
                by_depth[depth], _fn, ctx["jobs"], on_start, on_done))

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

    def sync(self, dep_set: str = None, args=None, walk: bool = False):
        import asyncio
        import multiprocessing
        from .pkg_sync import PkgSyncResult, SyncOutcome
        from .project_ops_info import ProjectSyncInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock, patch_lock_after_sync

        _, deps_dir = self._resolve_deps_dir(walk=walk)
        if deps_dir is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml) or a "
                  "package-lock.json under %s" % self.root_dir)

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
        # A filter may name either the bare package name or its full scope
        # path, so `--packages libA` still reaches a nested libA.
        pkg_items = [
            (key, entry) for key, entry in packages.items()
            if not packages_filter
            or key in packages_filter
            or _entry_name(key, entry) in packages_filter
        ]

        if not pkg_items:
            return []

        # ── Parallel execution ─────────────────────────────────────────────
        n_workers = sync_info.max_parallel or multiprocessing.cpu_count()

        async def _run_all():
            semaphore = asyncio.Semaphore(n_workers)
            loop = asyncio.get_event_loop()

            async def _run_one(key, entry):
                async with semaphore:
                    # The key is the scope path: the package's location under
                    # deps_dir, and the label that keeps two same-named
                    # packages distinguishable. In a flat workspace it is just
                    # the package name.
                    name = _entry_name(key, entry)
                    if progress:
                        progress.on_pkg_start(key)
                    src = entry.get("src", "")
                    # Normalize src aliases that appear in lock files but aren't
                    # registered directly (http archive types → "url").
                    if src in ("tgz", "txz", "zip", "jar", "http"):
                        src = "url"
                    # Packages materialized from a deps-source are read-only mirrors
                    # of someone else's tree — there is nothing to sync.
                    if entry.get("from_deps_source"):
                        result = PkgSyncResult(
                            name=key, src_type=src,
                            path=os.path.join(deps_dir, key),
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
                            name=key, src_type=src,
                            path=os.path.join(deps_dir, key),
                            outcome=SyncOutcome.SKIPPED,
                            skipped_reason=src or "non-git",
                        )
                    else:
                        pkg = rgy.mkPackage(src, name, entry, None)
                        pkg.path = os.path.join(deps_dir, key)
                        pkg.scope_key = key
                        result = await loop.run_in_executor(
                            None, pkg.sync, sync_info
                        )
                        if result is not None:
                            result.name = key
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

        # Announce worktree detection independently of deps-sourcing: the user
        # should always know ivpm recognized the linked worktree and where its
        # main worktree lives, even when auto-sourcing is disabled
        # (--no-worktree-deps-source) or an explicit --deps-source was supplied.
        main = None
        if proj_info is not None:
            from .git_worktree import detect_main_worktree
            main = detect_main_worktree(self.root_dir)
            if main is not None:
                # INFO so it surfaces in the TUI too: the RichSink suppresses
                # NOTE-level output during the live progress display, but always
                # renders INFO regardless of the verbosity threshold.
                info("git worktree detected\n"
                     "      main worktree: %s" % main)

        # Auto-detect a parent git worktree only when the user supplied no
        # explicit deps-source. Explicit sources always win.
        auto = False
        if not paths and not getattr(args, "no_worktree_deps_source", False) \
                and main is not None:
            cand = os.path.join(main, proj_info.deps_dir)
            if os.path.isdir(cand):
                paths = [cand]
                auto = True
                note("sourcing unchanged packages from main worktree (%s)"
                     % cand)
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

    def _apply_handler_overlay(self, proj_info, handler_overlay):
        """Merge a clone-provided handler overlay into proj_info.handler_configs.

        A clone provider may forward handler configuration alongside the tree it
        checks out.  The overlay is merged *underneath* whatever the workspace's
        own ``ivpm.yaml`` declares -- the local manifest always wins on a
        conflict:

        - a handler the manifest does not configure at all is adopted wholesale;
        - dicts are merged recursively (local keys win);
        - lists are unioned order-stably (overlay values first, then local
          values not already present);
        - any other (scalar) value keeps the local one.

        The merge is entirely generic: core has no knowledge of any particular
        handler's name or keys.
        """
        configs = proj_info.handler_configs
        for handler, overlay_cfg in handler_overlay.items():
            if overlay_cfg is None:
                continue
            existing = configs.get(handler)
            if existing is None:
                configs[handler] = overlay_cfg
            else:
                configs[handler] = self._merge_overlay_value(overlay_cfg, existing)

    @staticmethod
    def _merge_overlay_value(overlay, existing):
        """Merge *overlay* underneath *existing* (existing wins on conflict).

        Dicts merge recursively; lists union order-stably (overlay values
        first); any other value keeps ``existing``."""
        if isinstance(overlay, dict) and isinstance(existing, dict):
            merged = dict(existing)
            for k, ov in overlay.items():
                merged[k] = (ProjectOps._merge_overlay_value(ov, merged[k])
                             if k in merged else ov)
            return merged
        if isinstance(overlay, list) and isinstance(existing, list):
            out = list(overlay)
            for v in existing:
                if v not in out:
                    out.append(v)
            return out
        return existing

    def _load_source_manifest_from_lock(self, deps_dir_override: str = None):
        """Return ``(source_manifest, deps_dir_name)`` recorded by a previous
        ``update --from``, or ``None``.

        This is what makes a ``--from``-driven workspace re-runnable: it has no
        local ``ivpm.yaml``, so without replaying the recorded origin a bare
        ``ivpm update`` has nothing to drive it.  *deps_dir_name* is the
        directory the lock was found in, relative to ``root_dir`` (``"."`` when
        the root *is* the deps-dir), so a re-run reproduces the original layout
        even when the user omits ``--deps-dir``.

        Never walks: replay applies to the workspace the user named, not an
        ancestor of it.
        """
        from .package_lock import read_lock, find_ivpm_deps_dir

        if deps_dir_override is not None:
            deps_dir = os.path.join(self.root_dir, deps_dir_override)
        else:
            _, deps_dir = self._resolve_deps_dir()
        if deps_dir is None:
            return None

        try:
            lock = read_lock(os.path.join(deps_dir, "package-lock.json"))
        except Exception as e:
            _logger.debug("could not read lock for --from replay: %s", e)
            return None

        sm = lock.get("source_manifest")
        if not sm or not sm.get("from"):
            return None

        rel = os.path.relpath(os.path.abspath(deps_dir),
                              os.path.abspath(self.root_dir))
        return sm, rel

    def _init(self, dep_set=None, cli_overrides=None,
              from_manifest : str = None,
              deps_dir_override : str = None,
              default_config : dict = None,
              merged_proj_info = None) -> Tuple['ProjInfo', str, List[str], 'dict']:
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

        # 'ivpm install' supplies an already-merged root synthesized from
        # several sources. There is no manifest to read and no source_manifest
        # to record here -- the multi-source spec goes to the lock as the
        # richer 'install' record instead.
        if merged_proj_info is not None:
            proj_info = merged_proj_info
            deps_dir = os.path.join(
                self.root_dir, deps_dir_override or proj_info.deps_dir)
            return (proj_info, deps_dir,
                    req_dep_sets or [proj_info.default_dep_set], None)

        # Replay: a --from-driven workspace has no local ivpm.yaml, so a bare
        # re-run has nothing to drive it. Recover the recorded origin from the
        # lock. An explicit --from on the command line always takes precedence.
        if (from_manifest is None and default_config is None
                and not os.path.isfile(os.path.join(self.root_dir, "ivpm.yaml"))):
            replay = self._load_source_manifest_from_lock(deps_dir_override)
            if replay is not None:
                recorded, recorded_deps_dir = replay
                from_manifest = recorded["from"]
                if deps_dir_override is None:
                    deps_dir_override = recorded_deps_dir
                    _pre_deps_dir_name = recorded_deps_dir
                note("Reproducing workspace from the source manifest recorded "
                     "in package-lock.json (%s)" % from_manifest)

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
                        allow_include=not fetched.is_remote,
                        is_root=True)           # drives this update
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
                persisted_vars=persisted_vars,
                is_root=True)   # the user's own manifest: deprecations apply

            # No local ivpm.yaml: drive the update from a clone-provided
            # synthesized ``package:`` mapping. Feeding it through the normal
            # reader reuses all dep-set / with: / deps-dir parsing with no new
            # format.
            if proj_info is None and default_config is not None:
                import io
                import yaml
                from .ivpm_yaml_reader import IvpmYamlReader
                note("No ivpm.yaml found; using clone-provided configuration")
                buf = io.StringIO(yaml.dump({"package": default_config}))
                # is_root stays False: this config is synthesized by the clone
                # provider, not a file the user can edit, so a deprecation
                # warning pointing at "<clone-provided-config>" is unactionable.
                proj_info = IvpmYamlReader().read(
                    buf, "<clone-provided-config>",
                    cli_overrides=cli_overrides,
                    persisted_vars=persisted_vars,
                    allow_include=False)

        if proj_info is None:
            # A "bare" workspace (no ivpm.yaml but a discoverable lock) can be
            # inspected with status/sync, but update needs a manifest. Give a
            # precise message instead of the generic meta-data error.
            if from_manifest is None and default_config is None:
                from .package_lock import find_ivpm_deps_dir
                if find_ivpm_deps_dir(self.root_dir) is not None:
                    fatal("This workspace has no ivpm.yaml (it was created by "
                          "an 'ivpm clone' provider from a source without one). "
                          "'ivpm update' needs a manifest; re-clone to refresh, "
                          "or add an ivpm.yaml.")
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
    
    # Static: these depend only on their arguments. 'ivpm install' calls them
    # directly (ProjectOps._getDepSets) to select dep-sets out of each fetched
    # source manifest without constructing a ProjectOps.
    @staticmethod
    def _getDepSet(proj_info, dep_set):
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

    @staticmethod
    def _getDepSets(proj_info, dep_sets):
        """Resolve one or more requested dep-sets into a single PackagesInfo.

        Returns ``(names, ds)`` where *names* is the ordered list of resolved
        dep-set names and *ds* is the dep-set to install. With a single
        selection (or the default) the manifest's own PackagesInfo is returned
        unchanged; with several, their packages are merged into one.
        """
        from .packages_info import PackagesInfo

        if not dep_sets:
            name, ds = ProjectOps._getDepSet(proj_info, None)
            return [name], ds

        if len(dep_sets) == 1:
            name, ds = ProjectOps._getDepSet(proj_info, dep_sets[0])
            return [name], ds

        from .ivpm_yaml_reader import merge_with

        merged = PackagesInfo("+".join(dep_sets))
        merged_with = {}
        for name in dep_sets:
            _, ds = ProjectOps._getDepSet(proj_info, name)
            # Later dep-sets win on name collisions; a shared package pulled by
            # more than one set is installed once.
            merged.packages.update(ds.packages)
            merged.setup_deps.update(ds.setup_deps)
            merged.options.update(ds.options)
            # Per-dep-set 'with:' also merges left-to-right (later set wins).
            if ds.with_raw:
                merged_with = merge_with(merged_with, ds.with_raw)
        merged.with_raw = merged_with or None

        return list(dep_sets), merged
