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

        if os.path.isdir(dst_path) or os.path.islink(dst_path):
            note("Destination directory for %s exists ... skipping copy" % self.name)
        else:
            note("Populating package %s from %s" % (self.name, src_path))
            if platform.system() == "Windows" or not self.link:
                shutil.copytree(src_path, dst_path)
            else:
                os.symlink(src_path, dst_path, target_is_directory=True)

        return super().update(update_info)
    
    def process_options(self, opts, si):
        super().process_options(opts, si)

        if "link" in opts.keys():
            self.link = bool(opts["link"]) 

    @staticmethod
    def create(name, opts, si) -> 'PackageDir':
        print("create: name=%s" % name)
        pkg = PackageDir(name)
        print("pkg.name: %s" % pkg.name)
        pkg.process_options(opts, si)
        print("pkg.name: %s" % pkg.name)
        return pkg