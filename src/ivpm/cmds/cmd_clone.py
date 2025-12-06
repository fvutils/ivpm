#****************************************************************************
#* cmd_clone.py
#*
#* Copyright 2025 Matthew Ballance and Contributors
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
#****************************************************************************
import os
import subprocess

from ..utils import fatal
from ..project_ops import ProjectOps


class CmdClone(object):

    def __call__(self, args):
        # Determine workspace directory
        src = args.src
        wsdir = args.workspace_dir

        if wsdir is None:
            # Derive from basename of src (strip trailing .git if present)
            base = os.path.basename(src)
            if base.endswith('.git'):
                base = base[:-4]
            wsdir = base
        
        if os.path.isabs(wsdir):
            target_dir = wsdir
        else:
            target_dir = os.path.abspath(wsdir)
        
        if os.path.exists(target_dir):
            # Allow existing empty directory
            if os.listdir(target_dir):
                fatal("Workspace directory '%s' already exists and is not empty" % target_dir)
        
        # Decide if we are handling a Git source. For now, support Git only.
        self._clone_git(src, target_dir, args)

        # After cloning, run ivpm update in the new workspace
        # so dependencies are fetched according to options provided
        dep_set = getattr(args, 'dep_set', None)
        ivpm_yaml_path = os.path.join(target_dir, "ivpm.yaml")
        
        if os.path.isfile(ivpm_yaml_path):
            ProjectOps(target_dir).update(
                dep_set=dep_set,
                args=args
            )
        else:
            if dep_set is not None:
                fatal("Dependency set '%s' specified but no ivpm.yaml exists in cloned project" % dep_set)
            # No ivpm.yaml and no dep_set specified - just skip update

    def _clone_git(self, src, target_dir, args):
        # Construct clone URL. Convert to SSH form unless anonymous requested
        url = src
        use_anonymous = getattr(args, 'anonymous', False)

        if not use_anonymous:
            # Convert https/http URLs to git@host:path form; keep file: and local paths
            if '://' in src:
                proto = src.split('://', 1)[0]
                if proto != 'file':
                    rest = src.split('://', 1)[1]
                    # Transform host/path to git@host:path
                    first_sl = rest.find('/')
                    if first_sl != -1:
                        host = rest[:first_sl]
                        path = rest[first_sl+1:]
                        url = f"git@{host}:{path}"
            elif src.startswith('git@'):
                url = src
            else:
                # local path - leave as-is
                url = src
        
        git_cmd = ["git", "clone", url, target_dir]
        status = subprocess.run(git_cmd)
        if status.returncode != 0:
            fatal("Git command \"%s\" failed" % str(git_cmd))

        # Handle branch selection/creation
        branch = getattr(args, 'branch', None)
        if branch is not None:
            # Determine if remote branch exists
            # First, fetch to ensure remotes are up to date
            subprocess.run(["git", "fetch", "--all"], cwd=target_dir)
            # Check if origin/branch exists
            have_remote = False
            try:
                out = subprocess.check_output(["git", "ls-remote", "--heads", "origin", branch], cwd=target_dir)
                have_remote = (len(out.decode().strip()) > 0)
            except Exception:
                have_remote = False
            if have_remote:
                # Checkout branch tracking origin/branch
                status = subprocess.run(["git", "checkout", "-B", branch, f"origin/{branch}"], cwd=target_dir)
            else:
                # Create a new local branch from current HEAD
                status = subprocess.run(["git", "checkout", "-b", branch], cwd=target_dir)
            if status.returncode != 0:
                fatal("Failed to checkout branch %s" % branch)
