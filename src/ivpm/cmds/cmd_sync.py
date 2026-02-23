'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import subprocess
import sys
import stat

from ivpm.arg_utils import ensure_have_project_dir
from ivpm.msg import fatal, note
from ..project_ops import ProjectOps
from ..proj_info import ProjInfo
from ..package_lock import write_lock, read_lock



class CmdSync(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            args.project_dir = os.getcwd()
    
        proj_info = ProjInfo.mkFromProj(args.project_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        packages_dir = os.path.join(args.project_dir, proj_info.deps_dir)
    
        # After that check, go ahead and just check directories
        for dir in os.listdir(packages_dir):
            pkg_path = os.path.join(packages_dir, dir)
            git_dir = os.path.join(pkg_path, ".git")
            
            if os.path.isdir(git_dir):
                # Check if the package is editable by testing if it's writable
                # Cached (non-editable) packages are made read-only
                try:
                    mode = os.stat(pkg_path).st_mode
                    is_writable = bool(mode & stat.S_IWUSR)
                except Exception:
                    is_writable = False
                
                if not is_writable:
                    print("Note: skipping cached (read-only) package \"%s\"" % dir)
                    continue
                
                print("Package: " + dir)
                cwd = os.getcwd()
                os.chdir(pkg_path)
                try:
                    branch = subprocess.check_output(["git", "branch"])
                except Exception as e:
                    print("Note: Failed to get branch of package \"" + dir + "\"")
                    continue

                branch = branch.strip()
                if len(branch) == 0:
                    raise Exception("Error: branch is empty")

                branch_lines = branch.decode().splitlines()
                branch = None
                for bl in branch_lines:
                    if bl[0] == "*":
                        branch = bl[1:].strip()
                        break
                if branch is None:
                    raise Exception("Failed to identify branch")

                status = subprocess.run(["git", "fetch"])
                if status.returncode != 0:
                    fatal("Failed to run git fetch on package %s" % dir)
                status = subprocess.run(["git", "merge", "origin/" + branch])
                if status.returncode != 0:
                    fatal("Failed to run git merge origin/%s on package %s" % (branch, dir))
                os.chdir(cwd)
            elif os.path.isdir(pkg_path):
                print("Note: skipping non-Git package \"" + dir + "\"")
                sys.stdout.flush()

        # Update package-lock.json to reflect the new state of all packages.
        # We re-read the current commit hashes by patching the existing lock
        # entries — this preserves all non-git package entries unchanged.
        self._update_lock_after_sync(packages_dir)

    def _update_lock_after_sync(self, packages_dir: str):
        """Regenerate package-lock.json after sync by reading current HEAD commits."""
        lock_path = os.path.join(packages_dir, "package-lock.json")
        if not os.path.isfile(lock_path):
            return  # No lock file to update

        try:
            lock = read_lock(lock_path)
        except Exception as e:
            note("Warning: could not read package-lock.json for update: %s" % e)
            return

        packages = lock.get("packages", {})

        for name, entry in packages.items():
            if entry.get("src") != "git":
                continue
            pkg_path = os.path.join(packages_dir, name)
            if not os.path.isdir(os.path.join(pkg_path, ".git")):
                continue
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, cwd=pkg_path, timeout=10
                )
                if result.returncode == 0:
                    entry["commit_resolved"] = result.stdout.strip()
            except Exception:
                pass

        # Re-use write_lock's atomic write by building a minimal fake pkgs dict.
        # Simpler: just write the patched lock dict directly (atomically).
        import json, hashlib
        from datetime import datetime, timezone

        lock["generated"] = datetime.now(timezone.utc).isoformat()
        lock.pop("sha256", None)
        body = json.dumps(lock, indent=2, sort_keys=True)
        checksum = hashlib.sha256(body.encode()).hexdigest()
        lock["sha256"] = checksum

        tmp_path = lock_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(lock, indent=2, sort_keys=True, fp=f)
            f.write("\n")
        os.replace(tmp_path, lock_path)
        note("Updated package-lock.json after sync")

