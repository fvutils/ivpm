
# Packages Sources and Types

IVPM fetches packages. Each package has a single source and zero or more types.
A source specifies where package data comes from and, consequently, what should
handle fetching it: git, url, file, etc. Package contents have types -- 
Python, for example. Types control how a package is processed. Python content
in a package should be installed in the virtual environment. FuseSoc content
in a package should be registered.

## Package Handlers

Package source is established first, and the appropriate source used to fetch
the package. Package handlers are used to process the packages in several ways:
- Identify content types within a package
- Carry out operations on a package or packages

Package handlers are registered with the system via extensions. Each handler
specifies a numeric phase. IVPM defines major phase buckets, and handlers 
are encouraged to register with these numeric phases. 

Package handlers are evaluated in phase order. Handlers with the same phase 
can be executed in parallel. A package handler receives all IVPM tasks and
ordering information.

## Package Handler Tasks

Phase 1000 is to assign package types. These package handlers look at the 
specified packages add types based on content. For example, the PythonTypeHandler 
might check for setup.py, pyproject.toml, add PythonType if so and the
package doesn't already have.

Phase 10000 creates outputs. For example, the PythonVenvHandler creates 
a virtual environment and installs all Python packages. The EnvrcFileHandler
checks whether the package directory contains export.envrc or .envrc and
adds that to the .envrc file.
