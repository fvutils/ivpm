#****************************************************************************
#* copier_runner.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""Render an init template with copier.

This is the only IVPM module that imports copier, and it does so inside
:func:`run`: copier is the optional ``ivpm[init]`` extra, so nothing else may
depend on it being installed.  copier failures a user can act on are
translated to :class:`~ivpm.init_template.errors.InitError`.
"""
import dataclasses as dc
import importlib.util
import re
from typing import Any, Dict, Optional

from .errors import InitError
from .template_info import InitTemplate
from .trust import trust_hint

INSTALL_HINT = ("Install it with: pip install 'ivpm[init]'  "
                "(or: uvx --with copier ivpm init ...)")

_REQUIRED_RE = re.compile(r'Question "([^"]+)" is required')


@dc.dataclass
class RunOptions:
    defaults: bool = False      # accept defaults; never prompt
    overwrite: bool = False
    pretend: bool = False
    vcs_ref: Optional[str] = None
    skip_tasks: bool = False
    unsafe: bool = False        # the resolved trust decision
    quiet: bool = False


def copier_available() -> bool:
    try:
        return importlib.util.find_spec("copier") is not None
    except (ImportError, ValueError):
        return False


def run(template: InitTemplate, dest_dir: str, data: Dict[str, Any],
        user_defaults: Dict[str, Any], opts: RunOptions) -> None:
    """Render *template* into *dest_dir*."""
    try:
        import copier
        from copier import errors as copier_errors
    except ImportError:
        raise InitError("Template '%s' needs copier, which is not installed." % template.name,
                        INSTALL_HINT)

    try:
        copier.run_copy(
            src_path=template.src,
            dst_path=dest_dir,
            data=data,
            user_defaults=user_defaults,
            defaults=opts.defaults,
            overwrite=opts.overwrite,
            pretend=opts.pretend,
            vcs_ref=opts.vcs_ref,
            skip_tasks=opts.skip_tasks,
            unsafe=opts.unsafe,
            quiet=opts.quiet)
    except copier_errors.UnsafeTemplateError as e:
        raise InitError("Template '%s' is not trusted: %s"
                        % (template.name, _first_line(e)), trust_hint(template))
    except copier_errors.InteractiveSessionError as e:
        if "overwrite" in str(e):
            raise InitError("Template '%s' would overwrite existing files in %s."
                            % (template.name, dest_dir),
                            "Pass -f/--force to overwrite them.")
        raise InitError("Template '%s' needs answers that cannot be asked "
                        "without a terminal." % template.name,
                        "Pass them with -D KEY=VALUE, or use --defaults.")
    except copier_errors.CopierError as e:
        raise InitError("Template '%s' failed: %s" % (template.name, _first_line(e)))
    except ValueError as e:
        m = _REQUIRED_RE.search(str(e))
        if m:
            raise InitError("Template '%s': question '%s' has no default and was not "
                            "answered." % (template.name, m.group(1)),
                            "Pass it with -D %s=VALUE." % m.group(1))
        # copier validates its inputs with ValueError ("Local template must be
        # a directory", "Unknown revision: 'v9'").
        raise InitError("Template '%s' (%s): %s" % (template.name, template.src, e))
    except Exception as e:
        _reraise_known(template, e)
        raise


def _reraise_known(template: InitTemplate, e: Exception):
    """Translate failures from copier's dependencies, without importing them."""
    cls = type(e)
    if cls.__name__ == "ProcessExecutionError":
        # plumbum: a git command copier ran to fetch a remote template failed.
        stderr = (getattr(e, "stderr", "") or "").strip()
        argv = getattr(e, "argv", None)
        raise InitError("Fetching template '%s' from %s failed%s"
                        % (template.name, template.src,
                           (": " + stderr.splitlines()[-1]) if stderr else ""),
                        ("command: %s" % " ".join(str(a) for a in argv)) if argv else None)
    if cls.__module__.startswith("jinja2"):
        raise InitError("Template '%s' failed to render: %s" % (template.name, e))


def _first_line(e) -> str:
    s = str(e).strip()
    return s.splitlines()[0] if s else type(e).__name__
