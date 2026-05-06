#****************************************************************************
#* package_fusesoc.py
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
"""
PackageFuseSoC — IVPM source type that resolves FuseSoC VLNV identifiers
to git repositories via the fusesoc-cores registry index.

Usage in ivpm.yaml::

    deps:
    - name: wb_intercon
      src: fusesoc
      vlnv: "::wb_intercon:1.0"
"""
import dataclasses as dc
import logging
import os
import re
import yaml
from typing import Optional

from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from ..utils import fatal, note

_logger = logging.getLogger("ivpm.pkg_types.package_fusesoc")

_FUSESOC_CORES_URL = "https://github.com/fusesoc/fusesoc-cores.git"
_FUSESOC_CORES_DIR = "fusesoc-cores"


@dc.dataclass
class PackageFuseSoC(Package):
    """A package resolved via FuseSoC VLNV lookup.

    During ``update()``, the VLNV string is resolved against the
    fusesoc-cores registry index to find the provider git URL and version.
    The actual fetch is delegated to a ``PackageGit`` instance.
    """
    vlnv: str = None

    def update(self, update_info: ProjectUpdateInfo) -> 'ProjInfo':
        from .package_git import PackageGit
        git_pkg = self._resolve_to_git(update_info)
        result = git_pkg.update(update_info)
        self.path = git_pkg.path  # propagate so handlers can discover .core files
        return result

    def _resolve_to_git(self, update_info: ProjectUpdateInfo):
        """Resolve VLNV to a PackageGit by scanning the fusesoc-cores index."""
        index_dir = self._ensure_index(update_info)
        url, version = self._vlnv_lookup(self.vlnv, index_dir)
        from .package_git import PackageGit
        pkg = PackageGit(name=self.name, url=url, tag=version)
        pkg.process_options({"url": url, "tag": version}, None)
        return pkg

    def _ensure_index(self, update_info: ProjectUpdateInfo) -> str:
        """Ensure the fusesoc-cores index is available in deps-dir.

        The index is fetched as a cached git dependency if not already present.
        """
        deps_dir = update_info.deps_dir
        index_path = os.path.join(deps_dir, _FUSESOC_CORES_DIR)

        if os.path.isdir(index_path):
            note("FuseSoC cores index already available at %s" % index_path)
            return index_path

        from .package_git import PackageGit
        note("Fetching FuseSoC cores index from %s" % _FUSESOC_CORES_URL)
        index_pkg = PackageGit(
            name=_FUSESOC_CORES_DIR,
            url=_FUSESOC_CORES_URL,
            cache=True)
        index_pkg.process_options(
            {"url": _FUSESOC_CORES_URL, "cache": True},
            None)
        index_pkg.update(update_info)
        return index_path

    def _vlnv_lookup(self, vlnv: str, index_dir: str):
        """Walk *.core files in *index_dir*, find the one matching *vlnv*.

        Returns ``(url, version)`` from the matching core file's provider block.
        Raises ``SystemExit`` (via ``fatal()``) on failure.
        """
        target_name = self._parse_vlnv_name(vlnv)

        # Collect candidates: (core_file_path, version)
        candidates = []
        for root, _dirs, files in os.walk(index_dir):
            for fn in files:
                if not fn.endswith(".core"):
                    continue
                core_path = os.path.join(root, fn)
                core_name, core_version = self._read_core_name(core_path)
                if core_name is None:
                    continue
                if core_name == target_name:
                    candidates.append((core_path, core_version))

        if not candidates:
            # Try to suggest close matches
            close = self._find_close_matches(target_name, index_dir)
            hint = ""
            if close:
                hint = "; closest matches: %s" % ", ".join(close[:5])
            fatal(
                "VLNV %s: no matching core found in fusesoc-cores index%s" % (
                    vlnv, hint))

        target_version = self._parse_vlnv_version(vlnv)

        if len(candidates) > 1:
            # Multiple versions: pick the one matching the requested version
            if target_version == "latest":
                candidates.sort(key=lambda c: self._parse_version_key(c[1]))
                # Prefer the latest version that has a usable provider block
                for cpath, cver in reversed(candidates):
                    url, ver = self._extract_provider(cpath)
                    if url is not None:
                        return url, ver
                fatal(
                    "VLNV %s: found %s but no version has a fetchable "
                    "provider block" % (vlnv, target_name))
            else:
                matching = [c for c in candidates if c[1] == target_version]
                if not matching:
                    fatal(
                        "VLNV %s: found %s but no version matches %s; "
                        "available: %s" % (
                            vlnv, target_name, target_version,
                            ", ".join(c[1] for c in candidates)))
                best = matching[0]
        else:
            best = candidates[0]

        url, ver = self._extract_provider(best[0])
        if url is None:
            fatal(
                "VLNV %s: core file has no fetchable provider block "
                "(CAPI=2 cores without a provider must be cloned directly)" % vlnv)
        return url, ver

    @staticmethod
    def _parse_vlnv_name(vlnv: str) -> str:
        """Extract the name component from a VLNV string.

        VLNV format: vendor:library:name:version
        The name is the third component (index 2).
        """
        parts = vlnv.split(":")
        if len(parts) >= 3:
            return parts[2]
        return vlnv

    @staticmethod
    def _parse_vlnv_version(vlnv: str) -> str:
        """Extract the version component from a VLNV string."""
        parts = vlnv.split(":")
        if len(parts) >= 4:
            return parts[3]
        return "latest"

    @staticmethod
    def _read_core_name(core_path: str):
        """Read a .core file and return (name, version) or (None, None).

        Parses the YAML content and extracts the ``name:`` field which
        contains the full VLNV.  Returns the name (3rd component) and
        version (4th component).
        """
        try:
            with open(core_path) as fh:
                data = yaml.safe_load(fh)
        except Exception:
            return None, None

        if data is None or not isinstance(data, dict):
            return None, None

        full_name = data.get("name", "")
        if not full_name or not isinstance(full_name, str):
            return None, None

        parts = full_name.split(":")
        name = parts[2] if len(parts) >= 3 else full_name
        version = parts[3] if len(parts) >= 4 else "0"
        return name, version

    @staticmethod
    def _find_close_matches(target: str, index_dir: str) -> list:
        """Find core names that are close to *target* for helpful error messages."""
        matches = []
        for root, _dirs, files in os.walk(index_dir):
            for fn in files:
                if not fn.endswith(".core"):
                    continue
                name, _ver = PackageFuseSoC._read_core_name(os.path.join(root, fn))
                if name and (name.startswith(target) or target.startswith(name)):
                    matches.append(name)
        return matches

    @staticmethod
    def _extract_provider(core_path: str):
        """Extract git URL and version from the provider block of a .core file.

        Supports two formats:
        - CAPI=2 hybrid: ``provider: {name: github, user: X, repo: Y, version: Z}``
        - CAPI=1-style:  ``provider: {github: "user/repo"}`` or ``provider: {git: {repo: URL}}``

        Returns ``(url, version)`` on success, or ``(None, None)`` if the file
        has no usable provider block.
        """
        try:
            with open(core_path) as fh:
                data = yaml.safe_load(fh)
        except Exception as e:
            fatal("Failed to parse core file %s: %s" % (core_path, e))

        if not isinstance(data, dict):
            fatal("Core file %s has invalid structure" % core_path)

        provider = data.get("provider", None)
        if not isinstance(provider, dict):
            return None, None

        version = data.get("version", "HEAD")

        # CAPI=2 hybrid format: provider: {name: github, user: X, repo: Y, version: Z}
        prov_name = provider.get("name", None)
        if prov_name == "github":
            user = provider.get("user", "")
            repo = provider.get("repo", "")
            prov_version = provider.get("version", None)
            url = "https://github.com/%s/%s.git" % (user, repo)
            if prov_version:
                version = prov_version
            return url, version

        if prov_name == "git":
            url = provider.get("repo", None)
            if not url:
                fatal("Core file %s: git provider missing 'repo' key" % core_path)
            prov_version = provider.get("version", None)
            if prov_version:
                version = prov_version
            return url, version

        # CAPI=1-style dict-key format: provider: {github: ...}
        github = provider.get("github", None)
        if github:
            # github: "user/repo"  or  github: {user: ..., repo: ...}
            if isinstance(github, str):
                url = "https://github.com/%s.git" % github
            elif isinstance(github, dict):
                user = github.get("user", "")
                repo = github.get("repo", "")
                url = "https://github.com/%s/%s.git" % (user, repo)
            else:
                fatal("Unsupported github provider format in %s" % core_path)
            return url, version

        # Generic git provider
        git = provider.get("git", None)
        if isinstance(git, dict):
            url = git.get("repo", None)
            if not url:
                fatal("Core file %s: git provider missing 'repo' key" % core_path)
            git_version = git.get("version", None)
            if git_version:
                version = git_version
            return url, version

        return None, None  # Unknown provider format

    @staticmethod
    def _parse_version_key(version_str: str):
        """Parse a version string into a comparable key for sorting.

        Handles semver-like strings (e.g. "1.2.3", "1.0", "2") and falls
        back to string comparison.
        """
        try:
            parts = [int(p) for p in re.split(r"[._-]", version_str) if p.isdigit()]
            if parts:
                return tuple(parts)
        except (ValueError, TypeError):
            pass
        return (0,)  # fallback for unparseable versions

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "fusesoc"

        if "vlnv" in opts.keys():
            self.vlnv = opts["vlnv"]

    @staticmethod
    def create(name, opts, si) -> 'PackageFuseSoC':
        pkg = PackageFuseSoC(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="fusesoc",
            description="FuseSoC core — resolved by VLNV via the fusesoc-cores registry",
            params=[
                ParamInfo("vlnv", "VLNV identifier (vendor:library:name:version or ::name:version)", required=True),
            ],
            notes=(
                "Resolves the VLNV against the fusesoc-cores registry "
                "(https://github.com/fusesoc/fusesoc-cores.git), extracts the "
                "provider git URL and version, and fetches the repository. "
                "Only github and git providers are supported."
            ),
        )
