#****************************************************************************
#* fallback_runner.py
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
"""Render the built-in ``default`` template without copier.

copier is an optional extra, but ``ivpm init my-project`` is in every
tutorial and must work on a bare install.  This renderer reads the *same*
``ivpm.yaml.jinja`` copier would, and understands exactly two substitutions,
``{{ project_name }}`` and ``{{ project_version }}``.  A unit test keeps the
``default`` template within that subset, so there is a single copy of it.
"""
import os
import re
from typing import Any, Dict

from .copier_runner import RunOptions
from .errors import InitError
from .template_info import InitTemplate
from .template_rgy import BUILTIN_TEMPLATE, builtin_templates_dir

OUTPUT = "ivpm.yaml"
VARIABLES = ("project_name", "project_version")

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_JINJA_RE = re.compile(r"\{\{|\{%|\{#")


def template_file() -> str:
    return os.path.join(builtin_templates_dir(), BUILTIN_TEMPLATE, "template",
                        OUTPUT + ".jinja")


def supports(template: InitTemplate) -> bool:
    """Only IVPM's own ``default`` -- not a site template that shadows it."""
    return (template.name == BUILTIN_TEMPLATE
            and template.origin == "built-in"
            and os.path.normpath(template.src)
            == os.path.normpath(os.path.join(builtin_templates_dir(), BUILTIN_TEMPLATE)))


def render(text: str, values: Dict[str, Any]) -> str:
    """Substitute the supported variables in *text*; reject anything else."""
    def _sub(m):
        name = m.group(1)
        if name not in VARIABLES:
            raise RuntimeError("fallback renderer: unsupported variable '%s'" % name)
        return str(values[name])
    out = _VAR_RE.sub(_sub, text)
    if _JINJA_RE.search(out):
        raise RuntimeError("fallback renderer: unsupported Jinja construct in %s"
                           % template_file())
    return out


def run(template: InitTemplate, dest_dir: str, data: Dict[str, Any],
        user_defaults: Dict[str, Any], opts: RunOptions) -> None:
    values = {}
    for name in VARIABLES:
        values[name] = data[name] if name in data else user_defaults.get(name, "")
    with open(template_file()) as fp:
        content = render(fp.read(), values)

    dest = os.path.join(dest_dir, OUTPUT)
    if os.path.exists(dest) and not opts.overwrite:
        raise InitError("File %s exists and --force not specified" % dest)
    if opts.pretend:
        print("Would write %s" % dest)
        return
    os.makedirs(dest_dir, exist_ok=True)
    with open(dest, "w") as fo:
        fo.write(content)
