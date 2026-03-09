#****************************************************************************
#* package_handler_list.py
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
import dataclasses as dc
from itertools import groupby
from typing import List

from ivpm.package import Package
from .package_handler import PackageHandler

@dc.dataclass
class PackageHandlerList(PackageHandler):
    handlers: List[PackageHandler] = dc.field(default_factory=list)
    # Accumulated list of all packages seen in leaf mode; used for root_when evaluation
    _all_pkgs: list = dc.field(default_factory=list, init=False, repr=False)

    def addHandler(self, h):
        self.handlers.append(h)

    # ------------------------------------------------------------------ #
    # Root callbacks                                                       #
    # ------------------------------------------------------------------ #

    def on_root_pre_load(self, update_info):
        """Clear accumulated state and call on_root_pre_load on all handlers."""
        self._all_pkgs = []
        for h in self.handlers:
            h.on_root_pre_load(update_info)

    def on_root_post_load(self, update_info):
        """Evaluate root_when, sort passing handlers by phase, then call each."""
        passing = [h for h in self.handlers if self._root_conditions_pass(h)]
        passing.sort(key=lambda h: type(h).phase)
        for _phase, grp in groupby(passing, key=lambda h: type(h).phase):
            for h in grp:
                h.on_root_post_load(update_info)

    # ------------------------------------------------------------------ #
    # Leaf callbacks                                                       #
    # ------------------------------------------------------------------ #

    def on_leaf_pre_load(self, pkg: Package, update_info):
        for h in self.handlers:
            if not self._has_leaf_behavior(h):
                continue
            if self._leaf_conditions_pass(h, pkg):
                h.on_leaf_pre_load(pkg, update_info)

    def on_leaf_post_load(self, pkg: Package, update_info):
        # Accumulate every package for root_when evaluation
        self._all_pkgs.append(pkg)
        for h in self.handlers:
            if not self._has_leaf_behavior(h):
                continue
            if self._leaf_conditions_pass(h, pkg):
                h.on_leaf_post_load(pkg, update_info)

    # ------------------------------------------------------------------ #
    # Other hooks forwarded to all handlers                                #
    # ------------------------------------------------------------------ #

    def build(self, build_info):
        for h in self.handlers:
            h.build(build_info)

    def get_lock_entries(self, deps_dir: str) -> dict:
        result = {}
        for h in self.handlers:
            result.update(h.get_lock_entries(deps_dir))
        return result

    # ------------------------------------------------------------------ #
    # Condition helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _has_leaf_behavior(h: PackageHandler) -> bool:
        """True if the handler class overrides at least one leaf callback."""
        return (
            type(h).on_leaf_pre_load is not PackageHandler.on_leaf_pre_load
            or type(h).on_leaf_post_load is not PackageHandler.on_leaf_post_load
        )

    def _leaf_conditions_pass(self, h: PackageHandler, pkg) -> bool:
        """Evaluate leaf_when conditions (callable(pkg)->bool) for this package."""
        conditions = type(h).leaf_when
        if conditions is None:
            return True
        return all(cond(pkg) for cond in conditions)

    def _root_conditions_pass(self, h: PackageHandler) -> bool:
        """Evaluate root_when conditions (callable(packages)->bool) against all accumulated packages."""
        conditions = type(h).root_when
        if conditions is None:
            return True
        return all(cond(self._all_pkgs) for cond in conditions)

    # ------------------------------------------------------------------ #
    # Deprecated shims                                                     #
    # ------------------------------------------------------------------ #

    def process_pkg(self, pkg: Package):
        """Deprecated: use on_leaf_post_load()."""
        self.on_leaf_post_load(pkg, None)

    def update(self, update_info):
        """Deprecated: use on_root_post_load()."""
        self.on_root_post_load(update_info)

