#****************************************************************************
#* package_http.py
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
import sys
import urllib
import dataclasses as dc
from .package_file import PackageFile
from ..project_ops_info import ProjectUpdateInfo
from ..utils import note
from ..package import SourceType2Ext

class PackageHttp(PackageFile):

    def update(self, update_info : ProjectUpdateInfo):
        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        if os.path.isdir(pkg_dir):
            note("Skipping %s, since it is already loaded" % self.name)
        else:
            # Need to fetch, then unpack these
            download_dir = os.path.join(update_info.deps_dir, ".download")
                
            if not os.path.isdir(download_dir):
                os.makedirs(download_dir)

            if self.unpack:
                pkg_path = os.path.join(download_dir, 
                                        os.path.basename(self.url))
            else:
                pkg_path = os.path.join(update_info.deps_dir, self.name)
                    
            # TODO: should this be an option?   
            remove_pkg_src = True

            self._download_file(self.url, pkg_path)

            if self.unpack:
                self._install(pkg_path, pkg_dir)
                os.unlink(os.path.join(download_dir, 
                                       os.path.basename(self.url)))
            else:
                # 
                pass

    def _download_file(self, url, dest):
        r = httpx.get(url, follow_redirects=True)
        with open(dest, "wb") as f:
            f.write(r.content)
        pass
            
    @staticmethod
    def create(name, opts, si) -> 'PackageHttp':
        pkg = PackageHttp(name)
        pkg.process_options(opts, si)
        return pkg





