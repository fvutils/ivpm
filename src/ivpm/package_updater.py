'''
Created on Jun 22, 2021

@author: mballance
'''
import os
import shutil
import subprocess
import sys
import tarfile
import urllib
from zipfile import ZipFile

from ivpm.msg import note, fatal, warning
from ivpm.package import Package, SourceType, SourceType2Ext, PackageType
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from ivpm.project_info_reader import ProjectInfoReader
from typing import Dict
from ivpm.utils import get_venv_python


class PackageUpdater(object):
    
    def __init__(self, packages_dir, anonymous_git):
        self.debug = False
        self.packages_dir = packages_dir
        self.all_pkgs = PackagesInfo("root")
        self.new_deps = []
        self.anonymous_git = anonymous_git
        pass
    
    def update(self, pkgs : PackagesInfo) -> PackagesInfo:
        """
        Updates the specified packages, handling dependencies
        The 'pkgs' parameter holds the dependency information
        from the root project
        """

        
        count = 1

        pkg_q = []
        
        if len(pkgs.keys()) == 0:
            print("No packages")
        
        for key in pkgs.keys():
            print("Package: %s" % key)
            pkg_q.append(pkgs[key])
            
        while True:        
            pkg_deps = {}
            
            # Process this batch of packages
            while len(pkg_q) > 0:
                pkg : Package = pkg_q.pop(0)
                
                self.all_pkgs[pkg.name] = pkg
                
                if pkg.src_type != SourceType.PyPi:
                    proj_info : ProjInfo = self._update_pkg(pkg)
                    
                    # proj_info contains info on any setup-deps that
                    # might be required
                    for sd in proj_info.setup_deps:
                        print("Add setup-dep %s to package %s" % (sd, pkg.name))
                        if pkg.name not in self.all_pkgs.setup_deps.keys():
                            self.all_pkgs.setup_deps[pkg.name] = set()
                        self.all_pkgs.setup_deps[pkg.name].add(sd)

                    if proj_info.process_deps:
                        if not proj_info.has_dep_set(pkg.dep_set):
                            warning("package %s does not contain specified dep-set %s ; skipping" % (proj_info.name, pkg.dep_set))
                            continue
                        else:
                            note("Loading package %s dependencies from dep-set %s" % (proj_info.name, pkg.dep_set))

                        note("Processing dep-set %s of project %s" % (
                            pkg.dep_set,
                            pkg.name))                        

                        ds : PackagesInfo = proj_info.get_dep_set(pkg.dep_set)
                        for d in ds.packages.keys():
                            dep = ds.packages[d]
                    
                            if dep.name not in pkg_deps.keys():
                                pkg_deps[dep.name] = dep
                            else:
                                # TODO: warn about possible version conflict?
                                pass
            
            # Collect new dependencies and add to queue
            for key in pkg_deps.keys():
                if not key in self.all_pkgs.keys():
                    # New package
                    pkg_q.append(pkg_deps[key])
            note("%d new dependencies from iteration %d" % (len(pkg_q), count))
                    
            if len(pkg_q) == 0:
                # We're done
                break
            
            count += 1
            
        return self.all_pkgs
    
    def _update_pkg(self, pkg : Package) -> ProjInfo:
        """Loads a single package. Returns any dependencies"""
        must_update=False
  
        print("********************************************************************")
        print("* Processing package %s" % pkg.name)
        print("********************************************************************")

        pkg_dir = os.path.join(self.packages_dir, pkg.name)
        pkg.path = pkg_dir.replace("\\", "/")
        
        if os.path.isdir(pkg_dir):
            note("package %s is already loaded" % pkg.name)
        else:
            note("loading package %s" % pkg.name)

            # Package isn't currently present in dependencies
            scheme_idx = pkg.url.find("://")
            scheme = pkg.url[0:scheme_idx+3]
            
            if pkg.src_type == SourceType.Git:
                self._clone_git(pkg)
            else:
                remove_pkg_src = False
                pkg_path = None
                print("Must add package " + pkg.name + " scheme=" + scheme)
                
                if scheme == "file://":
                    pkg_path = pkg.url[scheme_idx+3:-1]
                elif scheme in ("http://", "https://", "ssh://"):
                    # Need to fetch, then unpack these
                    download_dir = os.path.join(self.packages_dir, ".download")
                
                    if not os.path.isdir(download_dir):
                        os.makedirs(download_dir)

                    if pkg.src_type not in SourceType2Ext.keys():
                        fatal("Unsupported source-type %s for package %s" % (str(pkg.src_type), pkg.name))                    
                    filename = pkg.name + SourceType2Ext[pkg.src_type]
                    
                    pkg_path = os.path.join(download_dir, filename)
                    
                    # TODO: should this be an option?   
                    remove_pkg_src = True

                    self._fetch_file(pkg.url, pkg_path)
                    
                pkg.path = os.path.join(self.packages_dir, pkg.name)
                pkg.path = pkg.path.replace("\\", "/")

                if self.debug:
                    print("package %s: type=%s" % (pkg.path, str(pkg.src_type)))
                if pkg.src_type in (SourceType.Jar,SourceType.Zip):
                    self._install_zip(pkg, pkg_path)
                elif pkg.src_type == SourceType.Tgz or pkg.src_type == SourceType.Txz:
                    self._install_tgz(pkg, pkg_path)
                    

                if remove_pkg_src:
                    os.unlink(os.path.join(download_dir, filename))
                    
        # Now, check the package for dependencies
        info : ProjInfo = ProjectInfoReader(pkg_dir).read()

        

        # After loading the package, or finding it already loaded,
        # check what we have
        if pkg.pkg_type == PackageType.Unknown:
            for py in ("setup.py", "pyproject.toml"):
                if os.path.isfile(os.path.join(self.packages_dir, pkg.name, py)):
                    pkg.pkg_type = PackageType.Python
                    break
        
        if info is None:
            info = ProjInfo(False)
            info.name = pkg.name

        # Ensure that we use the requested dep-set
        info.target_dep_set = pkg.dep_set
            
        info.process_deps = pkg.process_deps
        
        return info
    
    def _fetch_file(self, url, dest):
        if self.debug:
            print("fetch_file")
        sys.stdout.flush()
        urllib.request.urlretrieve(url, dest)
        
                
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
    
    def _clone_git(self, pkg):
        cwd = os.getcwd()
        os.chdir(self.packages_dir)
        sys.stdout.flush()

        git_cmd = ["git", "clone"]
        
        if pkg.depth is not None:
            git_cmd.extend(["--depth", str(pkg.depth)])

        if pkg.branch is not None:
            git_cmd.extend(["-b", str(pkg.branch)])

        # Modify the URL to use SSH/key-based clones
        # unless anonymous cloning was requested
        if not self.anonymous_git:
            print("NOTE: using dev URL")
            delim_idx = pkg.url.find("://")
            url = pkg.url[delim_idx+3:]
            first_sl_idx = url.find('/')
            url = "git@" + url[:first_sl_idx] + ":" + url[first_sl_idx+1:]
            print("Final URL: %s" % url)
            git_cmd.append(url)
        else:
            print("NOTE: using anonymous URL")
            git_cmd.append(pkg.url)

        # Clone to a directory with same name as package        
        git_cmd.append(pkg.name)
            
#         if scheme == "ssh://":
#             # This is an SSH checkout from Github
#             checkout_url = package_src[6:]            
#             git_cmd += "git@" + checkout_url
#         else:
#             git_cmd += package_src

        print("git_cmd: \"" + str(git_cmd) + "\"")
        status = subprocess.run(git_cmd)
        os.chdir(cwd)
        
        if status.returncode != 0:
            fatal("Git command \"%s\" failed" % str(git_cmd))

        # Checkout a specific commit            
        if pkg.commit is not None:
            os.chdir(os.path.join(self.packages_dir, pkg.name))
            git_cmd = "git reset --hard %s" % pkg.commit
            status = os.system(git_cmd)
            
            if status != 0:
                fatal("Git command \"%s\" failed" % str(git_cmd))
            os.chdir(cwd)
            
        
        # TODO: Existence of .gitmodules should trigger this
        os.chdir(os.path.join(self.packages_dir, pkg.name))
        sys.stdout.flush()
        status = os.system("git submodule update --init --recursive")
        os.chdir(cwd)        

