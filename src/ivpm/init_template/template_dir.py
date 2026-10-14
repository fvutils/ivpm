#****************************************************************************
#* template_dir.py
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
"""Template and collection directories, read without copier installed.

A *template directory* contains ``copier.yml`` (or ``copier.yaml``).  A
*collection* is a directory whose immediate subdirectories are templates; the
scan is deliberately not recursive.

Metadata comes from an optional ``_ivpm:`` mapping in the copier config.  It
is read with a lenient loader that maps copier's custom tags (``!include``)
to ``None``, so ``ivpm show init-templates`` works without copier and a
template that IVPM cannot fully parse is still listed rather than dropped.
"""
import dataclasses as dc
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .template_info import InitTemplate, valid_template_name

_logger = logging.getLogger("ivpm.init_template")

CONFIG_NAMES = ("copier.yml", "copier.yaml")


class _LenientLoader(yaml.SafeLoader):
    """SafeLoader that turns any unknown ``!tag`` node into ``None``."""


_LenientLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


@dc.dataclass
class QuestionInfo:
    """One copier question, normalized for display (best effort)."""
    name: str
    type: str = ""
    default: Any = None
    help: str = ""


def config_file(path: str) -> Optional[str]:
    """The copier config file in *path*, or ``None``."""
    for fn in CONFIG_NAMES:
        p = os.path.join(path, fn)
        if os.path.isfile(p):
            return p
    return None


def is_template_dir(path: str) -> bool:
    return os.path.isdir(path) and config_file(path) is not None


def read_template_config(path: str) -> Tuple[Dict[str, Any], List[str]]:
    """Read the merged copier config of template dir *path*.

    copier allows several YAML documents in one file and merges them, so every
    mapping document is merged here too.  Never raises: a parse failure yields
    ``{}`` and a warning.
    """
    warnings: List[str] = []
    cfg = config_file(path)
    if cfg is None:
        return {}, ["%s: no copier.yml" % path]
    try:
        with open(cfg) as fp:
            docs = list(yaml.load_all(fp, Loader=_LenientLoader))
    except (OSError, yaml.YAMLError) as e:
        _logger.debug("cannot parse %s: %s", cfg, e)
        return {}, ["%s: cannot be parsed (%s); listed without metadata"
                    % (cfg, str(e).splitlines()[0] if str(e) else type(e).__name__)]
    merged: Dict[str, Any] = {}
    for doc in docs:
        if isinstance(doc, dict):
            merged.update(doc)
    return merged, warnings


def read_template_meta(path: str) -> Tuple[Dict[str, Any], List[str]]:
    """Return the ``_ivpm`` mapping of template dir *path* and any warnings."""
    data, warnings = read_template_config(path)
    meta = data.get("_ivpm")
    if meta is None:
        return {}, warnings
    if not isinstance(meta, dict):
        warnings.append("%s: '_ivpm' is not a mapping; ignored" % config_file(path))
        return {}, warnings
    return meta, warnings


def read_questions(path: str) -> List[QuestionInfo]:
    """The questions declared in template dir *path*, in file order.

    Handles copier's simple form (``key: default``) and full form (a mapping
    with ``type``/``default``/``help``).  Keys starting with ``_`` are copier
    settings, not questions.
    """
    data, _ = read_template_config(path)
    questions = []
    for key, val in data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(val, dict):
            questions.append(QuestionInfo(
                name=key,
                type=str(val.get("type", "") or ""),
                default=val.get("default"),
                help=str(val.get("help", "") or "")))
        else:
            questions.append(QuestionInfo(name=key, default=val))
    return questions


def apply_meta(t: InitTemplate, meta: Dict[str, Any]) -> None:
    """Fill fields of *t* the provider left empty from template metadata.

    Provider descriptor fields win over ``_ivpm`` fields.  ``trusted`` is
    never taken from the template: a template cannot vouch for itself.
    """
    for key in ("description", "category", "next_steps"):
        val = meta.get(key)
        if not getattr(t, key) and isinstance(val, (str, int, float)) and val != "":
            setattr(t, key, str(val))
    if meta.get("hidden") is True:
        t.hidden = True


def template_from_dir(path: str,
                      origin: str = "",
                      warnings: Optional[List[str]] = None) -> InitTemplate:
    """Build a descriptor for template dir *path* from its ``_ivpm`` block."""
    meta, w = read_template_meta(path)
    if warnings is not None:
        warnings.extend(w)
    name = meta.get("name")
    if name is None:
        name = os.path.basename(os.path.normpath(path))
    elif not valid_template_name(name):
        if warnings is not None:
            warnings.append("%s: invalid _ivpm.name %r; using the directory name"
                            % (path, name))
        name = os.path.basename(os.path.normpath(path))
    t = InitTemplate(name=name, src=os.path.abspath(path), origin=origin)
    apply_meta(t, meta)
    return t


def scan(path: str,
         origin: str = "",
         warnings: Optional[List[str]] = None) -> List[InitTemplate]:
    """Templates found at *path*: the dir itself, or each template subdir.

    Results for a collection are sorted by directory name.  A missing path,
    or a directory holding no templates, yields ``[]`` and a warning.
    """
    if warnings is None:
        warnings = []
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        warnings.append("%s: template path does not exist" % path)
        return []
    if is_template_dir(path):
        return [template_from_dir(path, origin, warnings)]
    ret = []
    try:
        entries = sorted(os.listdir(path))
    except OSError as e:
        warnings.append("%s: cannot be listed (%s)" % (path, e))
        return []
    for ent in entries:
        sub = os.path.join(path, ent)
        if is_template_dir(sub):
            ret.append(template_from_dir(sub, origin, warnings))
    if not ret:
        warnings.append("%s: no copier templates found" % path)
    return ret
