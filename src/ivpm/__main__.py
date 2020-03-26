'''
Created on Jan 19, 2020

@author: ballance
'''

import argparse
import os
import subprocess
from subprocess import check_output
import sys

from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from mimetypes import init
from string import Template
import stat


#********************************************************************
#* read_packages
#*
#* Read the content of a packages.info file and return a dictionary
#* with an entry per package with a non-null 
#********************************************************************
def read_packages(packages_mf):
    packages = PackagesInfo()
    
    fh = open(packages_mf, "r")

    for l in fh.readlines():
        l = l.strip()
        
        comment_idx = l.find("#")
        if comment_idx != -1:
            l = l[0:comment_idx]
        
        if l == "":
            continue
        
        at_idx = l.find("@")
        options_m = {}
        if at_idx != -1:
            package=l[0:at_idx].strip()
            src=l[at_idx+1:len(l)].strip()
            options = ""
            # Determine if there are options
            if src.find(" ") != -1:
                options = src[src.find(" "):len(src)].strip()
                src = src[:src.find(" ")].strip()
            elif src.find("\t") != -1:
                options = src[src.find("\t"):len(src)].strip()
                src = src[:src.find("\t")].strip()

            if options != "":
                for opt in options.split():
                    if opt.find("=") != -1:
                        opt_k = opt[:opt.find("=")].strip()
                        opt_v = opt[opt.find("=")+1:len(opt)].strip()
                        options_m[opt_k] = opt_v
                    else:
                        print("Error: malformed option \"" + opt + "\"")
        else:
            package=l
            src=None
     
        if package in packages.keys():
            print("Error: multiple package listings")
            
        packages[package] = src
        packages.set_options(package, options_m)
    
    fh.close()
    
    return packages

def find_project_dir():
    """Attempt to find the real project directory"""
    cwd = os.getcwd()
        
    if os.path.isdir(os.path.join(cwd, "packages")):
        return cwd
    else:
        # Go up the path
        parent = os.path.dirname(cwd)
        while parent != "" and parent != "/":
            if os.path.isdir(os.path.join(parent, "packages")):
                return parent
            parent = os.path.dirname(parent)
            
    return None

def ensure_have_project_dir(args):
    if args.project_dir is None:
        print("Note: Attempting to discover project_dir")
        sys.stdout.flush()
        args.project_dir = find_project_dir()
        
        if args.project_dir is None:
            raise Exception("Failed to find project_dir ; specify with --project-dir")
        else:
            print("Note: project_dir is " + args.project_dir) 
            sys.stdout.flush()

#********************************************************************
# write_packages
#********************************************************************
def write_packages(packages_mf, packages):
    
    with open(packages_mf, "w") as fh:
        for package in packages.keys():
            fh.write(package + "@" + packages[package] + "\n")
  
#********************************************************************
# write_packages_mk
#********************************************************************
def write_packages_mk(
        packages_mk, 
        project,
        package_deps):
    packages_dir = os.path.dirname(packages_mk)
    
    print("write_packages_mk: " + packages_dir)
  
    fh = open(packages_mk, "w")
    fh.write("#********************************************************************\n");
    fh.write("# packages.mk for " + project + "\n");
    fh.write("#********************************************************************\n");
    fh.write("\n");
    fh.write("ifneq (1,$(RULES))\n");
    fh.write("  ifeq (,$(IVPM_PYTHON))\n")
    if os.path.isdir(os.path.join(packages_dir, "python", "Scripts")):
        fh.write("    IVPM_PYTHON := $(PACKAGES_DIR)/python/Scripts/python\n")
    else:
        fh.write("    IVPM_PYTHON := $(PACKAGES_DIR)/python/bin/python\n")
    fh.write("  endif\n")
    fh.write("  PYTHON_BIN ?= $(IVPM_PYTHON)\n")
    fh.write("  IVPM_PYTHON_BINDIR := $(dir $(IVPM_PYTHON))\n")
# Remove this until we can figure out what's going on
    fh.write("  PATH := $(IVPM_PYTHON_BINDIR):$(PATH)\n")
    fh.write("  export PATH\n")
    fh.write("package_deps = " + project + "\n")
  
    for p in package_deps.keys():
        print("package_dep: " + str(p))
        info = package_deps[p]
        fh.write(p + "_deps=")
        for d in info.deps():
            if d != project and os.path.exists(os.path.join(packages_dir, d, "etc/packages.mf")):
                fh.write(d + " ")
        fh.write("\n")
        fh.write(p + "_clean_deps=")
        for d in info.deps():
            if d != project and os.path.exists(os.path.join(packages_dir, d, "etc/packages.mf")):
                fh.write("clean_" + d + " ")
        fh.write("\n")
      
        if os.path.isfile(packages_dir + "/" + p + "/mkfiles/" + p + ".mk"):
            fh.write("include $(PACKAGES_DIR)/" + p + "/mkfiles/" + p + ".mk\n")

    fh.write("else # Rules\n");
    fh.write("ifneq (1,$(PACKAGES_MK_RULES_INCLUDED))\n");
    fh.write("PACKAGES_MK_RULES_INCLUDED := 1\n")
    for p in package_deps.keys():
        info = package_deps[p]
        fh.write(p + " : $(" + p + "_deps)\n");
     
        if info.is_src:
            fh.write("\t$(Q)$(MAKE) PACKAGES_DIR=$(PACKAGES_DIR) PHASE2=true -C $(PACKAGES_DIR)/" + p + "/scripts -f ivpm.mk build\n")
        fh.write("\n");
        fh.write("clean_" + p + " : $(" + p + "_clean_deps)\n");
     
        if info.is_src:
            fh.write("\t$(Q)$(MAKE) PACKAGES_DIR=$(PACKAGES_DIR) PHASE2=true -C $(PACKAGES_DIR)/" + p + "/scripts -f ivpm.mk clean\n")
        fh.write("\n");
      
        if os.path.isfile(packages_dir + "/" + p + "/mkfiles/" + p + ".mk"):
            fh.write("include $(PACKAGES_DIR)/" + p + "/mkfiles/" + p + ".mk\n")

    fh.write("\n")
    fh.write("endif # PACKAGES_MK_RULES_INCLUDED\n")
    fh.write("endif\n");
    fh.write("\n")
  
    fh.close()
  
#********************************************************************
# write_sve_f
#********************************************************************
def write_sve_f(
        sve_f, 
        project,
        package_deps):
    packages_dir = os.path.dirname(sve_f)
  
    with open(sve_f, "w") as fh:
        fh.write("//********************************************************************\n");
        fh.write("//* sve.F for " + project + "\n");
        fh.write("//********************************************************************\n");
        fh.write("\n");

        for p in package_deps.keys():
            if os.path.isfile(packages_dir + "/" + p + "/sve.F"):
                fh.write("-F ./" + p + "/sve.F\n")
            elif os.path.isfile(packages_dir + "/" + p + "/sve.f"):
                fh.write("-F ./" + p + "/sve.f\n")


#********************************************************************
# write_packages_env
#********************************************************************
def write_packages_env(
        env_f,
        is_csh,
        project,
        package_deps):
  
    with open(env_f, "w") as fh:
        fh.write("#********************************************************************\n");
        fh.write("#* environment setup file for " + project + "\n");
        fh.write("#********************************************************************\n");
        fh.write("\n");

        for p in package_deps.keys():
            info = package_deps[p]
            ivpm = info.ivpm_info

            if "rootvar" in ivpm.keys():
                if is_csh:
                    fh.write("setenv " + ivpm["rootvar"] + " $PACKAGES_DIR/" + ivpm["name"] + "\n")
                else:
                    fh.write("export " + ivpm["rootvar"] + "=$PACKAGES_DIR/" + ivpm["name"] + "\n")
      
#********************************************************************
# read_info
#
# Reads an .info file, which has the format key=value
#********************************************************************
def read_info(info_file):
    info = {}
    
    fh = open(info_file, "r")

    for l in fh.readlines():
        l = l.strip()
        
        comment_idx = l.find('#')
        if comment_idx != -1:
            l = l[0:comment_idx]
        
        if l == '':
            continue
        
        eq_idx = l.find('=')
        if eq_idx != -1:
            key=l[0:eq_idx].strip()
            src=l[eq_idx+1:len(l)].strip()
            info[key] = src
        else:
            print("Error: malformed line \"" + l + "\" in " + info_file);
     
    fh.close()
    
    return info

        
#********************************************************************
# update_package()
#
# package      - the name of the package to update
# packages_mf  - the packages/packages.mf file
# packages     - the packages.mf file for this package
# package_deps - a dict of package-name to package_info
#********************************************************************
def update_package(
    package,
        packages_mf,
    dependencies,
    packages_dir,
    package_deps
    ):
    package_src = dependencies[package]
    must_update=False
  
    print("********************************************************************")
    print("Processing package " + package + "")
    print("********************************************************************")
  

    if package in packages_mf.keys():
        print("package \"" + package + "\" in package_mf list")     
        # See if we are up-to-date or require a change
        if os.path.isdir(packages_dir + "/" + package) == False:
            must_update = True
        elif packages_mf[package] != dependencies[package]:
            # TODO: should check if we are switching from binary to source
            print("Removing existing package dir for " + package)
            sys.stdout.flush()
            os.system("rm -rf " + packages_dir + "/" + package)
            print("PackagesMF: " + packages_mf[package] + " != " + dependencies[package])
            must_update = True
    else:
        must_update = True
        print("package \"" + package + "\" NOT in package_mf list")     
    
    if must_update:
        # Package isn't currently present in dependencies
        scheme_idx = package_src.find("://")
        scheme = package_src[0:scheme_idx+3]
        print("Must add package " + package + " scheme=" + scheme)
        if scheme == "file://":
            path = package_src[scheme_idx+3:len(package_src)]
            cwd = os.getcwd()
            os.chdir(packages_dir)
            sys.stdout.flush()
            status = os.system("tar xvzf " + path)
            os.chdir(cwd)
      
            if status != 0:
                print("Error: while unpacking " + package)
            
            print("File: " + path)
        elif scheme == "http://" or scheme == "https://" or scheme == "ssh://":
            ext_idx = package_src.rfind('.')
            if ext_idx == -1:
                print("Error: URL resource doesn't have an extension")
            ext = package_src[ext_idx:len(package_src)]

            if ext == ".git":
                cwd = os.getcwd()
                os.chdir(packages_dir)
                sys.stdout.flush()
                options_m = dependencies.get_options(package)

                git_cmd = "git clone "
                if "depth" in options_m.keys():
                    git_cmd += "--depth " + str(options_m["depth"] + " ")

                if scheme == "ssh://":
                    # This is an SSH checkout from Github
                    checkout_url = package_src[6:]            
                    git_cmd += "git@" + checkout_url
                else:
                    git_cmd += package_src

                print("git_cmd: \"" + git_cmd + "\"")
                status = os.system(git_cmd)
                os.chdir(cwd)
                os.chdir(packages_dir + "/" + package)
                sys.stdout.flush()
                status = os.system("git submodule update --init --recursive")
                os.chdir(cwd)
            elif ext == ".gz":
                # Just assume this is a .tar.gz
                cwd = os.getcwd()
                os.chdir(packages_dir)
                sys.stdout.flush()
                os.system("wget -O " + package + ".tar.gz " + package_src)
                os.system("tar xvzf " + package + ".tar.gz")
                os.system("rm -rf " + package + ".tar.gz")
                os.chdir(cwd)
            else:
                print("Error: unknown URL extension \"" + ext + "\"")
        else:
            print("Error: unknown scheme " + scheme)

    if os.path.exists(os.path.join(packages_dir, package, "etc/packages.mf")):
        print("Note: package \"" + package + "\" is an IVPM package")
        sys.stdout.flush()
        this_package_mf = read_packages(packages_dir + "/" + package + "/etc/packages.mf")
 
        # This is a source package, so keep track so we can properly build it 
        is_src = os.path.isfile(packages_dir + "/" + package + "/scripts/ivpm.mk")
  
        # Add a new entry for this package
        info = ProjInfo(is_src)

        if os.path.isfile(packages_dir + "/" + package + "/etc/ivpm.info"):
            info.ivpm_info = read_info(packages_dir + "/" + package + "/etc/ivpm.info")
            package_deps[package] = info
  
        for p in this_package_mf.keys():
            print("Dependency: " + p)
            info.add_dependency(p)
            if p in dependencies.keys():
                print("  ... has already been handled")
            else:
                print("  ... loading now")
                # Add the new package to the full dependency list we're building
                dependencies[p] = this_package_mf[p]
        
                update_package(
                    p,            # The package to upate
                    packages_mf,  # The dependencies/dependencies.mf input file
                    dependencies, # The dependencies/dependencies.mf output file 
                    packages_dir, # Path to dependencies
                    package_deps) # Dependency information for each file
    else:
        print("Note: package \"" + package + "\" is not an IVPM package")
        sys.stdout.flush()
     

#********************************************************************
# git_status()
#********************************************************************
def git_status(args):
    ensure_have_project_dir(args)
            
    packages_dir = os.path.join(args.project_dir, "packages")

    # After that check, go ahead and just check directories
    for dir in os.listdir(packages_dir):
        if os.path.isdir(os.path.join(packages_dir, dir, ".git")):
            print("Package: " + dir)
            cwd = os.getcwd()
            os.chdir(packages_dir + "/" + dir)
            status = os.system("git status -s")
            os.chdir(cwd)
        elif dir != "python" and os.path.isdir(os.path.join(packages_dir, dir)):
            print("Note: skipping non-Git package \"" + dir + "\"")
            sys.stdout.flush()

#********************************************************************
# git_update()
#********************************************************************
def git_update(args):
    ensure_have_project_dir(args)
    
    packages_dir = os.path.join(args.project_dir, "packages")
    
    # After that check, go ahead and just check directories
    for dir in os.listdir(packages_dir):
        if os.path.isdir(os.path.join(packages_dir, dir, ".git")):
            print("Package: " + dir)
            cwd = os.getcwd()
            os.chdir(packages_dir + "/" + dir)
            branch = subprocess.check_output(["git", "branch"])
            branch = branch.strip()
            if len(branch) == 0:
                raise Exception("Error: branch is empty")

            if branch[0] == "*":
                branch = branch[1:].strip()

            status = os.system("git fetch")
            status = os.system("git merge origin/" + branch)
            os.chdir(cwd)
        elif os.path.isdir(packages_dir + "/" + dir):
            print("Note: skipping non-Git package \"" + dir + "\"")
            sys.stdout.flush()

#********************************************************************
# update()
#********************************************************************
def update(args):
    
    if args.project_dir is None:
        # If a default is not provided, use the current directory
        print("Note: project_dir not specified ; using working directory")
        args.project_dir = os.getcwd()
    
    etc_dir = os.path.join(args.project_dir, "etc")
    packages_dir = os.path.join(args.project_dir, "packages")
    packages_mf = PackagesInfo()

    if os.path.isfile(os.path.join(etc_dir, "ivpm.info")):
        info = read_info(os.path.join(etc_dir, "ivpm.info"))
    else:
        info = None
    
    # Map between project name and ProjInfo
    package_deps = {}

    if os.path.isdir(packages_dir) == False:
        os.makedirs(packages_dir);
    elif os.path.isfile(packages_dir + "/packages.mf"):
        packages_mf = read_packages(packages_dir + "/packages.mf")

  
    # Ensure that we have a python virtual environment setup
    if 'IVPM_PYTHON' not in os.environ.keys() or os.environ['IVPM_PYTHON'] == "":
        # First, find a Python to use
        python = None
        for p in ["python", "python3"]:
            out = check_output([p, "--version"])

            out_s = out.decode().split()

            if len(out_s) == 2 and out_s[1][0] == "3":
                python = p
                break
        
        if python is None:
            raise Exception("Failed to find Python3")

        if not os.path.isdir(os.path.join(packages_dir, "python")):
            print("Note: creating Python virtual environment")
            sys.stdout.flush()
            os.system(python + " -m venv " + os.path.join(packages_dir, "python"))
            print("Note: upgrading pip")
            sys.stdout.flush()
            if os.path.isdir(os.path.join(packages_dir, "python", "Scripts")):
                ivpm_python = os.path.join(packages_dir, "python", "Scripts", "python")
            else:
                ivpm_python = os.path.join(packages_dir, "python", "bin", "python")
            os.system(ivpm_python + " -m pip install --upgrade pip")
        else:
            print("Note: Python virtual environment already exists")
            sys.stdout.flush()

        if os.path.isdir(os.path.join(packages_dir, "python", "Scripts")):
            ivpm_python = os.path.join(packages_dir, "python", "Scripts", "python")
        else:
            ivpm_python = os.path.join(packages_dir, "python", "bin", "python")
    else:
        ivpm_python = os.environ['IVPM_PYTHON']

    if args.requirements is None:
        # Check to see if a requirements.txt exists already
        for reqs in ["requirements_dev.txt", "requirements.txt"]:
            if os.path.isfile(os.path.join(args.project_dir, reqs)):
                print("Note: Using default requirements \"" + reqs + "\"")
                args.requirements = os.path.join(args.project_dir, reqs);
                break
    
    if os.path.isfile(os.path.join(etc_dir, "packages.mf")):
        # Load the root project dependencies
        dependencies = read_packages(etc_dir + "/packages.mf")
    else:
        dependencies = None

    if args.requirements is None and dependencies is None:
        raise Exception("Neither requirements nor packages.mf provided")

    if args.requirements is not None:    
        # Ensure the Git wrapper is in place. This ensures we don't
        # stomp on existing check-outs when updating dependencies
        scripts_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "scripts")
        print("scripts_dir: " + scripts_dir)
        
        path = os.environ["PATH"]
        os.environ["PATH"] = scripts_dir + ":" + path
        os.system("" + ivpm_python + " -m pip install -r " + args.requirements + " --src " + packages_dir)
        os.environ["PATH"] = path

    if dependencies is not None:
        if info is None:
            raise Exception("Found packages.mf, but no etc/ivpm.info exists")

        # The dependencies list should include this project
        dependencies[info['name']] = "root";
    
        # Add an entry for the root project
        pinfo = ProjInfo(False)
        for d in dependencies.keys():
            pinfo.add_dependency(d)
        package_deps[info["name"]] = pinfo

        for pkg in dependencies.keys():
            if dependencies[pkg] == "root":
                continue

            update_package(
                pkg, 
                packages_mf,
                dependencies, 
                packages_dir,
                package_deps)

        write_packages(packages_dir + "/packages.mf", dependencies)
        write_packages_mk(packages_dir + "/packages.mk", info["name"], package_deps)
        write_sve_f(packages_dir + "/sve.F", info["name"], package_deps)
        write_packages_env(packages_dir + "/packages_env.sh", False, info["name"], package_deps)
        write_packages_env(packages_dir + "/packages_env.csh", True, info["name"], package_deps)
        
def init(args):
#     
    params = dict(
        name=args.name,
        version=args.version
    )

    # TODO: allow override    
    proj = os.getcwd()
    
    ivpm_dir = os.path.dirname(os.path.realpath(__file__))
    templates_dir = os.path.join(ivpm_dir, "templates")
    
    for src,dir in zip(["ivpm.info", "packages.mf", "ivpm.py"],
                    ["etc", "etc", "scripts"]):
        
        with open(os.path.join(templates_dir, src), "r") as fi:
            content = fi.read()
            
            outdir = os.path.join(proj, dir)
            
            if not os.path.isdir(outdir):
                print("Note: Creating directory " + str(outdir))
                os.mkdir(outdir)

            content_t = Template(content)
            content = content_t.safe_substitute(params)
            
            dest = os.path.join(proj, dir, src)
            if os.path.isfile(dest) and not args.force:
                raise Exception("File " + str(dest) + " exists and --force not specified")
            
            with open(dest, "w") as fo:
                fo.write(content)

    # Finally, ensure scripts/ivpm.py is executable
    ivpm_py = os.path.join(proj, "scripts", "ivpm.py")
    st = os.stat(ivpm_py)
    os.chmod(ivpm_py, st.st_mode | stat.S_IEXEC)

def get_parser():
    parser = argparse.ArgumentParser(prog="ivpm")
    
    subparser = parser.add_subparsers()
    subparser.required = True
    subparser.dest = 'command'
    
    update_cmd = subparser.add_parser("update")
    update_cmd.set_defaults(func=update)
    update_cmd.add_argument("-p", "--project-dir", dest="project_dir")
    update_cmd.add_argument("-r", "--requirements", dest="requirements")
    
    init_cmd = subparser.add_parser("init")
    init_cmd.set_defaults(func=init)
    init_cmd.add_argument("-v", "--version", default="0.0.1")
    init_cmd.add_argument("-n", "--name", required=True)
    init_cmd.add_argument("-f", "--force", default=False, action='store_const', const=True)
    
    git_status_cmd = subparser.add_parser("git-status")
    git_status_cmd.set_defaults(func=git_status)
    git_status_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    
    git_update_cmd = subparser.add_parser("git-update")
    git_update_cmd.set_defaults(func=git_update)
    git_update_cmd.add_argument("-p", "-project-dir", dest="project_dir")

    return parser

def main(project_dir=None):
    parser = get_parser()
    
    args = parser.parse_args()

    # If the user hasn't specified the project directory,
    # set the default
    if not hasattr(args, "project_dir") or getattr(args, "project_dir") is None:
        args.project_dir = project_dir

    args.func(args)
    pass

if __name__ == "__main__":
    main()
    
