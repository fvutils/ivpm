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
from .project_info_reader import ProjectInfoReader
from .update_info import UpdateInfo

@dc.dataclass
class PackageFile(PackageURL):

    def update(self, update_info : UpdateInfo) -> ProjInfo:
        pkg_dir = os.path.join(update_info.packages_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        # Install (unpack) the file 

        # Now, check the package for dependencies
        info : ProjInfo = ProjectInfoReader(pkg_dir).read()

        return info

    def _install_tgz(self, pkg, pkg_path):
        cwd = os.getcwd()
        os.chdir(self.packages_dir)
        
        tf = tarfile.open(pkg_path)

        for fi in tf:
            if fi.name.find("/") != -1:
                fi.name = fi.name[fi.name.find("/")+1:]
                tf.extract(fi, path=pkg.name)
        tf.close()

        os.chdir(cwd)

    def _install_zip(self, pkg, pkg_path):
        ext = os.path.splitext(pkg.name)[1]

        if ext == "":
            if self.debug:
                print("_install_zip: %s %s" % (str(pkg), str(pkg_path)))
            cwd = os.getcwd()
            os.chdir(self.packages_dir)
            sys.stdout.flush()
            with ZipFile(pkg_path, 'r') as zipObj:
                zipObj.extractall(pkg.name)
            os.chdir(cwd)        
        else:
            # Copy the .zip file to the destination
            if self.debug:
                print("_install_zip: copy file")
            shutil.copyfile(
                    pkg_path,
                    os.path.join(self.packages_dir, pkg.name))    


