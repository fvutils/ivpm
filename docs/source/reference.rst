#########
Reference
#########

Commands
========

.. argparse::
    :module: ivpm.__main__
    :func: get_parser
    :prog: ivpm


Clone
=====

The clone command creates a new workspace from a Git URL or local path and initializes it.

.. code-block:: text

   ivpm clone [options] <src-url-or-path> [workspace_dir]

Options:
- -a, --anonymous: use HTTPS/anonymous cloning instead of SSH
- -b, --branch <name>: checkout existing remote branch if present; otherwise create a new local branch
- -d, --dep-set <name>: specify dependency set for ivpm update
- --py-uv / --py-pip: choose environment tool for ivpm update

Behavior:
- If workspace_dir is omitted, uses the basename of the source (stripping trailing .git).
- Fails if the target workspace directory already exists.
- After cloning, runs ivpm update in the workspace with the provided options.



YAML File Format
================

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/package-def

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/dep-set

.. jsonschema:: ../../src/ivpm/share/ivpm.json#/defs/package-dep

