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
import dataclasses as dc
from ..proj_info import ProjInfo
from .package_http import PackageHttp

@dc.dataclass
class PackageGhRls(PackageHttp):
    version : str = "latest"
    file : str = None

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
            # Find the appropriate version
            github_com_idx = self.url.find("github.com")
            repo_idx = self.url.find("/", github_com_idx+len("github.com"))
            url = "https://api.github.com/repos/" + self.url[github_com_idx+len("github.com")+1:] + "/releases"
            print("url: %s" % url, flush=True)
            rls_info = httpx.get(url, follow_redirects=True)

            if rls_info.status_code != 200:
                raise Exception("Failed to fetch release info: %d" % rls_info.status_code)
            
            rls_info = json.loads(rls_info.content)
            
            if self.version == "latest":
                rls = rls_info[0]
            else:
                raise NotImplementedError("Only 'latest' is supported for version")
            
            # Find the asset
            file_url = None
            if self.file is None:
                if len(rls["assets"]) == 0:
                    raise Exception("No assets found in release")
                elif len(rls["assets"]) == 1:
                    file_url = rls["assets"][0]["browser_download_url"]
                else:
                    raise Exception("Must specify a file to download")
            else:
                raise NotImplementedError("File specification not yet supported")

            ext = os.path.splitext(file_url)[1]
            print("ext: %s" % str(ext))
            if ext == ".tgz":
                self.src_type = ".tar.gz"
            else:
                self.src_type = ext
                if self.src_type in [".gz", ".xz", ".bz2"]:
                    pdot = file_url.rfind('.')
                    pdot = file_url.rfind('.', 0, pdot-1)
                    self.src_type = file_url[pdot:]

            download_dst = os.path.join(update_info.deps_dir, os.path.basename(file_url))
            self._download_file(file_url, download_dst)
            
            # Install (unpack) the file 
            self._install(download_dst, pkg_dir)

            os.unlink(download_dst)

        if os.path.isdir(pkg_dir):
            return ProjInfo.mkFromProj(pkg_dir)
        else:
            return None

    @staticmethod
    def create(name, opts, si) -> 'PackageGhRls':
        pkg = PackageGhRls(name)
        pkg.process_options(opts, si)
        return pkg

