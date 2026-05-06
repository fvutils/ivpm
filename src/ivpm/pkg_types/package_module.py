#****************************************************************************
#* package_module.py
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
PackageModule -- IVPM source type that resolves an Environment Modules
specifier to a root directory on disk and sets ``pkg.path``.

Usage in ivpm.yaml::

    deps:
    - name: gcc
      src: module
      module: gcc/15.2.0
"""
import dataclasses as dc
import logging
import os
import re
from typing import Optional

from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from ..utils import fatal, note

_logger = logging.getLogger("ivpm.pkg_types.package_module")


def _get_modules_interface(update_info: ProjectUpdateInfo):
    """Lazily create and cache a ModulesInterface on the update_info."""
    mi = getattr(update_info, 'modules_interface', None)
    if mi is not None:
        return mi

    from ..modules_interface import detect_variant

    cfg = update_info.handler_configs.get("modules", {}) or {}
    mi = detect_variant(
        variant_override=cfg.get("variant"),
        cmd_override=cfg.get("modulecmd"),
    )
    update_info.modules_interface = mi
    return mi


@dc.dataclass
class PackageModule(Package):
    """A package resolved via Environment Modules lookup.

    During ``update()``, the module specifier is resolved to a modulefile
    path and root directory.  ``pkg.path`` is set to the root so that
    downstream handlers (agents, fusesoc, etc.) can discover content.
    """
    module: str = None              # e.g. "gcc/15.2.0"
    module_root: str = None         # resolved root directory
    modulefile_path: str = None     # absolute path to the modulefile
    root_override: str = None       # explicit root: from YAML
    resolve_root: bool = False      # opt-in module-show parsing

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "module"

        if "module" in opts:
            self.module = opts["module"]
        elif "version" in opts:
            # Derive specifier from name/version (e.g. vcs/2024.09)
            self.module = "%s/%s" % (self.name, opts["version"])
        else:
            fatal("src: module requires a 'module:' or 'version:' specifier")

        if "root" in opts:
            self.root_override = opts["root"]
        if "resolve-root" in opts:
            self.resolve_root = bool(opts["resolve-root"])

        # Implicit type assignment (unless user specified type: explicitly)
        if not any(td.type_name == "module" for td in self.type_data):
            from ..pkg_content_type import ModuleTypeData
            td = ModuleTypeData()
            td.type_name = "module"
            td.module = self.module
            self.type_data.append(td)

    @staticmethod
    def create(name, opts, si) -> 'PackageModule':
        pkg = PackageModule(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="module",
            description="Environment Module -- resolves a module specifier to its modulefile directory on disk",
            params=[
                ParamInfo("module", "Module specifier (e.g. gcc/15.2.0). If omitted, derived from name/version"),
                ParamInfo("root", "Explicit root directory override"),
                ParamInfo("resolve-root", "Parse module show output to determine the install prefix", type_hint="bool"),
            ],
            notes=(
                "Resolves the module specifier against the Environment Modules "
                "system to find the modulefile location.  The modulefile's parent "
                "directory becomes pkg.path by default, unless root: or "
                "resolve-root: is specified."
            ),
        )

    def update(self, update_info: ProjectUpdateInfo) -> 'ProjInfo':
        from ..proj_info import ProjInfo

        update_info.report_package(cacheable=False)
        mi = _get_modules_interface(update_info)

        # Step 1: Locate the modulefile
        mf_path = mi.module_path(self.module)
        if mf_path is None:
            fatal("Module '%s' is not available (module_path returned None). "
                  "Check MODULEPATH and module availability." % self.module)
        self.modulefile_path = mf_path

        # Step 2: Determine the root directory
        if self.root_override:
            root = os.path.expandvars(self.root_override)
        elif self.resolve_root:
            root = self._resolve_root_from_show(mi)
        else:
            # Default: modulefile directory
            root = os.path.dirname(mf_path) if os.path.isfile(mf_path) else mf_path

        # Step 3: Set pkg.path
        self.path = root
        self.module_root = root

        # Update ModuleTypeData with the module specifier
        for td in self.type_data:
            if hasattr(td, 'module') and td.type_name == "module":
                td.module = self.module

        note("Module '%s' resolved to %s" % (self.module, root))

        # Step 4: Load sub-project info
        return ProjInfo.mkFromProj(root)

    def _resolve_root_from_show(self, mi) -> str:
        """Parse ``module show`` output to determine the install prefix.

        Looks for ``setenv *_HOME``, ``prepend-path PATH``, or ``set root``
        directives.  Falls back to the modulefile directory.
        """
        show_output = mi.module_show(self.module)

        # Try setenv *_HOME /path
        for line in show_output.splitlines():
            m = re.match(r'\s*setenv\s+\w*_HOME\s+(\S+)', line)
            if m:
                candidate = m.group(1)
                if os.path.isdir(candidate):
                    return candidate

        # Try prepend-path PATH /path/bin -> parent
        for line in show_output.splitlines():
            m = re.match(r'\s*prepend-path\s+PATH\s+(\S+)', line)
            if m:
                bin_dir = m.group(1)
                if bin_dir.endswith("/bin") and os.path.isdir(os.path.dirname(bin_dir)):
                    return os.path.dirname(bin_dir)

        # Try set root /path (Tcl variable)
        for line in show_output.splitlines():
            m = re.match(r'\s*set\s+root\s+(\S+)', line)
            if m:
                candidate = m.group(1)
                if os.path.isdir(candidate):
                    return candidate

        # Fallback: modulefile directory
        _logger.warning(
            "resolve-root: could not determine install prefix from "
            "'module show %s'; falling back to modulefile directory",
            self.module)
        if self.modulefile_path:
            return os.path.dirname(self.modulefile_path)
        return "."

    def sync(self, sync_info):
        from ..pkg_sync import PkgSyncResult, SyncOutcome
        return PkgSyncResult(
            name=self.name,
            src_type="module",
            path=self.path or "",
            outcome=SyncOutcome.SKIPPED,
            skipped_reason="environment module (externally managed)",
        )

    def status(self, pkgs_info):
        """Report module name, resolved root, and availability."""
        _logger.info("Module: %s", self.module)
        _logger.info("  Modulefile: %s", self.modulefile_path or "unknown")
        _logger.info("  Root: %s", self.module_root or "unknown")
