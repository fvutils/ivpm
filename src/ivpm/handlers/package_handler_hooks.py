#****************************************************************************
#* package_handler_hooks.py
#*
#* Runs the hooks declared in the root ivpm.yaml ('package: hooks:') at
#* lifecycle events. See docs/hooks-design.md.
#*
#* Copyright 2024 Matthew Ballance and Contributors
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
import dataclasses as dc
import logging
import os

from ..installer_run import run_installer, format_output_tail
from ..msg import note
from ..perf import span_or_null
from ..project_ops_info import ProjectUpdateInfo
from ..utils import getlocstr
from .package_handler import PackageHandler, HandlerFatalError
from .handler_phases import HandlerPhase

_logger = logging.getLogger("ivpm.handlers.package_handler_hooks")


def _venv_bindir(python_dir: str) -> str:
    # Same layout test as utils.get_venv_python
    scripts = os.path.join(python_dir, "Scripts")
    return scripts if os.path.isdir(scripts) else os.path.join(python_dir, "bin")


def _venv_python(python_dir: str):
    """The venv interpreter, or None when there is no venv."""
    for rel in (("Scripts", "python.exe"), ("Scripts", "python"), ("bin", "python")):
        path = os.path.join(python_dir, *rel)
        if os.path.isfile(path):
            return path
    return None


@dc.dataclass
class PackageHandlerHooks(PackageHandler):
    name               = "hooks"
    description        = "Runs project hooks ('hooks:' in the root ivpm.yaml) at lifecycle events"
    leaf_when          = None
    root_when          = None
    # After 'install' (the venv exists) and 'integrate'
    phase              = HandlerPhase.FINALIZE
    conditions_summary = "root: when the root ivpm.yaml declares hooks for the event"

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            cli_options=[
                "update: --no-hooks   Do not run hooks declared in ivpm.yaml",
                "clone:  --no-hooks   (same as update)",
            ],
            notes=(
                "Runs 'post-update' hooks after update and clone (not sync). Only "
                "the root project's hooks run; a dependency's are ignored. A "
                "'python:' hook runs as <venv>/bin/python -c <source>, with the "
                "project root as working directory."
            ),
        )

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        self.run_event("post-update", update_info)

    def run_event(self, event: str, update_info: ProjectUpdateInfo):
        """Run the hooks for *event*, in declared order. Stops at the first failure."""
        hooks = [h for h in (getattr(update_info, "hooks", None) or [])
                 if event in h.events]
        if not hooks:
            return

        if getattr(update_info.args, "no_hooks", False):
            note("Skipping %d %s hook(s) (--no-hooks)" % (len(hooks), event))
            return

        python_dir = os.path.join(update_info.deps_dir, "python")
        venv_python = None
        if any(h.kind == "python" for h in hooks):
            venv_python = _venv_python(python_dir)
            if venv_python is None:
                raise HandlerFatalError(
                    "%s needs the project's Python virtual environment, but "
                    "there is none at %s: %s" % (
                        self._describe(hooks[0], event, 1), python_dir,
                        self._why_no_venv(update_info)))

        env = self._hook_env(update_info, python_dir)
        cwd = update_info.project_root()
        quiet = bool(getattr(update_info, "suppress_output", False))
        perf = getattr(update_info, "perf", None)

        with span_or_null(perf, "hooks." + event):
            for i, hook in enumerate(hooks, start=1):
                label = hook.label(event, i)
                with span_or_null(perf, "hook", package=label), \
                        self.task_context(update_info, "hook.%s.%d" % (event, i),
                                          "Hook: %s" % label) as task:
                    _logger.debug("Running %s", self._describe(hook, event, i))
                    result = run_installer(
                        [venv_python, "-c", hook.source],
                        env=env, cwd=cwd, task=task, quiet=quiet)
                    for line in result.lines:
                        _logger.debug("hook %s: %s", label, line)
                    if not result.ok:
                        raise HandlerFatalError(
                            "%s failed with exit code %d%s" % (
                                self._describe(hook, event, i),
                                result.returncode,
                                format_output_tail(result.lines)))

    @staticmethod
    def _describe(hook, event, index) -> str:
        """'hook 'egg-info' (post-update, ivpm.yaml:12:7)'"""
        loc = ""
        if hook.srcinfo is not None:
            loc = ", " + getlocstr(hook)
        return "hook '%s' (%s%s)" % (hook.label(event, index), event, loc)

    @staticmethod
    def _why_no_venv(update_info) -> str:
        from ..proj_info import VenvMode
        from .package_handler_python import resolve_venv_mode
        if resolve_venv_mode(update_info) != VenvMode.SKIP:
            return "it was not created"
        if getattr(update_info.args, "py_skip_install", False):
            return "Python installation is disabled (--py-skip-install)"
        if update_info.skip_venv:
            return "venv creation was skipped for this run"
        return "Python installation is disabled (with: python: venv: false)"

    @staticmethod
    def _hook_env(update_info, python_dir) -> dict:
        """The environment a hook runs in: the caller's, with the venv active.

        A hook that itself runs 'python' or 'pip' gets the venv's. IVPM_PROJECT
        and IVPM_PACKAGES match what packages.envrc exports.
        """
        env = dict(os.environ)
        abs_python_dir = os.path.abspath(python_dir)
        env["VIRTUAL_ENV"] = abs_python_dir
        env["PATH"] = os.pathsep.join(
            [_venv_bindir(abs_python_dir)]
            + ([env["PATH"]] if env.get("PATH") else []))
        env.pop("PYTHONHOME", None)
        env["IVPM_PACKAGES"] = os.path.abspath(update_info.deps_dir)
        env["IVPM_PROJECT"] = os.path.abspath(update_info.project_root())
        return env
