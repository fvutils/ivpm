'''
Created on Jun 8, 2021

@author: mballance
'''
import os
from subprocess import check_output

from ivpm.packages_info import PackagesInfo
from ivpm.project_info_reader import ProjectInfoReader
from ivpm.utils import get_venv_python, setup_venv
from ivpm.msg import note, fatal
from ivpm.sve_filelist_writer import SveFilelistWriter
from ivpm.out_wrapper import OutWrapper
from ivpm.package_updater import PackageUpdater
from _struct import pack


class CmdUpdate(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        proj_info = ProjectInfoReader(args.project_dir).read()
            
#         # Map between project name and ProjInfo
#         package_deps = {}

        packages_dir = os.path.join(args.project_dir, "packages")
# 
#         if os.path.isdir(packages_dir) == False:
#             os.makedirs(packages_dir);
#         elif os.path.isfile(packages_dir + "/packages.mf"):
#             packages_mf = read_packages(packages_dir + "/packages.mf")

 
        # Ensure that we have a python virtual environment setup
        if not os.path.isdir(os.path.join(packages_dir, "python")):
            ivpm_python = setup_venv(os.path.join(packages_dir, "python"))
        else:
            note("python virtual environment already exists")
            ivpm_python = get_venv_python(os.path.join(packages_dir, "python"))
            
        if not args.rls:
            if proj_info.dev_deps is None:
                fatal("development mode selected, but no dev-deps specified")
        else:
            if proj_info.deps is None:
                fatal("release mode selected, but no deps specified")

        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")
        note("beginning update using %s packages" % (
            "development" if not args.rls else "release"))
        pkgs_info = PackageUpdater(packages_dir, not args.rls).update(
            proj_info.dev_deps if not args.rls else proj_info.deps
            )
            

#         if args.requirements is None:
#             # Check to see if a requirements.txt exists already
#             for reqs in ["requirements_dev.txt", "requirements.txt"]:
#                 if os.path.isfile(os.path.join(args.project_dir, reqs)):
#                     print("Note: Using default requirements \"" + reqs + "\"")
#                     args.requirements = os.path.join(args.project_dir, reqs);
#                     break
    
#         if os.path.isfile(os.path.join(etc_dir, "packages.mf")):
#             # Load the root project dependencies
#             dependencies = read_packages(etc_dir + "/packages.mf")
#         else:
#             dependencies = None

#         if args.requirements is None and dependencies is None:
#             raise Exception("Neither requirements nor packages.mf provided")
# 
#         if args.requirements is not None:    
#             # Ensure the Git wrapper is in place. This ensures we don't
#             # stomp on existing check-outs when updating dependencies
#             scripts_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "scripts")
#             print("scripts_dir: " + scripts_dir)
#         
#             path = os.environ["PATH"]
#             os.environ["PATH"] = scripts_dir + ":" + path
#             os.system("" + ivpm_python + " -m pip install -r " + args.requirements + " --src " + packages_dir)
#             os.environ["PATH"] = path
# 
#         if dependencies is not None:
#             if info is None:
#                 raise Exception("Found packages.mf, but no etc/ivpm.info exists")
# 
#             # The dependencies list should include this project
#             dependencies[info['name']] = "root";
#     
#             # Add an entry for the root project
#             pinfo = ProjInfo(False)
#             for d in dependencies.keys():
#                 pinfo.add_dependency(d)
#                 package_deps[info["name"]] = pinfo
# 
#             # Sub-requirements might be added, so copy the
#             # package set before iterating over it
#             pkgs = set()
#             for pkg in dependencies.keys():
#                 pkgs.add(pkg)
#             
#             for pkg in pkgs:
#                 if dependencies[pkg] == "root":
#                     continue
# 
#                 update_package(
#                     pkg, 
#                     packages_mf,
#                     dependencies, 
#                     packages_dir,
#                     package_deps)
# 
#             write_packages(packages_dir + "/packages.mf", dependencies)
#            write_packages_mk(packages_dir + "/packages.mk", info["name"], package_deps)
        with open(os.path.join(packages_dir, "sve.F"), "w") as fp:
            SveFilelistWriter(OutWrapper(fp)).write(pkgs_info)
#            write_packages_env(packages_dir + "/packages_env.sh", False, info["name"], package_deps)
#            write_packages_env(packages_dir + "/packages_env.csh", True, info["name"], package_deps)
        

    