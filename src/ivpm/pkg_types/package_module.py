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
    # logical specifier -- resolved via the modules system
    - name: gcc
      src: module
      module: gcc/15.2.0

    # modulefile path -- resolved relative to the declaring ivpm.yaml
    - name: mytool
      src: module
      modulefile: etc/modulefiles/mytool/1.0

``module:`` and ``modulefile:`` are mutually exclusive; the key -- not the
shape of the value -- selects which form is in play.
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
    module: str = None              # e.g. "gcc/15.2.0" (logical form)
    modulefile_spec: str = None     # path as written in YAML (modulefile: form)
    module_root: str = None         # resolved root directory
    modulefile_path: str = None     # absolute path to the modulefile
    root_override: str = None       # explicit root: from YAML
    resolve_root: bool = False      # opt-in module-show parsing

    # The ModuleTypeData this package added for itself, if any. Retracted by
    # update() when the reader later appends an explicit 'type: module' entry.
    _implicit_td: object = dc.field(default=None, repr=False, compare=False)

    @property
    def is_modulefile(self) -> bool:
        """True when this dep was declared with ``modulefile:`` (a path on disk).

        Derived from ``modulefile_spec`` rather than stored, so the declared
        form and the flag cannot drift apart.
        """
        return self.modulefile_spec is not None

    @property
    def load_spec(self) -> str:
        """The specifier the modules handler emits as ``module load <spec>``."""
        return self.modulefile_path if self.is_modulefile else self.module

    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | {
            "module", "modulefile", "version", "root", "resolve-root"}

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "module"

        # The key is the discriminator: 'module:'/'version:' name a logical
        # module to look up, 'modulefile:' names a file on disk. Mixing them
        # is always a mistake, so reject it rather than picking a winner.
        if "modulefile" in opts and "module" in opts:
            fatal("src: module accepts either a logical module name via 'module:' "
                  "or a modulefile path via 'modulefile:', not both", self)
        if "modulefile" in opts and "version" in opts:
            fatal("src: module accepts either a modulefile path via 'modulefile:' "
                  "or a 'version:' (which derives a logical module name), not both",
                  self)

        if "modulefile" in opts:
            self.modulefile_spec = opts["modulefile"]
        elif "module" in opts:
            self.module = opts["module"]
        elif "version" in opts:
            # Derive specifier from name/version (e.g. vcs/2024.09)
            self.module = "%s/%s" % (self.name, opts["version"])
        else:
            fatal("src: module requires a 'module:', 'modulefile:', or "
                  "'version:' specifier", self)

        if "root" in opts:
            self.root_override = opts["root"]
        if "resolve-root" in opts:
            self.resolve_root = bool(opts["resolve-root"])

        # Implicit type assignment (unless user specified type: explicitly).
        # The reader appends any explicit 'type:' data *after* this runs, so
        # an explicit entry cannot be seen here; _implicit_td records what we
        # added so update() can retract it if one shows up later.
        if not any(td.type_name == "module" for td in self.type_data):
            from ..pkg_content_type import ModuleTypeData
            td = ModuleTypeData()
            td.type_name = "module"
            td.module = self.module
            self.type_data.append(td)
            self._implicit_td = td

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
            description="Environment Module -- resolves a logical module specifier (module:) or a modulefile path (modulefile:) to a directory on disk",
            params=[
                ParamInfo("module", "Logical module specifier (e.g. gcc/15.2.0), looked up via the modules system. If omitted, derived from name/version. Use modulefile: for a path on disk"),
                ParamInfo("modulefile", "Path to a modulefile on disk, resolved relative to the declaring ivpm.yaml. Mutually exclusive with module:/version:"),
                ParamInfo("root", "Explicit root directory override"),
                ParamInfo("resolve-root", "Parse module show output to determine the install prefix", type_hint="bool"),
            ],
            notes=(
                "With module:, the specifier is resolved against the Environment "
                "Modules system to find the modulefile location.  With modulefile:, "
                "the path is used directly and no modules installation is required "
                "(unless resolve-root: is set).  The modulefile's parent directory "
                "becomes pkg.path by default, unless root: or resolve-root: is "
                "specified.  Note that 'module load <path>' requires Modules 4.x "
                "or Lmod."
            ),
        )

    def update(self, update_info: ProjectUpdateInfo) -> 'ProjInfo':
        from ..proj_info import ProjInfo

        update_info.report_package(cacheable=False)
        self._retract_implicit_type_data()

        # Step 1: Locate the modulefile
        #
        # The modulefile: form needs no modules installation at all -- the
        # path is declared, not looked up -- so the interface is created
        # lazily and only when resolve-root: demands it.
        mi = None
        if self.is_modulefile:
            mf_path = self._resolve_modulefile_path(update_info)
        else:
            mi = _get_modules_interface(update_info)
            mf_path = mi.module_path(self.module)
            if mf_path is None:
                fatal("Module '%s' is not available (module_path returned None). "
                      "Check MODULEPATH and module availability." % self.module)
        self.modulefile_path = mf_path

        # Step 2: Determine the root directory
        if self.root_override:
            root = os.path.expandvars(self.root_override)
        elif self.resolve_root:
            if mi is None:
                mi = _get_modules_interface(update_info)
            root = self._resolve_root_from_show(mi, self.load_spec)
        else:
            # Default: modulefile directory
            root = os.path.dirname(mf_path) if os.path.isfile(mf_path) else mf_path

        # Step 3: Set pkg.path
        self.path = root
        self.module_root = root

        # Update ModuleTypeData with the resolved specifier
        for td in self.type_data:
            if td.type_name != "module":
                continue
            if hasattr(td, 'module'):
                td.module = self.module
            if hasattr(td, 'modulefile'):
                td.modulefile = self.modulefile_path if self.is_modulefile else None

        if self.is_modulefile:
            note("Modulefile '%s' resolved to %s (root %s)" % (
                self.modulefile_spec, mf_path, root))
        else:
            note("Module '%s' resolved to %s" % (self.module, root))

        # Step 4: Load sub-project info
        return ProjInfo.mkFromProj(root)

    def _retract_implicit_type_data(self):
        """Drop our self-added ModuleTypeData when the user declared one.

        ``process_options`` runs before the reader appends explicit ``type:``
        entries, so it cannot tell whether the user wrote ``type: module``.
        Left in place, the implicit entry shadows the explicit one and its
        options (notably ``load: false``) are silently ignored.
        """
        if self._implicit_td is None:
            return
        explicit = [td for td in self.type_data
                    if td.type_name == "module" and td is not self._implicit_td]
        if explicit:
            self.type_data.remove(self._implicit_td)
        self._implicit_td = None

    def _resolve_modulefile_path(self, update_info: ProjectUpdateInfo) -> str:
        """Resolve the ``modulefile:`` path to an existing absolute file.

        Relative paths are resolved against the ivpm.yaml that declared the
        dep -- the same contract every other relative path in IVPM follows.
        """
        from ..utils import resolve_pkg_path

        fallback = None
        if hasattr(update_info, "project_root_or_none"):
            fallback = update_info.project_root_or_none()

        path = os.path.expanduser(self.modulefile_spec)
        path = os.path.abspath(resolve_pkg_path(self, path, fallback))

        if os.path.isdir(path):
            fatal("modulefile: '%s' resolves to a directory (%s). "
                  "'modulefile:' takes a path to a modulefile; use 'module:' "
                  "for a logical module name." % (self.modulefile_spec, path),
                  self)
        if not os.path.isfile(path):
            fatal("modulefile '%s' does not exist (resolved to %s)" % (
                self.modulefile_spec, path), self)
        return path

    def _resolve_root_from_show(self, mi, spec: str = None) -> str:
        """Parse ``module show`` output to determine the install prefix.

        Looks for ``setenv *_HOME``, ``prepend-path PATH``, or ``set root``
        directives.  Falls back to the modulefile directory.

        *spec* is what to show: a logical name, or the absolute modulefile
        path for the ``modulefile:`` form.  Defaults to ``self.module``.
        """
        from ..modules_interface import ModulesError

        if spec is None:
            spec = self.module

        try:
            show_output = mi.module_show(spec)
        except ModulesError as e:
            # For the modulefile: form there may be no modules installation
            # at all -- that is a supported configuration, so fall back to
            # the modulefile directory rather than failing the update.
            _logger.debug(
                "resolve-root: 'module show %s' failed (%s); "
                "falling back to modulefile directory", spec, e)
            show_output = ""

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
            spec)
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
        if self.is_modulefile:
            _logger.info("Modulefile: %s (declared)", self.modulefile_spec)
            _logger.info("  Resolved: %s", self.modulefile_path or "unresolved")
        else:
            _logger.info("Module: %s", self.module)
            _logger.info("  Modulefile: %s", self.modulefile_path or "unknown")
        _logger.info("  Root: %s", self.module_root or "unknown")
