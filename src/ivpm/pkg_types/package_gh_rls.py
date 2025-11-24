#****************************************************************************
#* package_gh_rls.py
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
import os
import httpx
import json
import re
import platform
import subprocess
import dataclasses as dc
from typing import Optional
from ..proj_info import ProjInfo
from .package_http import PackageHttp

@dc.dataclass
class PackageGhRls(PackageHttp):
    version : str = "latest"
    file : Optional[str] = None

    def process_options(self, opts, si):
        super().process_options(opts, si)

        if self.url.find("github.com") == -1:
            raise Exception("GitHub release URL must be specified. URL %s doesn't contain github.com" % self.url)
        
        if "version" in opts.keys():
            self.version = opts["version"]

        if "file" in opts.keys():
            self.file = opts["file"]


    def update(self, update_info):
        pkg_dir = os.path.join(update_info.deps_dir, self.name)

        if not os.path.isdir(pkg_dir):
            # Query release metadata
            github_com_idx = self.url.find("github.com")
            repo_idx = self.url.find("/", github_com_idx + len("github.com"))
            url = "https://api.github.com/repos/" + self.url[github_com_idx + len("github.com") + 1:] + "/releases"
#            print("url: %s" % url, flush=True)
            rls_info = httpx.get(url, follow_redirects=True)

            if rls_info.status_code != 200:
                raise Exception("Failed to fetch release info: %d" % rls_info.status_code)

            rls_info = json.loads(rls_info.content)

            if self.version == "latest":
                found = False
                for r in rls_info:
                    if not r["prerelease"]:
                        rls = r
                        found = True
                        break
                if not found:
                    raise Exception("Failed to find latest release")
            else:
                raise NotImplementedError("Only 'latest' is supported for version")

            # Determine file to download:
            # - If no assets: treat as source-only (use tarball_url or zipball_url)
            # - If assets exist: treat as binary release; select appropriate platform binary
            file_url = None
            forced_ext = None  # When using tarball/zipball URLs (no extension in URL)

            assets = rls.get("assets", [])
            if self.file is not None:
                # Future: explicit asset name selection could be implemented here
                raise NotImplementedError("File specification not yet supported")

            has_binaries = self._has_binary_assets(assets)
            if not has_binaries:
                # Source-only release
                file_url, forced_ext = self._choose_source_url(rls)
                if file_url is None:
                    # Fallback: single asset might be a source archive
                    if len(assets) == 1 and "browser_download_url" in assets[0]:
                        file_url = assets[0]["browser_download_url"]
                    else:
                        raise Exception("No suitable source artifact found (no assets with binaries; tarball/zipball not available)")
            else:
                # Binary release: select platform-appropriate asset
                sysname, machine, glibc = self._get_system_info()
                norm_arch = self._normalize_arch(sysname, machine)

                selected = None
                if sysname == "linux":
                    # Require manylinux-conformant artifact matching arch and <= glibc version
                    if glibc is None:
                        raise Exception("Cannot determine glibc version on Linux. Unable to select a suitable manylinux binary.")
                    selected = self._select_linux_asset(assets, norm_arch, glibc)

                    if selected is None:
                        # Build a helpful error message
                        found = []
                        for a in assets:
                            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
                            tag = self._parse_manylinux(nm)
                            if tag is not None:
                                found.append(f"manylinux_{tag[0]}_{tag[1]}_{tag[2]}")
                        if not found:
                            names = [a.get("name") or os.path.basename(a.get("browser_download_url", "")) for a in assets]
                            raise Exception(f"No suitable Linux binary found for arch={norm_arch} glibc={glibc[0]}.{glibc[1]}. Available assets: {', '.join(names)}")
                        else:
                            raise Exception(f"No suitable Linux manylinux binary found for arch={norm_arch} glibc={glibc[0]}.{glibc[1]}. Available manylinux tags: {', '.join(sorted(set(found)))}")
                elif sysname == "darwin":
                    selected = self._select_macos_asset(assets, norm_arch)
                    if selected is None:
                        names = [a.get("name") or os.path.basename(a.get("browser_download_url", "")) for a in assets]
                        raise Exception(f"No suitable macOS binary found for arch={norm_arch}. Available assets: {', '.join(names)}")
                elif sysname == "windows":
                    selected = self._select_windows_asset(assets, norm_arch)
                    if selected is None:
                        names = [a.get("name") or os.path.basename(a.get("browser_download_url", "")) for a in assets]
                        raise Exception(f"No suitable Windows binary found for arch={norm_arch}. Available assets: {', '.join(names)}")
                else:
                    raise Exception(f"Unsupported platform: {sysname}")

                file_url = selected["browser_download_url"]

            # Determine archive type for unpack
            if forced_ext is not None:
                ext = forced_ext
            else:
                ext = os.path.splitext(file_url)[1]
                if ext == "":
                    # Fallback: assume GitHub tarball if URL lacks extension
                    ext = ".tar.gz"

#            print("ext: %s" % str(ext))
            if ext == ".tgz":
                self.src_type = ".tar.gz"
            else:
                self.src_type = ext
                if self.src_type in [".gz", ".xz", ".bz2"]:
                    pdot = file_url.rfind('.')
                    pdot = file_url.rfind('.', 0, pdot - 1)
                    self.src_type = file_url[pdot:]

            filename = os.path.basename(file_url)
            if forced_ext is not None and not filename.endswith(forced_ext):
                filename = filename + forced_ext
            download_dst = os.path.join(update_info.deps_dir, filename)
            self._download_file(file_url, download_dst)

            # Install (unpack) the file
            self._install(download_dst, pkg_dir)

            os.unlink(download_dst)

        # Conform to PackageHttp.update() which returns None

    # Helper methods for asset selection and platform detection

    def _normalize_arch(self, system, machine):
        system = (system or "").lower()
        m = (machine or "").lower()
        if system == "linux":
            if m in ("x86_64", "amd64"):
                return "x86_64"
            if m in ("aarch64", "arm64"):
                return "aarch64"
            if m.startswith("armv7"):
                return "armv7l"
            return m
        elif system == "darwin":
            if m in ("arm64", "aarch64"):
                return "arm64"
            return "x86_64" if m in ("x86_64", "amd64") else m
        elif system == "windows":
            return "x86_64" if m in ("x86_64", "amd64") else m
        else:
            return m

    def _get_system_info(self):
        sysname = platform.system().lower()
        machine = platform.machine()
        glibc = None
        if sysname == "linux":
            libc_name, libc_ver = platform.libc_ver()
            ver_t = None
            if libc_ver:
                try:
                    parts = libc_ver.split(".")
                    major = int(parts[0]) if len(parts) > 0 else 0
                    minor = int(parts[1]) if len(parts) > 1 else 0
                    ver_t = (major, minor)
                except Exception:
                    ver_t = None
            if ver_t is None:
                try:
                    # Fallback to parsing ldd --version
                    out = subprocess.check_output(["ldd", "--version"], stderr=subprocess.STDOUT, text=True)
                    m = re.search(r"(\d+)\.(\d+)", out)
                    if m:
                        ver_t = (int(m.group(1)), int(m.group(2)))
                except Exception:
                    ver_t = None
            glibc = ver_t
        return sysname, machine, glibc

    def _parse_manylinux(self, name):
        """
        Parse manylinux tag from a filename.
        Returns (glibc_major, glibc_minor, arch) or None
        """
        n = (name or "").lower()
        m = re.search(r"manylinux_(\d+)_(\d+)_([a-z0-9_]+)", n)
        if m:
            return (int(m.group(1)), int(m.group(2)), m.group(3))
        m = re.search(r"manylinux(1|2010|2014)_([a-z0-9_]+)", n)
        if m:
            legacy = m.group(1)
            arch = m.group(2)
            if legacy == "1":
                return (2, 5, arch)       # manylinux1 -> glibc 2.5
            elif legacy == "2010":
                return (2, 12, arch)      # manylinux2010 -> glibc 2.12
            elif legacy == "2014":
                return (2, 17, arch)      # manylinux2014 -> glibc 2.17
        return None

    def _select_linux_asset(self, assets, arch, glibc):
        candidates = []
        for a in assets:
            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
            tag = self._parse_manylinux(nm)
            if tag is None:
                continue
            if tag[2] != arch:
                continue
            ver = (tag[0], tag[1])
            candidates.append((ver, a))
        if not candidates:
            return None
        eligible = [(ver, a) for (ver, a) in candidates if ver <= glibc]
        if eligible:
            eligible.sort(key=lambda x: x[0])
            return eligible[-1][1]
        return None

    def _select_macos_asset(self, assets, arch):
        # Minimal heuristic: look for mac-related tags and arch token
        keys = ("macos", "darwin", "osx")
        for a in assets:
            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
            if any(k in nm for k in keys):
                if arch in nm or (arch == "arm64" and "aarch64" in nm) or (arch == "x86_64" and "amd64" in nm):
                    return a
        # Fallback: any mac-related asset
        for a in assets:
            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
            if any(k in nm for k in keys):
                return a
        return None

    def _select_windows_asset(self, assets, arch):
        keys = ("windows", "win64", "win32", "win")
        arch_aliases = [arch]
        if arch == "x86_64":
            arch_aliases += ["amd64"]
        for a in assets:
            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
            if any(k in nm for k in keys) and any(alias in nm for alias in arch_aliases):
                return a
        # Fallback to any windows asset
        for a in assets:
            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
            if any(k in nm for k in keys):
                return a
        return None

    def _has_binary_assets(self, assets):
        for a in assets:
            nm = (a.get("name") or os.path.basename(a.get("browser_download_url", ""))).lower()
            if self._parse_manylinux(nm) is not None:
                return True
            if any(k in nm for k in ("macos", "darwin", "osx", "windows", "win64", "win32", "win")):
                return True
        return False

    def _choose_source_url(self, rls):
        # Prefer tarball over zipball
        tarball = rls.get("tarball_url")
        if tarball:
            return tarball, ".tar.gz"
        zipball = rls.get("zipball_url")
        if zipball:
            return zipball, ".zip"
        return None, None

    @staticmethod
    def create(name, opts, si) -> 'PackageGhRls':
        pkg = PackageGhRls(name)
        pkg.process_options(opts, si)
        return pkg
