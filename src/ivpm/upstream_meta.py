#****************************************************************************
#* upstream_meta.py
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
"""Project metadata read from a package's *upstream* manifest.

A package sourced from PyPI or npm already states its license, home page and
documentation URL in ``pyproject.toml`` / ``package.json``. Restating those in
``ivpm.yaml`` would be busywork, so ``ivpm show bom`` falls back to them.

Two rules hold throughout:

* **The ivpm.yaml manifest always wins.** Upstream metadata only fills a field
  the manifest left unset.
* **This is documentation only.** Nothing here is consulted by resolution,
  fetching, caching or locking; a malformed upstream manifest yields ``{}``
  rather than an error.
"""
import json
import os
from typing import Dict, Optional

# The metadata fields a BOM reports. Keys are ivpm's own spelling.
META_KEYS = ("description", "license", "homepage", "documentation", "version")


def _load_toml(path: str) -> Optional[dict]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return None
    try:
        with open(path, "rb") as fp:
            return tomllib.load(fp)
    except Exception:
        return None


def _from_pyproject(pkg_dir: str) -> Dict[str, str]:
    data = _load_toml(os.path.join(pkg_dir, "pyproject.toml"))
    if not isinstance(data, dict):
        return {}
    project = data.get("project")
    if not isinstance(project, dict):
        return {}

    out: Dict[str, str] = {}
    for src, dst in (("description", "description"), ("version", "version")):
        val = project.get(src)
        if isinstance(val, str) and val:
            out[dst] = val

    # PEP 621 allows either a plain SPDX string or a table
    # ({file = ...} / {text = ...}); only the expression is reportable.
    lic = project.get("license")
    if isinstance(lic, str) and lic:
        out["license"] = lic
    elif isinstance(lic, dict) and isinstance(lic.get("text"), str):
        out["license"] = lic["text"]

    urls = project.get("urls")
    if isinstance(urls, dict):
        # URL labels are free-form and only conventionally capitalised, so
        # match case-insensitively rather than on an exact spelling.
        lowered = {str(k).lower(): v for k, v in urls.items()}
        for key, dst in (("homepage", "homepage"),
                         ("documentation", "documentation"),
                         ("docs", "documentation")):
            val = lowered.get(key)
            if isinstance(val, str) and val and dst not in out:
                out[dst] = val
    return out


def _from_package_json(pkg_dir: str) -> Dict[str, str]:
    path = os.path.join(pkg_dir, "package.json")
    try:
        with open(path) as fp:
            data = json.load(fp)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, str] = {}
    for src, dst in (("description", "description"), ("version", "version"),
                     ("license", "license"), ("homepage", "homepage")):
        val = data.get(src)
        if isinstance(val, str) and val:
            out[dst] = val
    return out


def read_upstream_metadata(pkg_dir: str) -> Dict[str, str]:
    """Return whatever metadata *pkg_dir*'s upstream manifest declares.

    Only keys actually present are returned, so the result merges cleanly over
    a manifest-derived dict. ``pyproject.toml`` is preferred over
    ``package.json`` when a package somehow carries both.
    """
    if not pkg_dir or not os.path.isdir(pkg_dir):
        return {}
    meta = _from_package_json(pkg_dir)
    meta.update(_from_pyproject(pkg_dir))
    return meta


def package_metadata(pkg_dir: str) -> Dict[str, Optional[str]]:
    """Return *pkg_dir*'s project metadata, manifest-first.

    ``ivpm.yaml`` is authoritative for every field it declares; upstream
    metadata fills the rest. Keys the package declares nowhere are absent.
    """
    meta = read_upstream_metadata(pkg_dir)

    ivpm_yaml = os.path.join(pkg_dir, "ivpm.yaml")
    if os.path.isfile(ivpm_yaml):
        try:
            from .proj_info import ProjInfo
            info = ProjInfo.mkFromProj(pkg_dir)
        except Exception:
            # Documentation only: a manifest this package cannot parse must not
            # take the BOM down with it.
            info = None
        if info is not None:
            for key in META_KEYS:
                val = getattr(info, key, None)
                if val:
                    meta[key] = str(val)
    return meta
