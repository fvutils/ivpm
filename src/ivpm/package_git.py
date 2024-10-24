#****************************************************************************
#* package_git.py
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
import subprocess
import dataclasses as dc
from .package import Package
from .proj_info import ProjInfo
from .project_info_reader import ProjectInfoReader
from .update_info import UpdateInfo
from .utils import note, fatal

@dc.dataclass
class PackageGit(Package):
    branch : str = None
    commit : str = None
    tag : str = None
    depth : str = None
    anonymous : bool = None

    def update(self, update_info : UpdateInfo) -> ProjInfo:
        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        if os.path.exists(pkg_dir):
            note("package %s is already loaded" % self.name)
        else:
            note("loading package %s" % self.name)

            cwd = os.getcwd()
            os.chdir(update_info.deps_dir)
            sys.stdout.flush()

            git_cmd = ["git", "clone"]
        
            if self.depth is not None:
                git_cmd.extend(["--depth", str(self.depth)])

            if self.branch is not None:
                git_cmd.extend(["-b", str(self.branch)])

            # Modify the URL to use SSH/key-based clones
            # unless anonymous cloning was requested
            use_anonymous = update_info.anonymous_git

            if self.anonymous is not None:
                use_anonymous = self.anonymous

            if not use_anonymous:
                print("NOTE: using dev URL")
                delim_idx = self.url.find("://")
                url = self.url[delim_idx+3:]
                first_sl_idx = url.find('/')
                url = "git@" + url[:first_sl_idx] + ":" + url[first_sl_idx+1:]
                print("Final URL: %s" % url)
                git_cmd.append(url)
            else:
                print("NOTE: using anonymous URL")
                git_cmd.append(self.url)

            # Clone to a directory with same name as package        
            git_cmd.append(self.name)
            
            print("git_cmd: \"" + str(git_cmd) + "\"")
            status = subprocess.run(git_cmd)
            os.chdir(cwd)
        
            if status.returncode != 0:
                fatal("Git command \"%s\" failed" % str(git_cmd))

            # Checkout a specific commit            
            if self.commit is not None:
                os.chdir(os.path.join(self.deps_dir, self.name))
                git_cmd = "git reset --hard %s" % self.commit
                status = os.system(git_cmd)
            
                if status != 0:
                    fatal("Git command \"%s\" failed" % str(git_cmd))
                os.chdir(cwd)
            
        
            # TODO: Existence of .gitmodules should trigger this
            if os.path.isfile(os.path.join(update_info.deps_dir, self.name, ".gitmodules")):
                os.chdir(os.path.join(update_info.deps_dir, self.name))
                sys.stdout.flush()
                status = os.system("git submodule update --init --recursive")
                os.chdir(cwd)

        proj_info = ProjectInfoReader(pkg_dir).read()
        return proj_info


