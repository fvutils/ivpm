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
from .package_url import PackageURL
from ..proj_info import ProjInfo
from ..project_ops_info import ProjectUpdateInfo, ProjectStatusInfo, ProjectSyncInfo
from ..utils import note, fatal

@dc.dataclass
class PackageGit(PackageURL):
    branch : str = None
    commit : str = None
    tag : str = None
    depth : str = None
    anonymous : bool = None

    def update(self, update_info : ProjectUpdateInfo) -> ProjInfo:
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

        return ProjInfo.mkFromProj(pkg_dir)
    
    def status(self, status_info : ProjectStatusInfo):
        pass
    
    def sync(self, sync_info : ProjectSyncInfo):
        if not os.path.isdir(os.path.join(sync_info.deps_dir, dir, ".git")):
            fatal("Package \"" + dir + "\" is not a Git repository")
        print("Package: " + dir)
        try:
            branch = subprocess.check_output(
                ["git", "branch"],
                cwd=os.path.join(sync_info.deps_dir, dir))
        except Exception as e:
            fatal("failed to get branch of package \"" + dir + "\"")

        branch = branch.strip()
        if len(branch) == 0:
            fatal("branch is empty")

        branch_lines = branch.decode().splitlines()
        branch = None
        for bl in branch_lines:
            if bl[0] == "*":
                branch = bl[1:].strip()
                break
        if branch is None:
            fatal("Failed to identify branch")

        status = subprocess.run(
            ["git", "fetch"],
            cwd=os.path.join(sync_info.deps_dir, dir))
        if status.returncode != 0:
            fatal("Failed to run git fetch on package %s" % dir)
        status = subprocess.run(
            ["git", "merge", "origin/" + branch],
            cwd=os.path.join(sync_info.deps_dir, dir))
        if status.returncode != 0:
            fatal("Failed to run git merge origin/%s on package %s" % (branch, dir))
    
    def process_options(self, opts, si):
        super().process_options(opts, si)

        if "anonymous" in opts.keys():
            self.anonymous = opts["anonymous"]
                
        if "depth" in opts.keys():
            self.depth = opts["depth"]
                
        if "dep-set" in opts.keys():
            self.dep_set = opts["dep-set"]
               
        if "branch" in opts.keys():
            self.branch = opts["branch"]
                
        if "commit" in opts.keys():
            self.commit = opts["commit"]
               
        if "tag" in opts.keys():
            self.tag = opts["tag"]


    @staticmethod
    def create(name, opts, si) -> 'PackageGit':
        pkg = PackageGit(name)
        pkg.process_options(opts, si)
        return pkg

