#****************************************************************************
#* install_lib.py
#*
#* Copyright 2022 Matthew Ballance and Contributors
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
import shutil
from setuptools.command.install_lib import install_lib as _install_lib

class InstallLib(_install_lib):
    
    def install(self):
        print("install_lib::install")
        build_cmd = self.get_finalized_command('build_ext')
        print("build_cmd: %s" % str(build_cmd))
        install_root = self.get_finalized_command('install').root
        print("install_root: %s" % install_root)
        
        for ext in build_cmd.extensions:
            incdirs = getattr(ext, "include_dirs", None)
            print("Ext: %s" % ext.name)
            if len(incdirs) > 0:
                pkg_name = ext.name.split('.')[0]
                dst = os.path.join(install_root, pkg_name, "share")
                print("dst: %s" % dst)
#                if not os.path.isdir(dst):
#                    os.makedirs(dst)
                dst = os.path.join(dst, "include")
                shutil.copytree(
                    incdirs[0],
                    dst
                )
                pass
                
        return super().install()

