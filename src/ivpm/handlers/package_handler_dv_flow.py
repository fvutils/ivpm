#****************************************************************************
#* package_handler_dv_flow.py
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
import yaml
from typing import Dict, Optional, Tuple
from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from .package_handler import PackageHandler
from .handler_phases import HandlerPhase

_logger = logging.getLogger("ivpm.handlers.package_handler_dv_flow")

# Name of the generated package-map file, written into the deps-dir. The root
# project's flow.yaml references it explicitly (package-map: packages/<this>).
DV_FLOW_PACKAGE_MAP = "dv-flow-package-map.yaml"

# The sole flow file detected in each dependency root. flow.dv / flow.yml are
# intentionally not checked.
FLOW_FILENAME = "flow.yaml"


@dc.dataclass
class PackageHandlerDvFlow(PackageHandler):
    name               = "dv-flow"
    description        = "Generates dv-flow-package-map.yaml mapping dv-flow package names to their flow.yaml"
    leaf_when          = None
    root_when          = None
    phase              = HandlerPhase.INTEGRATE
    conditions_summary = ("leaf: dependency packages with a root flow.yaml; "
                          "root: when any such package exists, or package.with.dv-flow.enable is true")

    # ivpm package/dir name -> (dv-flow package.name, path relative to deps-dir)
    flow_pkgs: Dict[str, Tuple[str, str]] = dc.field(default_factory=dict)

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            notes=(
                "Writes <deps-dir>/dv-flow-package-map.yaml enumerating each dependency "
                "that has a root flow.yaml, keyed by the dv-flow package.name read from "
                "that file. Activated automatically when any dependency has a flow.yaml, "
                "or forced on via package.with.dv-flow.enable: true (enable: false "
                "disables it). The map is rewritten on every update and removed when not "
                "activated, so it always reflects the current dependency set."
            ),
        )

    def reset(self):
        self.flow_pkgs = {}

    # ------------------------------------------------------------------ #
    # Leaf: record dependencies that publish a root flow.yaml             #
    # ------------------------------------------------------------------ #

    def on_leaf_post_load(self, pkg: Package, update_info: ProjectUpdateInfo):
        if not getattr(pkg, "path", None):
            return
        flow_path = os.path.join(pkg.path, FLOW_FILENAME)
        if not os.path.isfile(flow_path):
            return

        flow_name = self._read_flow_package_name(flow_path)
        if flow_name is None:
            _logger.warning(
                "dv-flow: %s has %s but no package.name; skipping",
                pkg.name, FLOW_FILENAME)
            return

        rel = "%s/%s" % (pkg.name, FLOW_FILENAME)
        with self._lock:
            self.flow_pkgs[pkg.name] = (flow_name, rel)
        _logger.debug("dv-flow: %s provides package '%s'", pkg.name, flow_name)

    # ------------------------------------------------------------------ #
    # Root: reconcile the map file against the current dependency set     #
    # ------------------------------------------------------------------ #

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        cfg = update_info.handler_configs.get("dv-flow", {}) or {}
        enable = cfg.get("enable", None)   # None=auto-detect, True=force-on, False=off

        activated = (enable is not False) and (bool(self.flow_pkgs) or enable is True)

        if activated:
            self._write_map(update_info.deps_dir)
        else:
            self._remove_stale_map(update_info.deps_dir)

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _read_flow_package_name(self, flow_path: str) -> Optional[str]:
        """Shallow-read the top-level package.name from a flow.yaml."""
        try:
            with open(flow_path) as fp:
                doc = yaml.safe_load(fp)
        except Exception as e:
            _logger.warning("dv-flow: failed to parse %s: %s", flow_path, e)
            return None
        pkg = (doc or {}).get("package")
        if isinstance(pkg, dict):
            name = pkg.get("name")
            return name if isinstance(name, str) and name else None
        return None

    def _write_map(self, deps_dir: str):
        from ..__version__ import _pkg_version
        from ..utils import note

        # Sort by dv-flow name for stable, diff-friendly output. Detect any two
        # dependencies declaring the same package name from different dirs.
        entries = []
        by_name: Dict[str, str] = {}
        for dir_name in sorted(self.flow_pkgs.keys()):
            flow_name, rel = self.flow_pkgs[dir_name]
            if flow_name in by_name:
                _logger.warning(
                    "dv-flow: package name '%s' declared by both %s and %s; keeping %s",
                    flow_name, by_name[flow_name], rel, by_name[flow_name])
                continue
            by_name[flow_name] = rel
            entries.append({"name": flow_name, "path": rel})
        entries.sort(key=lambda e: e["name"])

        doc = {"package-map": {
            "version": 1,
            "generated-by": "ivpm %s" % _pkg_version,
            "packages": entries,
        }}

        out = os.path.join(deps_dir, DV_FLOW_PACKAGE_MAP)
        with open(out, "w") as fp:
            fp.write("# Generated by IVPM dv-flow handler — do not edit.\n")
            fp.write("# Referenced explicitly from the project flow.yaml:\n")
            fp.write("#   package-map: %s/%s\n" % (os.path.basename(deps_dir.rstrip("/")), DV_FLOW_PACKAGE_MAP))
            yaml.safe_dump(doc, fp, sort_keys=False, default_flow_style=False)

        note("Generated %s with %d dv-flow package(s)" % (DV_FLOW_PACKAGE_MAP, len(entries)))

    def _remove_stale_map(self, deps_dir: str):
        out = os.path.join(deps_dir, DV_FLOW_PACKAGE_MAP)
        if os.path.isfile(out):
            os.remove(out)
            _logger.debug("dv-flow: removed stale %s (handler not activated)", DV_FLOW_PACKAGE_MAP)
