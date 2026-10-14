#****************************************************************************
#* template_vars.py
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
"""The values IVPM passes to every init template.

Three channels, each with a fixed meaning (``init-templates-design.md`` §5):

* **Explicit answers** (copier ``data``): ``NAME``, ``-v`` and ``-D``.  The
  question is not asked.
* **Suggested defaults** (copier ``user_defaults``): derived and configured
  values that pre-fill a question.  Later layers win: derived, the
  template's provider ``defaults``, ``SiteConfig.get_init_defaults()``, the
  site config file's ``init-defaults:``, then the user config file's.
* **Context** (``data["ivpm"]``): read-only facts about the IVPM run.  It is
  not a question, so copier never writes it to the answers file.
"""
import datetime
import os
import platform
import subprocess
import sys
from typing import Any, Dict, List, Tuple

import yaml

from .errors import InitError
from .template_info import InitTemplate

# The key the read-only context is passed under; not available to -D.
CONTEXT_KEY = "ivpm"

DEFAULT_VERSION = "0.0.1"


def parse_definitions(defs: List[str]) -> Dict[str, Any]:
    """Parse ``-D KEY=VALUE`` items; VALUE is read as a YAML scalar.

    ``-D flag=true`` gives a bool and ``-D n=3`` an int, as with ``copier -d``.
    A value that is not valid YAML is kept as the literal string.  A repeated
    key takes its last value.
    """
    ret: Dict[str, Any] = {}
    for item in defs or ():
        if "=" not in item:
            raise InitError("-D %s: expected KEY=VALUE" % item)
        key, val = item.split("=", 1)
        key = key.strip()
        if not key:
            raise InitError("-D %s: the key is empty" % item)
        ret[key] = _parse_scalar(val)
    return ret


def _parse_scalar(val: str) -> Any:
    if val == "":
        return ""
    try:
        return yaml.safe_load(val)
    except yaml.YAMLError:
        return val


def explicit_answers(args) -> Dict[str, Any]:
    """Answers given on the command line; ``-D`` beats ``NAME`` and ``-v``."""
    ret: Dict[str, Any] = {}
    name = getattr(args, "name", None)
    if name is not None:
        ret["project_name"] = name
    version = getattr(args, "version", None)
    if version is not None:
        ret["project_version"] = version
    ret.update(parse_definitions(getattr(args, "definitions", None) or []))
    return ret


def _git_config(key: str, cwd: str = None):
    try:
        r = subprocess.run(["git", "config", "--get", key], cwd=cwd,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    val = r.stdout.strip()
    return val if r.returncode == 0 and val else None


def derived_defaults(dest_dir: str) -> Dict[str, Any]:
    """Defaults IVPM can work out itself (well-known question names, §5)."""
    ret: Dict[str, Any] = {
        "project_name": os.path.basename(os.path.normpath(os.path.abspath(dest_dir))),
        "project_version": DEFAULT_VERSION,
        "year": datetime.date.today().year,
    }
    cwd = dest_dir if os.path.isdir(dest_dir) else None
    for key, cfg in (("author_name", "user.name"), ("author_email", "user.email")):
        val = _git_config(cfg, cwd)
        if val:
            ret[key] = val
    return ret


def layered_user_defaults(template: InitTemplate, dest_dir: str) -> Dict[str, Any]:
    """Merge the suggested-default layers, later winning (see module docstring)."""
    from ..site_config import get_site_config, _file_init_defaults_ex
    ret = derived_defaults(dest_dir)
    ret.update(template.defaults or {})
    site_defaults = get_site_config().get_init_defaults()
    if isinstance(site_defaults, dict):
        ret.update(site_defaults)
    # The accessor lists the user file first; the user file must win.
    for data, _path in reversed(_file_init_defaults_ex()):
        ret.update(data)
    ret.pop(CONTEXT_KEY, None)
    return ret


def ivpm_context(template: InitTemplate) -> Dict[str, Any]:
    """The read-only ``{{ ivpm.* }}`` values a template can use."""
    from ..__version__ import get_version
    from ..platform_info import _normalize_os, _normalize_arch
    os_name = _normalize_os((platform.system() or "").lower())
    arch = _normalize_arch(platform.machine())
    return {
        "version": get_version(),
        "python": sys.executable,
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "os": os_name,
        "arch": arch,
        "platform": "%s-%s" % (os_name, arch),
        "template": template.name,
        "origin": template.origin,
    }


def build_copier_inputs(template: InitTemplate, args,
                        dest_dir: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(data, user_defaults)`` for one ``ivpm init`` run."""
    explicit = explicit_answers(args)
    if CONTEXT_KEY in explicit:
        raise InitError("-D %s=...: '%s' is reserved for the values IVPM provides"
                        % (CONTEXT_KEY, CONTEXT_KEY))
    data = dict(explicit)
    data[CONTEXT_KEY] = ivpm_context(template)
    return data, layered_user_defaults(template, dest_dir)
