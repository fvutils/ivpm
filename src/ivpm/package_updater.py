#****************************************************************************
#* project_updater.py
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
#* Created on: Jun 22, 2021
#*     Author: mballance
#*
#****************************************************************************
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import urllib
from zipfile import ZipFile

from ivpm.msg import note, error, fatal, warning
from ivpm.diagnostics import SrcLoaderError
from ivpm.package import Package, SourceType, SourceType2Ext, PackageType
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from typing import Dict, List, Tuple
from ivpm.utils import get_venv_python
from .project_ops_info import ProjectUpdateInfo
from .dep_mode import FLATTEN
from .dep_materialize import promote_to_writable
from .load_plan import LoadAction
from .perf import span_or_null
from .pkg_remove import RefreshDenied
from .prepare import PrepareDenied
from .dep_scope import (
    DepScope, check_recursion, effective_mode, is_nested, scope_path)

_logger = logging.getLogger("ivpm.package_updater")


def _origin_suffix(pkg) -> str:
    """A short parenthetical describing *where* a failing dependency came from.

    Names the source type and URL (the actual specification that failed) and,
    for a transitive dependency, the package that pulled it in. The file:line
    location is added separately by the diagnostic reporter (it reads
    ``pkg.srcinfo``), so this only carries the spec details that help the user
    recognise which entry to fix."""
    parts = []
    src = getattr(pkg, "src_type", None)
    if src is not None:
        src = src.name.lower() if hasattr(src, "name") else str(src)
        parts.append("src: %s" % src)
    url = getattr(pkg, "url", None)
    if url:
        parts.append("url: %s" % url)
    resolved_by = getattr(pkg, "resolved_by", None)
    if resolved_by:
        parts.append("required by: %s" % resolved_by)
    return (" [%s]" % ", ".join(parts)) if parts else ""


def _already_reported(exc) -> bool:
    """True if *exc* carries diagnostics that have already been rendered.

    A ``SrcLoaderError`` with a diagnostics list came out of ``fatal()`` (or
    ``abort_if_errors()``), which renders as it raises -- so the failure has
    already been described in full: git's output, the auth hint, all of it.
    A bare exception from a code path that never touched the diagnostics
    layer has been reported nowhere.
    """
    return isinstance(exc, SrcLoaderError) and bool(exc.diagnostics)


def _failure_message(pkg, exc) -> str:
    """How the batch driver describes *pkg* failing with *exc*.

    Its job is to name the dependency entry that failed -- the file:line comes
    from ``pkg.srcinfo``, the source spec from :func:`_origin_suffix`. Whether
    it also has to state the *reason* depends on where the exception came
    from: repeating an already-rendered report prints the whole body a second
    time, and while a TUI is deferring errors the two copies land adjacent at
    the end of the run, precisely where the user is reading. So for an
    already-reported failure this adds only what the first report lacks;
    otherwise the reason must be quoted here or it is lost entirely.
    """
    if _already_reported(exc):
        return "Failed to update package %s%s" % (pkg.name, _origin_suffix(pkg))
    return "Failed to update package %s: %s%s" % (
        pkg.name, str(exc), _origin_suffix(pkg))


class PackageUpdater(object):
    
    def __init__(self,
                 deps_dir,
                 pkg_handler,
                 load=True,
                 args=None,
                 deps_mode=None,
                 preparers=None):
        self.debug = False
        self.deps_dir = deps_dir
        self.pkg_handler = pkg_handler
        # Pre-populate extensions. Empty unless a site has installed one, in
        # which case dispatch below is a no-op. Injectable for tests.
        if preparers is None:
            from .prepare import PackagePreparerRgy
            preparers = PackagePreparerRgy.inst().mkPreparerList()
        self.preparers = preparers
        self.all_pkgs = PackagesInfo("root")
        # scope path -> locked entry, for reproducing a nested workspace.
        # Empty for a normal update and for every flat workspace.
        self.scope_pins = {}
        # The root dependency scope. A flat workspace has only this one, and
        # scope_path() over it yields bare package names -- so all_pkgs keys,
        # lock keys and on-disk layout are unchanged from before nesting.
        self.root_scope = DepScope(
            name="", deps_dir=deps_dir, parent=None,
            mode=deps_mode if deps_mode is not None else FLATTEN)
        self.new_deps = []
        self.args = object() if args is None else args
        self.load = load
        self.update_info = ProjectUpdateInfo(self.args, deps_dir)
        
        # Get max parallelism from args, default to CPU count
        if hasattr(args, 'jobs') and args.jobs is not None:
            self.max_parallel = args.jobs
        else:
            import multiprocessing
            self.max_parallel = multiprocessing.cpu_count()
        self.update_info.max_parallel = self.max_parallel
        pass
    
    def update(self, pkgs : PackagesInfo) -> PackagesInfo:
        """
        Updates the specified packages, handling dependencies.
        Uses async parallel fetching for efficiency.
        """
        return asyncio.run(self._update_async(pkgs))
    
    async def _update_async(self, pkgs: PackagesInfo) -> PackagesInfo:
        """
        Async implementation of update that processes packages in parallel.
        The 'pkgs' parameter holds the dependency information
        from the root project.
        """
        count = 1

        # The queue carries (package, scope): which deps-dir the package
        # resolves into and which namespace it belongs to.
        pkg_q : List[Tuple[Package, DepScope]] = []

        if len(pkgs.keys()) == 0:
            _logger.info("No packages")

        # A dep-set-level 'deps-mode' on the root project's selected dep-set
        # sets the root scope's mode, so it propagates to everything below.
        if getattr(pkgs, "deps_mode", None) is not None:
            self.root_scope.mode = pkgs.deps_mode

        for key in pkgs.keys():
            _logger.debug("Package: %s", key)
            pkg_q.append((pkgs[key], self.root_scope))

        if not os.path.isdir(self.deps_dir):
            os.makedirs(self.deps_dir)

        # Create semaphore to limit parallelism
        semaphore = asyncio.Semaphore(self.max_parallel)
        
        while True:        
            pkg_deps = {}
            
            # Process this batch of packages in parallel
            if len(pkg_q) > 0:
                results = await self._process_batch_parallel(pkg_q, semaphore)
                
                # Collect dependencies from results.
                #
                # This step runs on the MAIN loop, never in a worker thread --
                # which is what lets the scope tree be mutated here without any
                # locking (nested-deps-impl-plan.md P2).
                for pkg, scope, proj_info in results:
                    self.all_pkgs[scope_path(scope, pkg.name)] = pkg
                    scope.packages[pkg.name] = pkg

                    # proj_info contains info on any setup-deps that
                    # might be required
                    if proj_info is not None:
                        for sd in proj_info.setup_deps:
                            _logger.debug("Add setup-dep %s to package %s", sd, pkg.name)
                            if pkg.name not in self.all_pkgs.setup_deps.keys():
                                self.all_pkgs.setup_deps[pkg.name] = set()
                            self.all_pkgs.setup_deps[pkg.name].add(sd)

                        if proj_info.process_deps:
                            if not proj_info.has_dep_set(pkg.dep_set):
                                fatal(self._mk_missing_dep_set_msg(pkg, proj_info))
                                continue
                            else:
                                note("Loading package %s dependencies from dep-set %s" % (proj_info.name, pkg.dep_set))

                            note("Processing dep-set %s of project %s" % (
                                pkg.dep_set,
                                pkg.name))

                            ds : PackagesInfo = proj_info.get_dep_set(pkg.dep_set)

                            dep_scope = self._scope_for_deps(pkg, scope, proj_info, ds)
                            if dep_scope is None:
                                # Cycle elided: the package is materialized, but
                                # its sub-tree is already present further up.
                                continue

                            for d in ds.packages.keys():
                                dep = ds.packages[d]
                                dep_key = scope_path(dep_scope, dep.name)

                                if dep_key not in pkg_deps.keys():
                                    # Track which package caused this dependency to be resolved
                                    dep.resolved_by = pkg.name
                                    dep.resolved_by_key = scope_path(scope, pkg.name)
                                    self._apply_scope_pin(dep, dep_key)
                                    pkg_deps[dep_key] = (dep, dep_scope)
                                else:
                                    # Already resolved by a higher-level package - don't override
                                    pass

            # Collect new dependencies and add to queue
            pkg_q = []
            for key in pkg_deps.keys():
                dep, dep_scope = pkg_deps[key]
                # Dedup is per-scope: the same name in two scopes is two
                # different packages, which is the point of nesting.
                existing = dep_scope.packages.get(dep.name)
                if existing is None:
                    # New package
                    pkg_q.append((dep, dep_scope))
                elif getattr(existing, "virtual", False) \
                        and not getattr(dep, "virtual", False):
                    # A real package whose name collides with a virtual redirect
                    # (`src: ivpm.yaml` factory) of the same name. The redirect
                    # installs nothing itself, so it must not shadow the real
                    # package -- otherwise the real one is silently never fetched
                    # (e.g. a `gcc-riscv` alias pointing at a dep-set that also
                    # contains a `gcc-riscv` package). Queue the real package so
                    # it is fetched; it will overwrite the virtual node below.
                    pkg_q.append((dep, dep_scope))
            note("%d new dependencies from iteration %d" % (len(pkg_q), count))
                    
            if len(pkg_q) == 0:
                # We're done
                break
            
            count += 1
            
        return self.all_pkgs
    
    def exclude_root_project(self, name: str) -> None:
        """Prevent the top-level project from being loaded as a dependency of
        itself.

        Root-scope-keyed on purpose: a package *nested* under some other
        boundary that happens to share the root project's name is a different
        package, and must not be caught by this marker.
        """
        self.root_scope.packages[name] = None
        self.all_pkgs[scope_path(self.root_scope, name)] = None

    def _apply_scope_pin(self, dep, dep_key: str) -> None:
        """Pin *dep* to the identity a lock file recorded for its scope path.

        Reproduction mode seeds the root scope from the lock and lets the
        manifests rebuild the nested tree; this is what makes the *versions* in
        that rebuilt tree match the lock. Only resolved identity is pinned --
        never the url or spec -- and only when the source type still agrees, so
        a genuine manifest change is not silently overridden.
        """
        entry = self.scope_pins.get(dep_key)
        if entry is None:
            return

        src = getattr(dep, "src_type", None) or ""
        if hasattr(src, "name"):
            from .package import SourceType2Spec
            src = SourceType2Spec.get(src, src.name.lower())
        if str(src) != entry.get("src", ""):
            return

        if src == "git":
            commit = entry.get("commit_resolved")
            if commit:
                dep.commit = commit
                dep.resolved_commit = commit
        elif src in ("gh-rls", "pypi"):
            version = entry.get("version_resolved")
            if version:
                dep.version = version
                dep.resolved_version = version

    def _mk_missing_dep_set_msg(self, pkg, proj_info) -> str:
        """Explain a dep-set that the resolved package does not declare.

        The dep-set name is often *inherited* -- a dependency entry that names
        no dep-set picks up the name of the dep-set that declares it -- so the
        name in the message may appear nowhere near the package it failed on.
        Point at the entry that asked, and at what the package actually offers.
        """
        from .utils import getlocstr

        avail = sorted(proj_info.dep_set_m.keys())
        msg = "package '%s' does not contain dep-set '%s'\n" % (
            proj_info.name, pkg.dep_set)

        loc = getlocstr(pkg)
        if pkg.dep_set_inherited:
            msg += ("  '%s' was inherited: the dependency entry for '%s' @ %s "
                    "names no 'dep-set', so it uses the name of the dep-set "
                    "that declares it.\n" % (pkg.dep_set, pkg.name, loc))
        else:
            msg += "  requested by the dependency entry for '%s' @ %s\n" % (
                pkg.name, loc)

        if pkg.resolved_by is not None:
            msg += "  resolved while processing package '%s'\n" % (
                pkg.resolved_by_key or pkg.resolved_by,)

        msg += "  dep-sets declared by '%s': %s\n" % (
            proj_info.name,
            ", ".join(avail) if avail else "<none>")
        msg += ("  Add an explicit 'dep-set: <name>' to that dependency entry, "
                "or declare '%s' in '%s'." % (pkg.dep_set, proj_info.name))
        return msg

    def _scope_for_deps(self, pkg, scope: DepScope, proj_info,
                        ds: PackagesInfo) -> 'Optional[DepScope]':
        """Which scope *pkg*'s own dependencies resolve into.

        Returns *scope* itself under ``flatten`` (today's behavior), a freshly
        opened child scope under ``nested``, or None when descending would
        repeat a sub-tree already resolved further up.

        Runs on the main loop only -- see P2.
        """
        if not ds.packages:
            # Nothing to place. Opening a scope here would leave an empty
            # deps-dir inside a leaf package -- and, worse, would promote a
            # cached leaf out of the cache for no reason.
            return scope

        mode = effective_mode(pkg, scope)
        if not is_nested(mode):
            return scope

        if getattr(pkg, "virtual", False):
            # A factory occupies no directory, so it has nowhere to nest into.
            # Declaring deps-mode on one is rejected at parse time; inheriting
            # it here is legitimate and simply does not apply.
            return scope

        elided_at = check_recursion(pkg, scope)
        if elided_at is not None:
            pkg.cycle_elided = elided_at
            note(
                "Dependency cycle elided at '%s': it is already provided by "
                "the enclosing scope '%s'. Its dependencies were not resolved "
                "again." % (
                    scope_path(scope, pkg.name),
                    elided_at if elided_at else "<root>"))
            return None

        # A boundary must be a real, writable directory to host a deps-dir.
        # It may currently be a symlink into the cache or a deps-source (P1).
        #
        # A live-link package (`src: dir` with the default `link: true`) is the
        # user's own working copy. Copying it would silently detach their edits
        # from the workspace, and nesting into it would write a deps-dir inside
        # their source tree. Neither is ours to choose, so say so instead.
        if os.path.islink(pkg.path) and getattr(pkg, "link", False):
            fatal(
                "Package '%s' is resolved with 'deps-mode: nested', but it is a "
                "live link to %s.\n"
                "  A nested package must own its directory so it can hold its "
                "own deps-dir. Either set 'link: false' on the dependency (ivpm "
                "then copies it), or resolve it with 'deps-mode: flatten'." % (
                    scope_path(scope, pkg.name), os.path.realpath(pkg.path)),
                pkg)

        promote_to_writable(pkg)

        deps_dir_name = getattr(proj_info, "deps_dir", None) or "packages"
        # Mark the package as a boundary, so the lock records the *effective*
        # mode rather than merely what some dep entry declared.
        pkg.boundary_deps_dir = deps_dir_name
        child = scope.open_child(pkg, deps_dir_name, mode)
        if not os.path.isdir(child.deps_dir):
            os.makedirs(child.deps_dir, exist_ok=True)
        return child

    async def _process_batch_parallel(self, pkg_q: List[Tuple[Package, DepScope]], semaphore: asyncio.Semaphore) -> List[Tuple[Package, DepScope, ProjInfo]]:
        """Process a batch of packages in parallel."""
        tasks = []
        for pkg, scope in pkg_q:
            tasks.append(self._update_pkg_async(pkg, scope, semaphore))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results, handling any exceptions
        processed = []
        for i, result in enumerate(results):
            pkg = pkg_q[i][0]
            if isinstance(result, (PrepareDenied, RefreshDenied)):
                # A refusal is a policy outcome, not a fetch failure: report it
                # as one, without the "Failed to update" / source-spec framing
                # that would send the user looking at the wrong thing. The
                # dependency's file:line:col still comes from pkg.srcinfo.
                fatal(str(result), pkg)
            elif _already_reported(result):
                # The reason was rendered where it was hit; all that is missing
                # is which dependency entry is responsible. Report that much,
                # then re-raise the original -- rather than fatal()'ing a
                # shorter message, which would strip the reason out of the
                # exception text that library callers (and any handler that
                # inspects it) still rely on.
                error(_failure_message(pkg, result), pkg)
                raise result
            elif isinstance(result, Exception):
                fatal(_failure_message(pkg, result), pkg)
            else:
                processed.append(result)

        return processed
    
    async def _update_pkg_async(self, pkg: Package, scope: DepScope, semaphore: asyncio.Semaphore) -> Tuple[Package, DepScope, ProjInfo]:
        """Async wrapper for updating a single package with semaphore limiting."""
        # Measure the real "wait" — time queued for a worker slot (there is no
        # cache lock to wait on). This span opens/closes on the event-loop
        # thread and parents onto the enclosing fetch phase; the per-package
        # fetch.pkg span (opened in the executor by package_start) is a sibling.
        perf = self.update_info.perf
        qspan = None
        if perf is not None:
            qspan = perf.open_span(
                "pkg.queue_wait", package=pkg.name,
                parent_id=self.update_info._fetch_parent_id)
        async with semaphore:
            if qspan is not None:
                perf.close_span(qspan)
            return await asyncio.get_event_loop().run_in_executor(
                None, self._update_pkg, pkg, scope
            )

    def _prepare_pkg(self, pkg, decision, scope, update_info) -> None:
        """Run the registered preparers for *pkg* before it is populated.

        A no-op when no preparer is installed, which is the default.
        """
        if not self.preparers:
            return

        from .prepare import PrepareRequest

        cache_dir = None
        try:
            provider = update_info.get_cache_provider()
            cache_dir = getattr(provider, "cache_dir", None)
        except Exception:
            # The cache is informational here; never let it block preparation.
            _logger.debug("Could not resolve cache dir for %s", pkg.name)

        req = PrepareRequest(
            pkg=pkg,
            decision=decision,
            target_dir=pkg.path,
            deps_dir=scope.deps_dir,
            scope_key=pkg.scope_key,
            update_info=update_info,
            # Filled in per preparer by the dispatcher, which knows each
            # preparer's name and so which 'with:' block belongs to it.
            config={},
            cache_dir=cache_dir,
            is_virtual=getattr(pkg, "virtual", False))

        perf = getattr(update_info, "perf", None)
        with span_or_null(perf, "prepare.pkg", package=pkg.name):
            self.preparers.prepare(req)

    def _refresh_pkg(self, pkg, decision, update_info) -> None:
        """Clear a stale tree so the provider's fetch path can re-materialize it.

        Called for LoadAction.REFRESH -- a package whose spec drifted, or every
        resident package under ``--refresh-all``. Two things have to be true
        afterwards for the fetch to work: the path is gone (``git clone`` and
        ``copytree`` cannot write over a populated directory), and we were
        allowed to remove it.

        The permission question is delegated to the package, not answered here:
        ``removal_safety()`` is the same gate `ivpm destroy` uses, so a git dep
        with uncommitted edits, unpushed commits, a local-only branch or a stash
        blocks a refresh exactly as it blocks a destroy. UNVERIFIABLE does not
        block -- an archive tree has no VCS to prove anything with, and its
        content is by definition re-downloadable.
        """
        from .pkg_remove import SafetyLevel
        from .project_ops_info import ProjectRemoveInfo

        force = bool(getattr(update_info, "force", False))
        remove_info = ProjectRemoveInfo(
            args=self.args,
            deps_dir=update_info.deps_dir,
            install_mode=update_info.install_mode,
            force=force)

        safety = pkg.removal_safety(remove_info)
        if safety.level is SafetyLevel.BLOCKED and not force:
            raise RefreshDenied(pkg.name, safety, decision.reason)

        with span_or_null(getattr(update_info, "perf", None),
                          "pkg.refresh", package=pkg.name):
            pkg.remove(remove_info)

        # remove() is best-effort by contract (it collects errors rather than
        # raising). If the path survived, the fetch below would fail with a
        # confusing provider-level error, so say what actually happened.
        if pkg.path is not None and os.path.lexists(pkg.path):
            raise Exception(
                "could not clear %s to refresh it; remove it by hand and "
                "re-run" % pkg.path)

    def _update_pkg(self, pkg : Package, scope : DepScope) -> Tuple[Package, DepScope, ProjInfo]:
        """Loads a single package. Returns the package and any dependencies."""
        must_update=False

        _logger.info("Processing package %s (dep-set %s)", pkg.name, pkg.dep_set)
        
        # Get package source info for event - normalize the type name
        pkg_type = getattr(pkg, 'src_type', None)
        if pkg_type is not None:
            # Convert SourceType enum to string spec if needed
            if hasattr(pkg_type, 'name'):
                # It's an enum - use lowercase name
                pkg_type = pkg_type.name.lower()
            else:
                # It's already a string
                pkg_type = str(pkg_type).lower()
        pkg_src = getattr(pkg, 'url', None) or getattr(pkg, 'path', None) or ""

        # Signal package start
        self.update_info.package_start(pkg.name, pkg_type, pkg_src)

        # Every source provider builds its own paths from update_info.deps_dir,
        # so handing it a scope view is what makes all ~77 of those call sites
        # scope-correct without touching any of them. Returns self (the very
        # same object) for the root scope, so flat updates are unchanged.
        update_info = self.update_info.scope_view(scope.deps_dir, scope.path)

        pkg_dir = os.path.join(scope.deps_dir, pkg.name)
        pkg.path = pkg_dir.replace("\\", "/")
        # Handlers key on this rather than on the bare name, which stops two
        # same-named packages in different scopes from silently overwriting
        # each other in the venv / envrc / flow-map accumulations. In a flat
        # workspace it IS the bare name.
        pkg.scope_key = scope_path(scope, pkg.name)

        try:
            # What is about to happen to this package, and why. Memoized, so
            # the source provider below observes the same decision -- in
            # particular, a preparer creating the target directory cannot flip
            # ABSENT to PREPARED_EMPTY and change the answer under it.
            decision = update_info.get_load_planner().decide(pkg)

            # A stale tree is cleared before anything else looks at it, so the
            # prepare step and the source provider both see a clean slate --
            # the provider needs no refresh-awareness of its own, which is what
            # makes this work uniformly across every source type.
            if decision.action is LoadAction.REFRESH:
                note("refreshing %s: %s" % (pkg.name, decision.reason))
                self._refresh_pkg(pkg, decision, update_info)

            # Prepare the location before anything is written into it. This is
            # where a site configures the target (group, mode, ACL) so that
            # content inherits it as it is created, and where it can refuse.
            self._prepare_pkg(pkg, decision, scope, update_info)

            # Notify handler before the package is fetched
            self.pkg_handler.on_leaf_pre_load(pkg, update_info)

            pkg.proj_info = pkg.update(update_info)

            # Merge self-declared types from the dep's own ivpm.yaml into pkg.type_data.
            # Caller-specified types take priority; self-declared ones are appended only
            # if their type name is not already present.
            if pkg.proj_info is not None and pkg.proj_info.self_types:
                from .pkg_content_type_rgy import PkgContentTypeRgy
                ct_rgy = PkgContentTypeRgy.inst()
                caller_names = {td.type_name for td in pkg.type_data}
                for type_name, opts in pkg.proj_info.self_types:
                    if type_name not in caller_names and ct_rgy.has(type_name):
                        pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, None))

            # Notify the package handlers after the source is loaded
            from .handlers.package_handler import HandlerFatalError
            try:
                self.pkg_handler.on_leaf_post_load(pkg, update_info)
            except HandlerFatalError:
                raise
            except Exception as leaf_exc:
                _logger.warning("Handler error for package %s: %s", pkg.name, leaf_exc)

            # Ensure that we use the requested dep-set
            if pkg.proj_info is not None:
                pkg.proj_info.target_dep_set = pkg.dep_set
                pkg.proj_info.process_deps = pkg.process_deps
            
            # Signal package complete, passing resolved version if available (e.g., gh-rls)
            resolved_version = getattr(pkg, 'resolved_version', None)
            self.update_info.package_complete(pkg.name, version=resolved_version)

            return (pkg, scope, pkg.proj_info)
        except Exception as e:
            # Signal package error, carrying the dependency's source location so
            # the TUI can show file:line:col alongside the message.
            self.update_info.package_error(
                pkg.name, str(e), loc=getattr(pkg, "srcinfo", None))
            raise

    

    

