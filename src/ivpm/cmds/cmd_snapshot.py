'''
Created on Jun 28, 2021

@author: mballance
'''
import os
import shutil
import subprocess
from typing import List

from ivpm.ivpm_yaml_writer import IvpmYamlWriter
from ivpm.msg import note, fatal, warning
from ivpm.out_wrapper import OutWrapper
from ivpm.package import Package, SourceType, PackageType
from ivpm.package_updater import PackageUpdater
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from ivpm.utils import setup_venv, get_venv_python


class CmdSnapshot(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        args.snapshot_dir = os.path.abspath(args.snapshot_dir)
            
        proj_info = ProjInfo.mkFromProj(args.project_dir).read()
        
        if not os.path.isdir(args.snapshot_dir):
            os.makedirs(args.snapshot_dir)
            
        if not args.rls:
            if proj_info.dev_deps is None:
                fatal("development mode selected, but no dev-deps specified")
        else:
            if proj_info.deps is None:
                fatal("release mode selected, but no deps specified")

        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")
        pkgs_info = PackageUpdater(args.snapshot_dir, True).update(
            proj_info.dev_deps if not args.rls else proj_info.deps
            )

        python_pkgs = []
        for key in pkgs_info.keys():
            pkg : Package = pkgs_info[key]
            
            print("pkg: %s type=%s src=%s" % (pkg.name, str(pkg.pkg_type), str(pkg.src_type)))
            
            if pkg.pkg_type == PackageType.Python:
                if pkg.path is None:
                    python_pkgs.append(pkg)
                elif os.path.isfile(os.path.join(pkg.path, "setup.py")):
                    python_pkgs.append(pkg)
                else:
                    warning("Package %s (%s) is marked as Python, but is missing setup.py" % (
                        pkg.name, pkg.path))
                    
        if len(python_pkgs) > 0:
            note("Recording Python packages to install")
            self._write_requirements_txt(
                args.snapshot_dir, 
                python_pkgs, 
                os.path.join(args.snapshot_dir, "python_pkgs.txt"))

        note("collecting git-version info")            
        self._collect_git_versions(pkgs_info)
            
        note("Writing ivpm.yaml")
        info = ProjInfo(True)
        info.deps = pkgs_info
        info.dev_deps = pkgs_info
        with open(os.path.join(args.snapshot_dir, "ivpm.yaml"), "w") as fp:
            IvpmYamlWriter(OutWrapper(fp)).write(info)
            
        note("Removing package .git directories")
#        self._remove_git_dirs(args.snapshot_dir)

        ivpm_dir = os.path.dirname(os.path.realpath(__file__))
        templates_dir = os.path.join(ivpm_dir, "templates")
        shutil.copy(
            os.path.join(templates_dir, "init.py"),
            os.path.join(args.snapshot_dir, "init.py"))
        
    def _collect_git_versions(self, pkgs_info : PackagesInfo):
        for key in pkgs_info.keys():
            pkg : Package = pkgs_info[key]
            
            if pkg.src_type == SourceType.Git:
                cwd = os.getcwd()
                os.chdir(pkg.path)
                status = subprocess.check_output(["git", "show", "--oneline", "-s"])
                
                status = status.decode()

                if status.find('(') != -1:
                    hash = status[0:status.find("(")].strip()
                elif status.find(' ') != -1:
                    hash = status[0:status.find(" ")].strip()
                else:
                    fatal("failed to decode git-show output %s" % status)
                
                pkg.version = hash
                
                os.chdir(cwd)

    def _remove_git_dirs(self, path):
        if os.path.isdir(os.path.join(path, ".git")):
            note("  ... %s" % os.path.join(path, ".git"))
            shutil.rmtree(os.path.join(path, ".git"))
            
        for d in os.listdir(path):
            if os.path.isdir(os.path.join(path, d)):
                self._remove_git_dirs(os.path.join(path, d))
                
    def _write_requirements_txt(self, 
                                packages_dir,
                                python_pkgs : List[Package],
                                file):
        with open(file, "w") as fp:
            for pkg in python_pkgs:
                
                if pkg.url is not None:
                    # Editable package
                    fp.write("-e ./%s\n" % pkg.name)
                else:
                    # PyPi package
                    fp.write("%s\n" % pkg.name)                

    