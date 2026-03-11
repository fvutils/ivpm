#****************************************************************************
#* package_handler.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
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
import threading
import time
from contextlib import contextmanager
from typing import ClassVar, List, Optional
from ..package import Package
from ..project_ops_info import ProjectUpdateInfo, ProjectBuildInfo
from ..update_event import UpdateEvent, UpdateEventType


class HandlerFatalError(Exception):
    """Raised by a leaf handler to signal a fatal error that should abort the update."""
    pass


class TaskHandle:
    """Handle returned by task_context(). Supports progress reporting and nested tasks."""

    def __init__(self, info, task_id: str, task_name: str, parent_task_id: Optional[str] = None):
        self._info = info
        self._task_id = task_id
        self._task_name = task_name
        self._parent_task_id = parent_task_id
        self._start = time.monotonic()

    def progress(self, message: str, step: Optional[int] = None, total: Optional[int] = None):
        """Emit a HANDLER_TASK_PROGRESS event."""
        dispatcher = getattr(self._info, 'event_dispatcher', None)
        if dispatcher:
            dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
                task_id=self._task_id,
                task_name=self._task_name,
                task_message=message,
                task_step=step,
                task_total=total,
                parent_task_id=self._parent_task_id,
            ))

    @contextmanager
    def task_context(self, task_id: str, task_name: str):
        """Create a nested child task under this task."""
        child = TaskHandle(self._info, task_id, task_name, parent_task_id=self._task_id)
        dispatcher = getattr(self._info, 'event_dispatcher', None)
        if dispatcher:
            dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.HANDLER_TASK_START,
                task_id=task_id,
                task_name=task_name,
                parent_task_id=self._task_id,
            ))
        try:
            yield child
        except Exception as e:
            if dispatcher:
                dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.HANDLER_TASK_ERROR,
                    task_id=task_id,
                    task_name=task_name,
                    parent_task_id=self._task_id,
                    task_message=str(e),
                    duration=time.monotonic() - child._start,
                ))
            raise
        else:
            if dispatcher:
                dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.HANDLER_TASK_END,
                    task_id=task_id,
                    task_name=task_name,
                    parent_task_id=self._task_id,
                    duration=time.monotonic() - child._start,
                ))


@dc.dataclass
class PackageHandler(object):
    # --- Handler metadata (ClassVar — override in subclasses with plain assignment) ---
    name:               ClassVar[Optional[str]] = None
    description:        ClassVar[Optional[str]] = None
    phase:              ClassVar[int]  = 0
    conditions_summary: ClassVar[Optional[str]] = None  # human-readable activation conditions

    # leaf_when: list of callable(pkg: Package) -> bool, or None (always active as leaf)
    leaf_when:   ClassVar[Optional[List]] = None

    # root_when: list of callable(packages: list[Package]) -> bool, or None (always active as root)
    root_when:   ClassVar[Optional[List]] = None

    # Per-instance thread lock — acquired during writes to accumulated state
    _lock: threading.Lock = dc.field(default_factory=threading.Lock, init=False, repr=False)

    @classmethod
    def handler_info(cls) -> 'HandlerInfo':
        """Return self-description for 'ivpm show handler <name>'."""
        from ..show.info_types import HandlerInfo
        return HandlerInfo(
            name=cls.name or "unknown",
            description=cls.description or "",
            phase=cls.phase,
            conditions=cls.conditions_summary or "",
        )

    # ------------------------------------------------------------------ #
    # Per-run state reset                                                  #
    # ------------------------------------------------------------------ #

    def reset(self):
        """Clear per-run accumulated state. Called automatically by on_root_pre_load."""
        pass

    # ------------------------------------------------------------------ #
    # Leaf callbacks (called per-package, concurrently)                   #
    # ------------------------------------------------------------------ #

    def on_leaf_pre_load(self, pkg: Package, update_info: ProjectUpdateInfo):
        """Called before a package is fetched/unpacked."""
        pass

    def on_leaf_post_load(self, pkg: Package, update_info: ProjectUpdateInfo):
        """Called after a package is ready on disk. Replaces process_pkg()."""
        pass

    # ------------------------------------------------------------------ #
    # Root callbacks (called once, on the main thread)                    #
    # ------------------------------------------------------------------ #

    def on_root_pre_load(self, update_info: ProjectUpdateInfo):
        """Called before any packages start loading. Resets per-run state."""
        self.reset()

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        """Called after all packages are loaded. Main work (venv, install, generate).
        Replaces update()."""
        pass

    # ------------------------------------------------------------------ #
    # Other handler hooks                                                  #
    # ------------------------------------------------------------------ #

    def get_lock_entries(self, deps_dir: str) -> dict:
        """Return extra top-level keys to merge into the lock file.

        Called after on_root_post_load() completes. Default returns {}.
        """
        return {}

    def build(self, build_info: ProjectBuildInfo):
        pass

    def add_options(self, subcommands: dict):
        """Register handler-specific CLI options. Called during parser setup.

        subcommands is a dict mapping subcommand name -> argparse subparser.
        """
        pass

    # ------------------------------------------------------------------ #
    # Task progress helpers                                                #
    # ------------------------------------------------------------------ #

    @contextmanager
    def task_context(self, info, task_id: str, task_name: str):
        """Top-level task context. info may be ProjectUpdateInfo or ProjectBuildInfo.

        Emits HANDLER_TASK_START on entry, HANDLER_TASK_END on clean exit,
        HANDLER_TASK_ERROR on exception (re-raises). Returns a TaskHandle for
        progress() calls and nested task_context() calls.
        """
        handle = TaskHandle(info, task_id, task_name)
        dispatcher = getattr(info, 'event_dispatcher', None)
        if dispatcher:
            dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.HANDLER_TASK_START,
                task_id=task_id,
                task_name=task_name,
            ))
        try:
            yield handle
        except Exception as e:
            if dispatcher:
                dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.HANDLER_TASK_ERROR,
                    task_id=task_id,
                    task_name=task_name,
                    task_message=str(e),
                    duration=time.monotonic() - handle._start,
                ))
            raise
        else:
            if dispatcher:
                dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.HANDLER_TASK_END,
                    task_id=task_id,
                    task_name=task_name,
                    duration=time.monotonic() - handle._start,
                ))

    # ------------------------------------------------------------------ #
    # Deprecated shims                                                     #
    # ------------------------------------------------------------------ #

    def process_pkg(self, pkg: Package):
        """Deprecated: use on_leaf_post_load(). Kept for backward compatibility."""
        self.on_leaf_post_load(pkg, None)

    def update(self, update_info: ProjectUpdateInfo):
        """Deprecated: use on_root_post_load(). Kept for backward compatibility."""
        self.on_root_post_load(update_info)

