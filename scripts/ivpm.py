#!/bin/python
import os.path
import sys
import subprocess

# Subcommands
# update: 
# - discovers the project location by finding ivpm.info
# - locates the packages.info file
# - Processes the existing packages (if any)
#   - Load the packages.mf file from the packages directory
# - For each package in the package.info file:
#   - If the selected package is not source
#     - Locate the package.mf file from the repositories
#   - Otherwise,
#
#   - Check the package vers 

# Discover location
# Need to determine the project that we're in

#********************************************************************
#* read_packages
#*
#* Read the content of a packages.info file and return a dictionary
#* with an entry per package with a non-null 
#********************************************************************
def read_packages(packages_mf):
    packages = {}
    
    fh = open(packages_mf, "rb")

    for l in fh.readlines():
        l = l.strip()
        
        comment_idx = l.find("#")
        if comment_idx != -1:
            l = l[0:comment_idx]
        
        if l == "":
            continue
        
        at_idx = l.find("@")
        if at_idx != -1:
            package=l[0:at_idx].strip()
            src=l[at_idx+1:len(l)].strip()
        else:
            package=l
            src=None
     
        if package in packages.keys():
            print("Error: multiple package listings")
            
        packages[package] = src
    
    fh.close()
    
    return packages

#********************************************************************
# write_packages
#********************************************************************
def write_packages(packages_mf, packages):
  fh = open(packages_mf, "w")

  for package in packages.keys():
    fh.write(package + "@" + packages[package] + "\n")

  fh.close()
  
#********************************************************************
# write_packages_mk
#********************************************************************
def write_packages_mk(
        packages_dir, 
        packages):
  packages_mk = packages_dir + "/packages.mk"
  
  fh = open(packages_mk, "w")
  for package in packages.keys():
    info = read_info(packages_dir + "/" + package + "/etc/ivpm.info")
    if "rootvar" in info.keys():
        fh.write(info["rootvar"] + "=$(PACKAGES_DIR)/" + package)
  fh.close()
    
#********************************************************************
# read_info
#
# Reads an .info file, which has the format key=value
#********************************************************************
def read_info(info_file):
    info = {}
    
    fh = open(info_file, "rb")

    for l in fh.readlines():
        l = l.strip()
        
        comment_idx = l.find("#")
        if comment_idx != -1:
            l = l[0:comment_idx]
        
        if l == "":
            continue
        
        eq_idx = l.find("=")
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
#********************************************************************
def update_package(
	package,
    packages_mf,
	packages,
	packages_dir
	):
  package_src = packages[package]
  must_update=False
  
  print "update_package: " + package
    
  if package in packages_mf.keys():
    # See if we are up-to-date or require a change
    if packages_mf[package] != packages[package]:
        print "PackagesMF: " + packages_mf[package] + " != " + packages[package]
        must_update = True
  else:
    must_update = True
    
  if must_update:
    # Package isn't currently present in packages
    scheme_idx = package_src.find("://")
    scheme = package_src[0:scheme_idx+3]
    print "Must add package " + package + " scheme=" + scheme
    if scheme == "file://":
      path = package_src[scheme_idx+3:len(package_src)]
      cwd = os.getcwd()
      os.chdir(packages_dir)
      status = os.system("tar xvzf " + path)
      os.chdir(cwd)
      
      if status != 0:
          print "Error: while unpacking " + package
          
      print "File: " + path
    elif scheme == "http://" or scheme == "https://":
      print "URL"
    else:
        print "Error: unknown scheme " + scheme
    
        

#********************************************************************
# update()
#
# 
#********************************************************************
def update(project_dir, info):
    etc_dir = project_dir + "/etc"
    packages_dir = project_dir + "/packages"
    packages_mf = {}

    if os.path.isdir(packages_dir) == False:
      os.makedirs(packages_dir);
    else:
      if os.path.isfile(packages_dir + "/packages.mf"):
        packages_mf = read_packages(packages_dir + "/packages.mf")
      else:
        print "Error: no packages.mf file"
  
    print "update"

    # Load the root project dependencies
    dependencies = read_packages(etc_dir + "/packages.mf")

    for pkg in dependencies.keys():
      update_package(
	    pkg, 
        packages_mf,
	    dependencies, 
	    packages_dir)

    write_packages(packages_dir + "/packages.mf", dependencies)
    write_packages_mk(packages_dir, dependencies)
    

#********************************************************************
# main()
#********************************************************************
def main():
    scripts_dir = os.path.dirname(os.path.realpath(__file__))
    project_dir = os.path.dirname(scripts_dir)
    etc_dir = os.path.dirname(scripts_dir) + "/etc"
    packages_dir = os.path.dirname(scripts_dir) + "/packages"
    
    if os.path.isfile(etc_dir + "/ivpm.info") == False:
        print("Error: no ivpm.info file in the etc directory ("+etc_dir+")")
        exit(1)

    if os.path.isfile(etc_dir + "/packages.mf") == False:
        print("Error: no packages.mf file in the etc directory ("+etc_dir+")")
        exit(1)
    
    if len(sys.argv) < 2:
        print("Error: too few args")
        exit(1)
        
    cmd = sys.argv[1]

    info = read_info(etc_dir + "/ivpm.info");
    
    if cmd == "update":
        update(project_dir, info)
    elif cmd == "build":
        print("Build")
    else:
        print("Error: " + cmd)
        
    
    print "Package: " + info["name"];
    print "Version: " + info["version"];

    # Load the root dependencies
#    for d in dependencies.keys():
#      print "Dependency: package=" + d + " " + dependencies[d];
    
if __name__ == "__main__":
    main()


    
