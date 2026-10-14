#****************************************************************************
#* wheel_hint.py
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
"""Explain why a PyPI package was built from source.

A package that normally ships wheels and is instead built from its sdist has
almost always met an interpreter or platform its author has no wheel for --
and that, not the build error that follows, is what the user can act on. The
build error itself (a missing header, a Cython file absent from the sdist) is
the package's problem.

Everything here is best effort. A hint that cannot be computed is left out;
it never replaces or delays the failure it decorates.
"""
import json
import logging
import os
import platform
import re
import urllib.request
from typing import List, Optional, Tuple

_logger = logging.getLogger("ivpm.wheel_hint")

# Where a requirement's name ends: a version specifier, extras, a marker, a
# direct reference, or whitespace.
_NAME_END_RE = re.compile(r"[=<>!~;\[\s(@]")


def split_requirement(spec: str) -> Tuple[str, Optional[str]]:
    """``(name, version)`` for an installer-reported requirement.

    Installers name what they built with its pin (uv: ``pkg==1.2.3``); the
    version is ``None`` unless pinned exactly with ``==``.
    """
    spec = (spec or "").strip()
    m = _NAME_END_RE.search(spec)
    if m is None:
        return spec, None
    name = spec[:m.start()]
    version = None
    rest = re.sub(r"^\s*\[[^\]]*\]", "", spec[m.start():])
    vm = re.match(r"\s*==\s*([^\s;,]+)", rest)
    if vm:
        version = vm.group(1)
    return name, version


def venv_python_version(python_dir: str) -> Optional[str]:
    """``"3.14"`` for the venv at *python_dir*, read from its pyvenv.cfg."""
    if not python_dir:
        return None
    try:
        with open(os.path.join(python_dir, "pyvenv.cfg")) as fp:
            for line in fp:
                key, _, value = line.partition("=")
                if key.strip() in ("version_info", "version"):
                    parts = value.strip().split(".")
                    if len(parts) >= 2:
                        return "%s.%s" % (parts[0], parts[1])
    except OSError:
        pass
    return None


def host_platform() -> Tuple[str, str]:
    """``(os, machine)`` as wheel platform tags spell them."""
    system = platform.system().lower()
    os_tag = {"darwin": "macosx", "windows": "win"}.get(system, system)
    machine = platform.machine().lower()
    if machine == "amd64":
        machine = "x86_64"
    elif machine == "arm64" and os_tag == "linux":
        machine = "aarch64"
    return os_tag, machine


def _platform_matches(plat_tag: str, os_tag: str, machine: str) -> bool:
    if plat_tag == "any":
        return True
    if os_tag == "win":
        return plat_tag.startswith("win") and (
            machine in plat_tag or (machine == "x86_64" and plat_tag == "win_amd64"))
    if os_tag == "macosx":
        return plat_tag.startswith("macosx") and (
            machine in plat_tag or "universal" in plat_tag)
    return os_tag in plat_tag and machine in plat_tag


def _pythons_from_filenames(filenames: List[str], os_tag: str,
                            machine: str) -> Optional[List[str]]:
    """CPython versions with a wheel for this platform, or None for a
    pure-Python / abi3 wheel (which any interpreter can use)."""
    versions = set()
    for fn in filenames:
        if not fn.endswith(".whl"):
            continue
        parts = fn[:-4].split("-")
        if len(parts) < 5:
            continue
        py_tag, abi_tag, plat_tags = parts[-3], parts[-2], parts[-1]
        if not any(_platform_matches(p, os_tag, machine)
                   for p in plat_tags.split(".")):
            continue
        if abi_tag in ("none", "abi3"):
            return None
        for t in py_tag.split("."):
            m = re.match(r"cp(\d)(\d+)$", t)
            if m:
                versions.add((int(m.group(1)), int(m.group(2))))
    return ["%d.%d" % v for v in sorted(versions)]


def _index_is_pypi() -> bool:
    """False when the user points the installer at another index: what
    pypi.org publishes is then not what the installer saw."""
    for var in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL"):
        value = os.environ.get(var)
        if value and "pypi.org" not in value:
            return False
    return True


def pypi_wheel_pythons(name: str, version: str,
                       timeout: float = 3.0) -> Optional[List[str]]:
    """CPython versions pypi.org has wheels for, for this host's platform.

    None when it cannot be told: no version, the query is disabled
    (``IVPM_NO_INDEX_QUERY``), another index is configured, the network is
    unavailable, or a wheel exists that any interpreter could use.
    """
    if not name or not version:
        return None
    if os.environ.get("IVPM_NO_INDEX_QUERY") or not _index_is_pypi():
        return None
    url = "https://pypi.org/pypi/%s/%s/json" % (name, version)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as rsp:
            data = json.load(rsp)
    except Exception as e:
        _logger.debug("wheel query for %s %s failed: %s", name, version, e)
        return None
    filenames = [u.get("filename", "") for u in data.get("urls", [])]
    os_tag, machine = host_platform()
    return _pythons_from_filenames(filenames, os_tag, machine)


def _ranges(versions: List[str]) -> str:
    """``3.8-3.13`` for a contiguous run of minor versions; comma-joined runs
    otherwise."""
    nums = [tuple(int(x) for x in v.split(".")) for v in versions]
    runs = []
    for v in nums:
        if runs and runs[-1][1][0] == v[0] and runs[-1][1][1] + 1 == v[1]:
            runs[-1] = (runs[-1][0], v)
        else:
            runs.append((v, v))
    return ", ".join(("%d.%d" % a) if a == b else ("%d.%d-%d.%d" % (a + b))
                     for a, b in runs)


def explain_source_build(name: str, version: Optional[str],
                         python_dir: str = None,
                         query=pypi_wheel_pythons) -> Tuple[List[str], List[str]]:
    """``(why, next_steps)`` lines for a PyPI package whose source build failed.

    Both lists are empty when there is nothing useful to say.
    """
    pyver = venv_python_version(python_dir)
    os_tag, machine = host_platform()
    env = "CPython %s, %s %s" % (pyver, os_tag, machine) if pyver else \
        "%s %s" % (os_tag, machine)

    why = ["no prebuilt wheel of '%s' matched this environment (%s)," % (name, env),
           "so the installer built it from source, and that build failed."]
    steps = []

    pythons = query(name, version) if version else None
    if pythons:
        why.append("pypi.org has %s %s wheels of %s %s for CPython %s."
                   % (os_tag, machine, name, version, _ranges(pythons)))
        if pyver and pyver not in pythons:
            steps.append("recreate the Python environment with a Python that "
                         "'%s' publishes wheels for (%s)"
                         % (name, _ranges(pythons)))
            steps.append("or pin '%s' to a release that has a wheel for "
                         "CPython %s" % (name, pyver))
    elif pythons == [] and version:
        why.append("pypi.org has no %s %s wheels of %s %s at all."
                   % (os_tag, machine, name, version))
        steps.append("install the build prerequisites '%s' needs, or pin a "
                     "release that has wheels for this platform" % name)
    return why, steps
