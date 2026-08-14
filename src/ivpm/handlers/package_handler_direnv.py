#****************************************************************************
#* package_handler_direnv.py
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
import dataclasses as dc
import logging
import os
import toposort
from typing import Dict
from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from .package_handler import PackageHandler
from .handler_phases import HandlerPhase
from .scope_keys import pkg_key, pkg_rel_dir, resolver_key

_logger = logging.getLogger("ivpm.handlers.package_handler_direnv")


@dc.dataclass
class PackageHandlerDirenv(PackageHandler):
    name               = "direnv"
    description        = "Collects envrc files from packages and generates a combined packages.envrc"
    leaf_when          = None
    root_when          = None
    phase              = HandlerPhase.ENVIRONMENT
    conditions_summary = "leaf: non-PyPI packages with export.envrc, or with direnv.envrc specified in package.with; root: only when at least one such package is present"

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            notes=(
                "Generates packages/packages.envrc that sources each package's envrc in topological order. "
                "By default, only export.envrc is collected. A package can publish any envrc file by "
                "specifying package: { with: { direnv: { envrc: <relpath> } } } in its ivpm.yaml."
            ),
        )
    # scope key (package path relative to the root deps-dir; the bare package
    # name in a flat workspace) -> (Package, envrc filename)
    envrc_pkgs: Dict[str, tuple] = dc.field(default_factory=dict)

    def reset(self):
        self.envrc_pkgs = {}

    def on_leaf_post_load(self, pkg: Package, update_info):
        """Record packages that publish an envrc file.

        The envrc file is chosen as follows:
        1. If the package's own ivpm.yaml specifies
           ``package: { with: { direnv: { envrc: <relpath> } } }``, that path
           is used (relative to the package root).
        2. Otherwise, only ``export.envrc`` is looked for automatically.
           A bare ``.envrc`` is intentionally not collected unless the package
           explicitly opts in via the config above.
        """
        if not hasattr(pkg, "path") or pkg.path is None:
            return
        if getattr(pkg, "src_type", None) == "pypi":
            return

        # Check whether the package's own ivpm.yaml declares an explicit envrc.
        explicit_envrc = None
        if pkg.proj_info is not None:
            direnv_cfg = pkg.proj_info.handler_configs.get("direnv")
            if isinstance(direnv_cfg, dict):
                explicit_envrc = direnv_cfg.get("envrc")

        if explicit_envrc is not None:
            candidate = explicit_envrc
            if os.path.isfile(os.path.join(pkg.path, candidate)):
                with self._lock:
                    self.envrc_pkgs[pkg_key(pkg)] = (pkg, candidate)
                _logger.debug("Package %s exports %s (explicit)", pkg.name, candidate)
            else:
                _logger.warning(
                    "Package %s specifies direnv.envrc=%s but file not found",
                    pkg.name, candidate)
        else:
            # Default: only auto-collect export.envrc
            if os.path.isfile(os.path.join(pkg.path, "export.envrc")):
                with self._lock:
                    self.envrc_pkgs[pkg_key(pkg)] = (pkg, "export.envrc")
                _logger.debug("Package %s has export.envrc", pkg.name)

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        # Root project env: directives are emitted here too (delegated to
        # direnv), so generate the file when there is anything to write --
        # either packages publishing envrc files, or root env: settings.
        env_settings = getattr(update_info, "env_settings", None) or []
        if not self.envrc_pkgs and not env_settings:
            _logger.debug("No packages with envrc files and no env: settings; skipping packages.envrc generation")
            return

        # Build dependency map for topological sort (only among envrc packages).
        # An edge runs from a package to each dependency IT resolved, so
        # dependencies are sourced first. Keyed on scope keys, which is what
        # keeps two same-named packages in different scopes distinct.
        deps_m: Dict[str, set] = {}
        for key in self.envrc_pkgs.keys():
            deps_m.setdefault(key, set())
        for key, (pkg, _) in self.envrc_pkgs.items():
            parent = resolver_key(pkg)
            if parent is not None and parent in self.envrc_pkgs:
                deps_m.setdefault(parent, set()).add(key)

        # Topological order: dependencies before dependents
        ordered = []
        for pkg_set in toposort.toposort(deps_m):
            for pkg_name in pkg_set:
                ordered.append(pkg_name)

        deps_dir = update_info.deps_dir
        output_path = os.path.join(deps_dir, "packages.envrc")

        _logger.debug("Writing packages.envrc to %s", output_path)

        abs_deps_dir = os.path.abspath(deps_dir)
        # Project root: prefer the value carried on update_info; fall back to
        # the parent of the deps dir if the layout wasn't recorded.
        proj_dir = update_info.project_dir or os.path.dirname(abs_deps_dir)
        with open(output_path, "w") as fp:
            fp.write("# Generated by IVPM — do not edit manually\n")
            fp.write("export IVPM_PACKAGES=%s\n" % abs_deps_dir)
            fp.write("export IVPM_PROJECT=%s\n" % os.path.abspath(proj_dir))
            for key in ordered:
                pkg, envrc_file = self.envrc_pkgs[key]
                # Emit the path the package actually occupies, relative to the
                # root deps-dir -- a nested package is not at ./<name>/.
                fp.write("source_env ./%s/%s\n" % (
                    pkg_rel_dir(pkg, abs_deps_dir), envrc_file))
            # Root project env: directives are emitted last so the project's
            # own declarations take precedence over package-provided envrc.
            if env_settings:
                fp.write("# --- ivpm:env (project) ---\n")
                for es in env_settings:
                    fp.write("%s\n" % es.as_direnv())

        from ..utils import note
        note("Generated packages.envrc with %d entries" % len(ordered))
