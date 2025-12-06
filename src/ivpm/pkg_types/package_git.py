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
import logging
import os
import sys
import subprocess
import dataclasses as dc
from .package_url import PackageURL
from ..proj_info import ProjInfo
from ..project_ops_info import ProjectUpdateInfo, ProjectStatusInfo, ProjectSyncInfo
from ..utils import note, fatal
from ..cache import Cache, is_github_url, parse_github_url

_logger = logging.getLogger("ivpm.pkg_types.package_git")


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

        # Report this package for cache statistics
        # Git packages are cacheable if cache=True, editable if cache is not True
        is_cacheable = self.cache is True
        is_editable = self.cache is not True  # Could be cached but isn't
        update_info.report_package(cacheable=is_cacheable, editable=is_editable)

        if os.path.exists(pkg_dir) or os.path.islink(pkg_dir):
            note("package %s is already loaded" % self.name)
        else:
            # Check if caching is enabled and supported
            if self.cache is True:
                # For GitHub URLs, use GitHub API; for others, use git ls-remote
                return self._update_with_cache(update_info, pkg_dir)
            elif self.cache is False:
                # Explicitly no cache - clone without history and make read-only
                return self._update_no_cache_readonly(update_info, pkg_dir)
            else:
                # cache not specified - clone with full history
                return self._update_full_clone(update_info, pkg_dir)

        return ProjInfo.mkFromProj(pkg_dir)

    def _get_github_commit_hash(self, owner: str, repo: str, ref: str = None) -> str:
        """Get the commit hash for a GitHub repo using the API or git ls-remote.
        
        For general git URLs, uses git ls-remote to get the hash.
        """
        import httpx
        
        if ref is None:
            ref = self.branch or self.tag or "HEAD"
        
        # Try GitHub API first if it's a GitHub URL
        if is_github_url(self.url):
            try:
                # Use GitHub API to get the commit hash
                api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
                response = httpx.get(api_url, follow_redirects=True, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    return data["sha"]
            except Exception:
                pass
        
        # Fallback to git ls-remote for any git URL
        return self._get_commit_hash_ls_remote(ref)
    
    def _get_commit_hash_ls_remote(self, ref: str = None) -> str:
        """Get commit hash using git ls-remote."""
        if ref is None:
            ref = self.branch or self.tag or "HEAD"
        
        try:
            # Use git ls-remote to get the hash
            result = subprocess.run(
                ["git", "ls-remote", self.url, ref],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "hash\tref"
                return result.stdout.strip().split()[0]
            
            # Try refs/heads/ prefix for branches
            if not ref.startswith("refs/"):
                result = subprocess.run(
                    ["git", "ls-remote", self.url, f"refs/heads/{ref}"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split()[0]
                
                # Try refs/tags/ prefix for tags
                result = subprocess.run(
                    ["git", "ls-remote", self.url, f"refs/tags/{ref}"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split()[0]
        except Exception:
            pass
        
        return None

    def _update_with_cache(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Update using the cache."""
        note("loading package %s with cache" % self.name)
        
        ref = self.branch or self.tag or "HEAD"
        
        # Get the commit hash - use GitHub API for GitHub URLs, git ls-remote otherwise
        commit_hash = None
        if is_github_url(self.url):
            owner, repo = parse_github_url(self.url)
            commit_hash = self._get_github_commit_hash(owner, repo, ref)
        else:
            # Use git ls-remote for general git URLs
            commit_hash = self._get_commit_hash_ls_remote(ref)
        
        if commit_hash is None:
            fatal("Failed to get commit hash for %s (ref: %s)" % (self.url, ref))
        
        cache = update_info.cache
        if cache is None:
            cache = Cache()
        
        # If cache is not properly configured, fall back to full clone
        if not cache.is_enabled():
            note("IVPM_CACHE not set - falling back to full clone for %s" % self.name)
            return self._update_full_clone(update_info, pkg_dir)
        
        # Check if this version is cached
        if cache.has_version(self.name, commit_hash):
            # Cache hit - symlink to deps
            note("Cache hit for %s at %s" % (self.name, commit_hash[:12]))
            cache.link_to_deps(self.name, commit_hash, update_info.deps_dir)
            update_info.report_cache_hit()
            return ProjInfo.mkFromProj(pkg_dir)
        
        # Cache miss - clone without history
        note("Cache miss for %s - cloning" % self.name)
        update_info.report_cache_miss()
        
        # Clone to a temporary location first
        temp_dir = os.path.join(update_info.deps_dir, f".cache_temp_{self.name}")
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)
        
        self._clone_to_dir(update_info, temp_dir, depth=1)
        
        # Store in cache and link
        cache.store_version(self.name, commit_hash, temp_dir)
        cache.link_to_deps(self.name, commit_hash, update_info.deps_dir)
        
        return ProjInfo.mkFromProj(pkg_dir)

    def _update_no_cache_readonly(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Clone without history and make read-only (cache=False)."""
        note("loading package %s (no cache, read-only)" % self.name)
        
        self._clone_to_dir(update_info, pkg_dir, depth=1)
        
        # Make read-only
        self._make_readonly(pkg_dir)
        
        return ProjInfo.mkFromProj(pkg_dir)

    def _update_full_clone(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Full clone with history (cache unspecified)."""
        note("loading package %s" % self.name)
        
        self._clone_to_dir(update_info, pkg_dir, depth=self.depth)
        
        return ProjInfo.mkFromProj(pkg_dir)

    def _clone_to_dir(self, update_info: ProjectUpdateInfo, target_dir: str, depth=None):
        """Clone the repo to the specified directory."""
        cwd = os.getcwd()
        parent_dir = os.path.dirname(target_dir)
        target_name = os.path.basename(target_dir)
        
        if not os.path.isdir(parent_dir):
            os.makedirs(parent_dir)
        
        os.chdir(parent_dir)
        sys.stdout.flush()

        git_cmd = ["git", "clone"]
    
        if depth is not None:
            git_cmd.extend(["--depth", str(depth)])

        if self.branch is not None:
            git_cmd.extend(["-b", str(self.branch)])

        # Modify the URL to use SSH/key-based clones
        # unless anonymous cloning was requested
        if update_info.args is not None and hasattr(update_info.args, "anonymous"):
            use_anonymous = getattr(update_info.args, "anonymous")
        else:
            use_anonymous = False

        if self.anonymous is not None:
            use_anonymous = self.anonymous

        if not use_anonymous:
            _logger.debug("Using dev URL")
            delim_idx = self.url.find("://")
            protocol = self.url[:delim_idx]
            if protocol != "file":
                url = self.url[delim_idx+3:]
                first_sl_idx = url.find('/')
                url = "git@" + url[:first_sl_idx] + ":" + url[first_sl_idx+1:]
                _logger.debug("Final URL: %s", url)
                git_cmd.append(url)
            else:
                _logger.debug("Using original file-based URL")
                git_cmd.append(self.url)
        else:
            _logger.debug("Using anonymous URL")
            git_cmd.append(self.url)

        # Clone to the target directory name
        git_cmd.append(target_name)
        
        _logger.debug("git_cmd: %s", str(git_cmd))
        
        # Suppress output when in Rich TUI mode
        if update_info.suppress_output:
            status = subprocess.run(git_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            status = subprocess.run(git_cmd)
        os.chdir(cwd)
    
        if status.returncode != 0:
            fatal("Git command \"%s\" failed" % str(git_cmd))

        # Checkout a specific commit            
        if self.commit is not None:
            os.chdir(target_dir)
            git_cmd = ["git", "reset", "--hard", self.commit]
            _logger.debug("git_cmd: %s", str(git_cmd))
            if update_info.suppress_output:
                status = subprocess.run(git_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                status = subprocess.run(git_cmd)
        
            if status.returncode != 0:
                fatal("Git command \"%s\" failed" % str(git_cmd))
            os.chdir(cwd)
        
    
        # TODO: Existence of .gitmodules should trigger this
        if os.path.isfile(os.path.join(target_dir, ".gitmodules")):
            os.chdir(target_dir)
            sys.stdout.flush()
            git_cmd = ["git", "submodule", "update", "--init", "--recursive"]
            _logger.debug("git_cmd: %s", str(git_cmd))
            if update_info.suppress_output:
                status = subprocess.run(git_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                status = subprocess.run(git_cmd)
            os.chdir(cwd)

    def _make_readonly(self, path: str):
        """Make all files in a directory tree read-only."""
        import stat
        for root, dirs, files in os.walk(path):
            for d in dirs:
                dir_path = os.path.join(root, d)
                mode = os.stat(dir_path).st_mode
                os.chmod(dir_path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            for f in files:
                file_path = os.path.join(root, f)
                mode = os.stat(file_path).st_mode
                os.chmod(file_path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    
    def status(self, status_info : ProjectStatusInfo):
        pass
    
    def sync(self, sync_info : ProjectSyncInfo):
        if not os.path.isdir(os.path.join(sync_info.deps_dir, dir, ".git")):
            fatal("Package \"" + dir + "\" is not a Git repository")
        _logger.info("Package: %s", dir)
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

