#****************************************************************************
#* package_file.py
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
import sys
import shutil
import tarfile
from zipfile import ZipFile
import dataclasses as dc
from .package_url import PackageURL
from .proj_info import ProjInfo
from .update_info import UpdateInfo

@dc.dataclass
class PackageFile(PackageURL):
    unpack : bool = None

    def update(self, update_info : UpdateInfo) -> ProjInfo:
        from .project_info_reader import ProjectInfoReader

        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        if not os.path.isdir(pkg_dir):
            # Install (unpack) the file 
            if self.unpack:
                self._install(self.url, pkg_dir)

        if os.path.isdir(pkg_dir):
            return ProjectInfoReader(pkg_dir).read()
        else:
            return None
    
    def _install(self, pkg_src, pkg_path):
        if self.src_type in (".tar.gz", ".tar.xz", ".tar.bz2"):
            self._install_tgz(pkg_src, pkg_path)
        elif self.src_type in (".jar", ".zip"):
            self._install_zip(pkg_src, pkg_path)
        else:
            raise Exception("Unsupported src_type: %s" % self.src_type)

    def _install_tgz(self, pkg_src, pkg_path):
        cwd = os.getcwd()
        try:
            os.chdir(os.path.dirname(pkg_path))
        
            tf = tarfile.open(pkg_src)

            for fi in tf:
                if fi.name.find("/") != -1:
                    fi.name = fi.name[fi.name.find("/")+1:]
                    tf.extract(fi, path=os.path.basename(pkg_path))
            tf.close()
        finally:
            os.chdir(cwd)

    def _install_zip(self, pkg_src, pkg_path):
            cwd = os.getcwd()
            try:
                os.chdir(os.path.dirname(pkg_path))
                sys.stdout.flush()
                with ZipFile(pkg_src, 'r') as zipObj:
                    zipObj.extractall(os.path.basename(pkg_path))
            finally:
                os.chdir(cwd)        


