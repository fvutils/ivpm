#!/bin/python
import os.path
import sys
import subprocess
import ivpm

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
# ivpm_main()
# This is the 
#********************************************************************
def ivpm_main(project_dir, argv):
    """Back-compat entry point for the bootstrap ivpm.py stored in projects"""
    
    # Determine where the ivpm package directory is
    scripts_dir = os.path.dirname(os.path.realpath(__file__))
    ivpm_dir = os.path.dirname(scripts_dir)
    
    sys.path.insert(0, os.path.join(ivpm_dir, "src"))
    from ivpm.__main__ import main
    main(project_dir)

