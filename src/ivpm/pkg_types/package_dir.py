#****************************************************************************
#* package_dir.py
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
import platform
import shutil
import dataclasses as dc
from .package_url import PackageURL
from ..project_ops_info import ProjectUpdateInfo
from ..load_plan import clear_prepared_dir, preserve_dir_mode
from ..utils import note, fatal

@dc.dataclass
class PackageDir(PackageURL):
    link : bool = True

    def update(self, update_info : ProjectUpdateInfo):
        # Report this package for cache statistics (directory packages are not cacheable)
        update_info.report_package(cacheable=False)

        if not self.url.startswith("file://"):
            fatal("URL for %s must start with file:// (%s)" % (
                self.name,
                self.url))

        src_path = os.path.expandvars(self.url[7:])

        if not os.path.isdir(src_path):
            fatal("Source (%s) for package %s does not exist" % (
                src_path,
                self.name
            ))
        dst_path = os.path.join(update_info.deps_dir, self.name)

        # The planner owns the "does this need loading?" decision -- an empty
        # directory is not a loaded package (see load_plan.py).
        if update_info.get_load_planner().decide(self).is_resident:
            note("Destination directory for %s exists ... skipping copy" % self.name)
        else:
            note("Populating package %s from %s" % (self.name, src_path))
            if platform.system() == "Windows" or not self.link:
                # dirs_exist_ok so the copy lands *inside* a directory left by a
                # pre-populate step rather than replacing it -- replacing it
                # would discard the group/mode that step configured, and with it
                # the inheritance the copied files are supposed to pick up.
                # copytree still copies the source's mode onto the destination
                # root, so the prepared bits are restored around it.
                with preserve_dir_mode(dst_path):
                    shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
            else:
                # A symlink cannot be created over an existing entry, and the
                # content lives at the target anyway (which carries its own
                # ownership), so an empty prepared directory is simply removed.
                clear_prepared_dir(dst_path)
                os.symlink(src_path, dst_path, target_is_directory=True)

        return super().update(update_info)
    
    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | {"link"}

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "dir"

        if "link" in opts.keys():
            self.link = bool(opts["link"])

    @staticmethod
    def create(name, opts, si) -> 'PackageDir':
        pkg = PackageDir(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="dir",
            description="Local directory — symlinked (or copied on Windows) into packages/",
            params=[
                ParamInfo("url", "file:// URL pointing to the local directory", required=True, type_hint="url"),
                ParamInfo("link", "Create a symlink instead of copying (default: true, ignored on Windows)", type_hint="bool"),
            ],
            notes="URL must start with file://.  Environment variables in the path are expanded.",
        )