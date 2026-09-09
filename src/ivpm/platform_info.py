"""Shared platform probe backing the ``ivpm_*`` manifest variables.

A single place that answers "what machine is this?" in the spelling IVPM
publishes to manifests. ``package_gh_rls`` had a private version of this for
release-asset selection; that probe stays *usable* by the matcher but the
canonical answers now live here.

Two contracts are load-bearing and deliberately differ from the gh-rls
matcher's private helpers:

* ``arch`` is ``arm64`` on **every** OS. The gh-rls matcher's
  ``_normalize_arch`` returns ``aarch64`` on Linux because it matches against
  release *filenames* that use that spelling; that is a matching detail, not a
  fact about the machine.
* the libc *family* is established before any version number is trusted. A
  version regex applied to whatever ``ldd --version`` printed cannot tell musl
  from glibc, and reporting musl 1.2 as glibc 1.2 is worse than reporting
  nothing.

Every field is a ``str``; unknown is ``""`` and never ``None``, so ``${{...}}``
substitution can never produce the text ``None``.
"""
import dataclasses as dc
import functools
import os
import platform
import re
import subprocess
from typing import Dict

# Variables under this prefix are owned by IVPM. A user ``vars:`` block that
# declares one is a fatal error (see variables.resolve_variables).
RESERVED_PREFIX = "ivpm_"


@dc.dataclass(frozen=True)
class PlatformInfo:
    os: str = ""             # "linux" | "macos" | "windows" | <raw, lowercased>
    arch: str = ""           # "x86_64" | "arm64" | <raw machine, lowercased>
    libc: str = ""           # "glibc" | "musl" | ""   ("" off Linux)
    libc_version: str = ""   # "2.39" | "1.2.4" | ""
    distro: str = ""         # "ubuntu" | ""           (Linux only)
    distro_version: str = ""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def probe() -> PlatformInfo:
    """Probe the running system. Cached -- it may shell out to ``ldd``."""
    sysname = (platform.system() or "").lower()
    os_name = _normalize_os(sysname)
    arch = _normalize_arch(platform.machine())

    libc, libc_version = ("", "")
    distro, distro_version = ("", "")
    if os_name == "linux":
        libc, libc_version = _probe_libc()
        distro, distro_version = _probe_distro()

    return PlatformInfo(
        os=os_name,
        arch=arch,
        libc=libc,
        libc_version=libc_version,
        distro=distro,
        distro_version=distro_version)


def as_variables(pi: PlatformInfo) -> Dict[str, str]:
    """Expand *pi* into the nine ``ivpm_*`` variables a manifest may read."""
    major, minor = _split_version(pi.libc_version)
    return {
        "ivpm_os": pi.os,
        "ivpm_arch": pi.arch,
        "ivpm_platform": "%s-%s" % (pi.os, pi.arch),
        "ivpm_libc": pi.libc,
        "ivpm_libc_version": pi.libc_version,
        "ivpm_libc_major": major,
        "ivpm_libc_minor": minor,
        "ivpm_distro": pi.distro,
        "ivpm_distro_version": pi.distro_version,
    }


def _reset_probe_cache():
    """Drop the memoized probe. For tests that fake the environment."""
    probe.cache_clear()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _normalize_os(sysname: str) -> str:
    if sysname == "darwin":
        return "macos"
    return sysname


def _normalize_arch(machine: str) -> str:
    """Canonical arch, identical on every OS.

    Deliberately *not* the same function as
    ``PackageGhRls._normalize_arch``: see the module docstring.
    """
    m = (machine or "").lower()
    if m in ("x86_64", "amd64", "x64"):
        return "x86_64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def _read_os_release() -> Dict[str, str]:
    """Parse ``/etc/os-release`` into a dict. ``{}`` when unavailable."""
    result: Dict[str, str] = {}
    try:
        if not os.path.exists("/etc/os-release"):
            return result
        with open("/etc/os-release", "r") as fp:
            for line in fp.read().split("\n"):
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    except Exception:
        return {}
    return result


def _ldd_version_text() -> str:
    """Raw text of ``ldd --version``. ``""`` when ldd is absent or fails.

    musl's ldd exits non-zero and writes to stderr, so neither is treated as
    a failure -- the text is what matters.
    """
    try:
        proc = subprocess.run(
            ["ldd", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True)
        return proc.stdout or ""
    except Exception:
        return ""


def _probe_libc():
    """Return ``(family, version)``, establishing the family *first*.

    The order is deliberate: ``platform.libc_ver()`` (authoritative when it
    answers, and it only ever names glibc), then ``/etc/os-release`` (Alpine
    is musl by construction), then the *text* of ``ldd --version``. A version
    number is only trusted once a family is known -- extracting ``(\\d+)\\.(\\d+)``
    from unclassified ldd output is how musl 1.2 gets reported as glibc 1.2.
    """
    try:
        libc_name, libc_ver = platform.libc_ver()
    except Exception:
        libc_name, libc_ver = ("", "")

    if libc_name and "glibc" in libc_name.lower():
        return ("glibc", libc_ver or _version_from_text(_ldd_version_text()))

    osr = _read_os_release()
    ids = " ".join([osr.get("ID", ""), osr.get("ID_LIKE", "")]).lower()
    if "alpine" in ids.split() or "alpine" in ids:
        return ("musl", _version_from_text(_ldd_version_text(), musl=True))

    text = _ldd_version_text()
    low = text.lower()
    if "musl" in low:
        return ("musl", _version_from_text(text, musl=True))
    if "glibc" in low or "gnu libc" in low or "gnu c library" in low:
        return ("glibc", _version_from_text(text))

    # Family unknown: say so rather than invent one from a number.
    return ("", "")


def _version_from_text(text: str, musl: bool = False) -> str:
    """Extract a dotted version from ldd output. ``""`` when absent.

    musl prints ``musl libc (x86_64)\\nVersion 1.2.4``, so the ``Version``
    line is preferred; the generic form covers glibc's
    ``ldd (Ubuntu GLIBC 2.39-0ubuntu8) 2.39``.
    """
    if not text:
        return ""
    m = re.search(r"Version\s+(\d+)\.(\d+)(?:\.(\d+))?", text)
    if m is None and not musl:
        m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if m is None:
        return ""
    parts = [g for g in m.groups() if g is not None]
    return ".".join(parts)


def _split_version(version: str):
    """``"2.39"`` -> ``("2", "39")``; missing components are ``""``."""
    if not version:
        return ("", "")
    parts = version.split(".")
    major = parts[0] if len(parts) > 0 else ""
    minor = parts[1] if len(parts) > 1 else ""
    return (major, minor)


def _probe_distro():
    """``(id, version_id)`` from ``/etc/os-release``; ``("", "")`` if unknown.

    Ported from ``PackageGhRls._get_linux_distro_info``, which now delegates
    here. Both fields are required, matching the original: a distro with no
    VERSION_ID cannot participate in the distro-tagged asset selection this
    was written for.
    """
    osr = _read_os_release()
    distro_id = osr.get("ID", "").lower()
    version_id = osr.get("VERSION_ID", "")
    if distro_id and version_id:
        return (distro_id, version_id)
    return ("", "")
