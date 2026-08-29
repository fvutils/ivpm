#****************************************************************************
#* project_opts_info.py
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
import dataclasses as dc
import enum
import logging
import os
import threading
import types
from typing import List, Optional, Tuple

from .update_event import UpdateEvent, UpdateEventType, UpdateEventDispatcher

_logger = logging.getLogger("ivpm.project_ops_info")


class FileStatus(enum.Enum):
    Unknown = "U"
    Modified = "M"
    Added = "A"
    Deleted = "D"

class InstallMode(enum.Enum):
    """How the deps-dir relates to a project.

    ``WORKSPACE`` is the conventional layout: a project root containing an
    ``ivpm.yaml``, with dependencies in a subdirectory beneath it.

    ``TOOLCHAIN`` is a shared tool directory: the deps-dir *is* the root and
    there is no project above it.  Handlers must not write project-scoped
    artifacts in this mode -- there is nowhere correct to put them.
    """
    WORKSPACE = enum.auto()
    TOOLCHAIN = enum.auto()


class ToolchainModeError(Exception):
    """Raised when a project root is demanded in ``TOOLCHAIN`` mode."""


@dc.dataclass
class ProjectOpsInfo(object):
    args : object
    deps_dir : str
    install_mode : InstallMode = InstallMode.WORKSPACE

    def project_root(self) -> str:
        """The project root directory.

        Raises in ``TOOLCHAIN`` mode -- there is no project.  This is the
        accessor for a handler that genuinely requires a project; failing
        loudly is the point, because the alternative is silently writing a
        project-scoped artifact into a shared tool tree.
        """
        root = self.project_root_or_none()
        if root is None:
            raise ToolchainModeError(
                "no project root in toolchain mode (deps-dir %s is the root); "
                "use project_root_or_none() to branch, or declare "
                "toolchain_support = UNSUPPORTED" % self.deps_dir)
        return root

    def project_root_or_none(self) -> Optional[str]:
        """The project root directory, or ``None`` in ``TOOLCHAIN`` mode."""
        if self.install_mode is InstallMode.TOOLCHAIN:
            return None
        return getattr(self, "project_dir", None) or os.path.dirname(self.deps_dir)

@dc.dataclass
class ProjectBuildInfo(ProjectOpsInfo):
    debug : bool = False
    event_dispatcher: Optional['UpdateEventDispatcher'] = None

@dc.dataclass
class ProjectSyncInfo(ProjectOpsInfo):
    dry_run: bool = False
    packages_filter: Optional[List[str]] = None  # if set, only sync named packages
    max_parallel: int = 0                         # 0 = cpu_count()
    progress: Optional[object] = None             # SyncProgressListener instance

@dc.dataclass
class ProjectRemoveInfo(ProjectOpsInfo):
    """Passed to Package.remove() / Package.removal_safety() and handler
    on_destroy() during `ivpm destroy`. Mirrors ProjectSyncInfo."""
    dry_run: bool = False
    force: bool = False
    deps_only: bool = False
    keep_venv: bool = False                          # post-MVP; default off
    progress: Optional[object] = None                # RemoveProgressListener
    event_dispatcher: Optional[UpdateEventDispatcher] = None

@dc.dataclass
class ProjectStatusInfo(ProjectOpsInfo):
    dep_set: Optional[str] = None

@dc.dataclass
class ProjectStatusResult(object):
    package : str
    path : str
    status : FileStatus

@dc.dataclass
class ProjectUpdateInfo(ProjectOpsInfo):
    project_name : Optional[str] = None
    project_version : Optional[str] = None
    force_py_install : bool = False
    skip_venv : bool = False
    cache_hits: int = 0
    cache_misses: int = 0
    total_packages: int = 0
    cacheable_packages: int = 0
    cache_unconfigured_packages: int = 0  # cache=True but IVPM_CACHE not set
    editable_packages: int = 0
    deps_source: Optional['DepsSource'] = None
    deps_source_mode: str = "link"  # "link" or "copy"
    deps_source_auto: bool = False  # deps_source was auto-detected (git worktree)
    deps_source_hits: int = 0
    deps_source_misses: int = 0
    max_parallel: int = 0  # 0 means use available cores
    event_dispatcher: Optional[UpdateEventDispatcher] = None
    suppress_output: bool = False  # When True, suppress subprocess output (Rich TUI mode)
    python_config: Optional[object] = None  # PythonConfig from root ivpm.yaml
    node_config: Optional[object] = None    # NodeConfig from root ivpm.yaml
    handler_configs: dict = dc.field(default_factory=dict)  # Extra with: keys for plugin handlers
    env_settings: list = dc.field(default_factory=list)  # Root project env: directives (EnvSpec); emitted to packages.envrc by the direnv handler
    project_dir: Optional[str] = None   # Project root (one level above deps_dir)
    # Name of the variable packages.envrc exports for the deps-dir, set by
    # 'ivpm install --root-var'. None means the bare default: IVPM_PACKAGES is
    # exported directly. When set, that name holds the path and IVPM_PACKAGES
    # is exported as an alias of it, so existing manifests keep resolving.
    root_var: Optional[str] = None
    # Path of the dependency scope this view resolves into, relative to the
    # root deps-dir ("" for the root scope). Set on scope views only.
    scope_prefix: str = ""
    handler_state: dict = dc.field(default_factory=dict)  # Loaded from ivpm.json["handlers"]
    lock_data: Optional[dict] = None  # Parsed package-lock.json for change detection
    pending_skill_dirs: List[Tuple[str, str]] = dc.field(default_factory=list)  # (name, skill_dir) pushed by handlers
    pending_plugin_dirs: List[Tuple[str, str]] = dc.field(default_factory=list)  # (name, plugin_root) pushed by handlers
    modules_interface: Optional['ModulesInterface'] = None  # lazily populated by PackageModule.update()
    _tui_ref: Optional[object] = None  # Reference to the TUI for prompt callbacks
    _cache_provider: Optional['CacheProvider'] = None  # session cache provider (memoized)
    _load_planner: Optional['LoadPlanner'] = None  # session load planner (memoized)
    disable_cache: bool = False  # When True, force a null cache provider (--no-cache)
    # Performance-span collector (perf.py). Always constructed by update();
    # --timing only gates the after-run display, not collection.
    perf: Optional['PerfCollector'] = None
    # span_id of the enclosing "fetch" phase, so per-package spans opened on
    # worker threads parent onto it across the executor boundary.
    _fetch_parent_id: Optional[int] = None
    # Thread-local handle to the current package's open fetch span. package
    # loads run one-per-worker-thread, so keeping "the current package" thread-
    # local lets report_cache_hit/miss (which carry no package name) annotate
    # the right span -- replacing the old single-slot _current_* timer that
    # interleaved under parallelism.
    _pkg_local: object = dc.field(
        default_factory=threading.local, repr=False, compare=False)

    @property
    def root_deps_dir(self) -> str:
        """The workspace's top-level deps-dir.

        Equal to ``deps_dir`` here; a scope view overrides it. Handlers that
        emit paths into a root-level artifact (packages.envrc, the dv-flow
        package map) must resolve against this, not against the scope they
        happen to be called in.
        """
        return self.deps_dir

    def scope_view(self, deps_dir: str, scope_prefix: str = "") -> 'ProjectUpdateInfo':
        """A view of this session with a different deps-dir.

        Used to resolve a nested dependency scope: everything mutable --
        counters, the memoized cache provider, the event dispatcher, perf, the
        thread-local package span -- is *shared* with this instance; only
        ``deps_dir`` differs. Returns ``self`` for the root scope so the flat
        path is byte-for-byte unchanged.

        This is deliberately not ``dc.replace``, which would clone the counters
        and the memoized cache provider into a second, diverging session.
        """
        if deps_dir == self.deps_dir:
            return self
        return _ScopedUpdateInfo(self, deps_dir, scope_prefix)

    def get_prompt_callback(self):
        """Return a prompt callback appropriate for the current TUI.
    
        Returns None if no TUI is configured or if running non-interactively.
        """
        if self._tui_ref is not None and hasattr(self._tui_ref, "make_prompt_callback"):
            return self._tui_ref.make_prompt_callback()
        return None

    def get_cache_provider(self):
        """Return the session cache provider, creating it on first use.

        The provider is scoped to this session (root project + deps_dir) and
        serves every dependency; the dependency is passed to its methods.
        Memoized so all packages in a session share one provider instance.
        """
        if self._cache_provider is None:
            from .cache_provider import CacheContext
            ctx = CacheContext(
                root_name=self.project_name,
                root_version=self.project_version,
                root_dir=self.project_dir,
                deps_dir=self.deps_dir,
            )
            if self.disable_cache:
                # --no-cache overrides the configured provider entirely.
                from .cache_provider import NullCacheProvider
                self._cache_provider = NullCacheProvider(ctx)
            else:
                from .site_config import get_site_config
                self._cache_provider = get_site_config().get_cache_provider(ctx)
        return self._cache_provider

    def get_load_planner(self):
        """The session load planner, created on first use.

        Rooted at ``root_deps_dir``, never at the current scope's deps-dir: lock
        keys are scope *paths* relative to the workspace root, and a nested
        scope's packages are recorded in that same root lock. Called through a
        scope view, this resolves to the one shared planner -- ``root_deps_dir``
        and ``lock_data`` both read through to the base, and the memo is written
        back to it.

        Constructed eagerly by ``ProjectOps.update`` before any parallel package
        load, so the memo never races.
        """
        if self._load_planner is None:
            from .load_plan import LoadPlanner
            self._load_planner = LoadPlanner(self.root_deps_dir, self.lock_data)
        return self._load_planner

    def report_cache_unconfigured(self):
        """Record that a package had cache=True but IVPM_CACHE was not set."""
        self.cache_unconfigured_packages += 1

    def report_deps_source_hit(self):
        self.deps_source_hits += 1

    def report_deps_source_miss(self):
        self.deps_source_misses += 1

    def try_deps_source(self, pkg) -> bool:
        """If a deps-source is configured and satisfies ``pkg``, materialize
        ``deps/<pkg.name>`` from the parent and return True; otherwise return
        False without modifying anything.
        """
        if self.deps_source is None:
            return False
        # In a nested scope, prefer a parent that nested the package at the
        # same position; a parent that flattened it still matches by name.
        key = ("%s/%s" % (self.scope_prefix, pkg.name)) \
            if self.scope_prefix else None
        hit = self.deps_source.lookup(pkg, key)
        if hit is None:
            self.report_deps_source_miss()
            return False
        self._materialize_from_deps_source(pkg, hit)
        self.report_deps_source_hit()
        return True

    def _materialize_from_deps_source(self, pkg, source_path: str):
        import os
        import shutil
        from .load_plan import clear_prepared_dir, preserve_dir_mode
        target = os.path.join(self.deps_dir, pkg.name)
        copy_mode = self.deps_source_mode == "copy"

        # An empty directory left by a pre-populate step is not content, so it
        # is not a collision. In copy mode it is kept and copied *into* (so the
        # group/mode that step configured survives, and the copied files
        # inherit it); in link mode it is removed, since a symlink cannot be
        # created over it and the target carries its own ownership anyway.
        prepared = (os.path.isdir(target) and not os.path.islink(target)
                    and not os.listdir(target))
        if prepared and not copy_mode:
            clear_prepared_dir(target)

        if os.path.lexists(target) and not prepared:
            raise RuntimeError(
                "Cannot materialize %s from deps-source %s: %s already exists"
                % (pkg.name, source_path, target))
        os.makedirs(self.deps_dir, exist_ok=True)
        if copy_mode:
            # See package_dir: copy *into* a prepared directory, and restore its
            # mode afterwards (copytree's copystat would overwrite it).
            with preserve_dir_mode(target):
                shutil.copytree(source_path, target, symlinks=True,
                                dirs_exist_ok=True)
        else:
            os.symlink(source_path, target)
        pkg.from_deps_source = source_path
        pkg.path = target.replace("\\", "/")

    def report_cache_hit(self):
        self.cache_hits += 1
        self._annotate_pkg_span("cache_hit", True)

    def report_cache_miss(self):
        self.cache_misses += 1
        self._annotate_pkg_span("cache_hit", False)

    def _annotate_pkg_span(self, key, value):
        """Set a meta field on the calling thread's current package span, if any.

        Called from within pkg.update() (same worker thread as package_start),
        so it targets the correct package even under parallel loads.
        """
        span = getattr(self._pkg_local, "span", None)
        if span is not None:
            span.meta[key] = value

    def report_package(self, cacheable: bool = False, editable: bool = False):
        """Report a package for statistics.
        
        Args:
            cacheable: True if package has cache=True set
            editable: True if package could be cached but isn't (e.g., git repos, .tar.gz without cache=True)
        """
        self.total_packages += 1
        if cacheable:
            self.cacheable_packages += 1
        if editable:
            self.editable_packages += 1
    
    def package_start(self, name: str, pkg_type: str = None, pkg_src: str = None):
        """Signal that loading of a package has started."""
        # Open the per-package "fetch" span, parented onto the enclosing fetch
        # phase across the executor boundary. Stash it thread-locally so the
        # matching package_complete (same worker thread) closes it and
        # report_cache_hit/miss can annotate it.
        span = None
        if self.perf is not None:
            span = self.perf.open_span(
                "fetch.pkg", package=name,
                parent_id=self._fetch_parent_id,
                type=pkg_type, src=pkg_src)
        self._pkg_local.span = span

        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_START,
                package_name=name,
                package_type=pkg_type,
                package_src=pkg_src
            )
            self.event_dispatcher.dispatch(event)
        _logger.debug("Package start: %s", name)
    
    def package_complete(self, name: str, version: str = None):
        """Signal that loading of a package has completed."""
        # Close the per-package span opened in package_start (same worker
        # thread); read the honest duration and cache-hit off the span rather
        # than a shared slot.
        span = getattr(self._pkg_local, "span", None)
        duration = None
        cache_hit = None
        if span is not None:
            cache_hit = span.meta.get("cache_hit")
            self.perf.close_span(span)
            duration = span.duration
            self._pkg_local.span = None

        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_COMPLETE,
                package_name=name,
                duration=duration,
                cache_hit=cache_hit,
                version=version
            )
            self.event_dispatcher.dispatch(event)
        _logger.debug("Package complete: %s (%.2fs)", name, duration or 0)

    def package_error(self, name: str, error_message: str, loc=None):
        """Signal that loading of a package has failed.

        *loc* is the dependency's source location (a SrcInfo, or any object
        carrying a ``.srcinfo``); when present it is stringified to
        ``file:line:col`` and attached to the event so the TUI can point the
        user at the exact ivpm.yaml entry that failed."""
        # Close the package span (if open) so it is recorded and the worker
        # thread's parent stack unwinds; tag it as errored.
        span = getattr(self._pkg_local, "span", None)
        if span is not None:
            span.meta["error"] = True
            self.perf.close_span(span)
            self._pkg_local.span = None

        package_loc = None
        si = getattr(loc, "srcinfo", loc)
        if si is not None and getattr(si, "filename", None) is not None \
                and getattr(si, "lineno", -1) >= 0:
            package_loc = str(si)
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_ERROR,
                package_name=name,
                error_message=error_message,
                package_loc=package_loc
            )
            self.event_dispatcher.dispatch(event)
        loc_str = (" (%s)" % package_loc) if package_loc else ""
        _logger.error("Package error: %s%s - %s", name, loc_str, error_message)
    
    def update_complete(self):
        """Signal that the update operation is complete."""
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.UPDATE_COMPLETE,
                total_packages=self.total_packages,
                cache_hits=self.cache_hits,
                cache_misses=self.cache_misses,
                cacheable_packages=self.cacheable_packages,
                editable_packages=self.editable_packages,
                cache_unconfigured_packages=self.cache_unconfigured_packages,
                deps_source_hits=self.deps_source_hits,
                deps_source_misses=self.deps_source_misses,
            )
            self.event_dispatcher.dispatch(event)
        _logger.debug("Update complete: %d packages", self.total_packages)
    
    def print_cache_summary(self):
        """Print a summary of cache statistics (legacy support)."""
        # Now handled via TUI events - this is kept for backward compatibility
        # when running without TUI
        if self.event_dispatcher is None:
            _logger.info("")
            _logger.info("Sub-Package Update Summary:")
            _logger.info("  Total packages: %d", self.total_packages)
            _logger.info("  Cacheable packages: %d", self.cacheable_packages)
            _logger.info("  Editable packages: %d", self.editable_packages)
            if self.cacheable_packages > 0:
                _logger.info("  Cache hits: %d", self.cache_hits)
                _logger.info("  Cache misses: %d", self.cache_misses)
                hit_rate = (self.cache_hits / self.cacheable_packages * 100) if self.cacheable_packages > 0 else 0
                _logger.info("  Hit rate: %.1f%%", hit_rate)


class _ScopedUpdateInfo(object):
    """A ProjectUpdateInfo view with an overridden ``deps_dir``.

    Attribute reads and writes delegate to the wrapped instance, so all session
    state stays shared and single-instance. Methods are re-bound to the *view*
    rather than the base, which is what makes ``try_deps_source`` and
    ``_materialize_from_deps_source`` (both of which read ``self.deps_dir``)
    scope-correct without any change of their own.

    Constructed only by ``ProjectUpdateInfo.scope_view``.
    """

    __slots__ = ("_base", "deps_dir", "scope_prefix", "_scoped_provider")

    def __init__(self, base: 'ProjectUpdateInfo', deps_dir: str,
                 scope_prefix: str = ""):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "deps_dir", deps_dir)
        object.__setattr__(self, "scope_prefix", scope_prefix)
        object.__setattr__(self, "_scoped_provider", None)

    @property
    def root_deps_dir(self) -> str:
        """The workspace's top-level deps-dir -- the base's, not this scope's."""
        return object.__getattribute__(self, "_base").deps_dir

    def get_cache_provider(self):
        """The session cache provider, re-based onto this scope's deps-dir.

        The cache itself is shared -- a cache entry's identity is a function of
        source and version, never of where the package is placed -- so this
        wraps the one session provider rather than creating a second.
        """
        provider = object.__getattribute__(self, "_scoped_provider")
        if provider is None:
            base = object.__getattribute__(self, "_base")
            provider = base.get_cache_provider().with_deps_dir(self.deps_dir)
            object.__setattr__(self, "_scoped_provider", provider)
        return provider

    def __getattr__(self, name):
        # Reached only for names not in __slots__.
        base = object.__getattribute__(self, "_base")
        attr = getattr(type(base), name, None)
        if isinstance(attr, types.FunctionType):
            # Bind to the view so 'self.deps_dir' inside resolves to the scope's.
            return attr.__get__(self, type(base))
        return getattr(base, name)

    def __setattr__(self, name, value):
        if name in ("deps_dir", "scope_prefix"):
            object.__setattr__(self, name, value)
        else:
            # Counters and other session state live on (and stay on) the base.
            setattr(object.__getattribute__(self, "_base"), name, value)

    def __repr__(self):
        return "<ProjectUpdateInfo scope_view deps_dir=%s>" % self.deps_dir

