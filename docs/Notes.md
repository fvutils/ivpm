

Core Flows
- build  -- Run a 'build' option on select packages
- snapshot (?)
- sync / git-update
- status / git-status
- update -- fetch missing packages. Possibly install Python packages (?)

Core Commands
- activte
- build
- init 
- pkg-info
- share -- return IVPM's 'share' directory (CMAKE integration)
- sync
- update
- Note: need something to query paths specified in IVPM files

# Capabilities
- Fetch package dependencies
- Manage hierarchical dependencies
- Bulk management of source dependencies, such as update (sync) and status
- Support for multiple dependency profiles (eg development, release, etc)
- Snapshot (create a self-contained copy) of source dependencies
- Create a Python virtual environment with Python dependencies installed
- Capture project environment variables
- Capture project paths, such as search paths for IP meta-data files
- CMake integration for obtaining dependent-package library and include paths
- Plug-in extensible

# Capturing Project Data and Dependencies
- Package name
- Package dependency sets
  - Standard / known names
-  

# Project Setup

# Core Flows
## Update
## Build
## Sync
## Status

# Python Package Support
- Motivation
  - Support development against source and precompiled packages
  - 
- Set DEBUG=1 during update / build
- Python portion of the integration
- Is this something that belongs in IVPM files as well?

# Environment Management
- env-sets
- value, path, path-prepend, path-append
- $IVPM_PACKAGES, $IVPM_PROJECT

# Project Paths Management
- $IVPM_PACKAGES, $IVPM_PROJECT

# Integrations

## Schema-Aware Editing with VSCode

# Extending IVPM

## Adding Source Types

## Adding Package Handlers

## Adding Sub-Commands

# Reference

## File Format

## API


