#########################
Getting Started with IVPM
#########################

Installing IVPM
***************

IVPM must be installed before it can be used to work with a project. Typically,
the easiest approach is to install IVPM as a user-installed package:

.. code-block:: bash

    % python3 -m pip install --user ivpm

Once this is done, you can invoke IVPM either via the entry-point script (ivpm)
or as a Python module:

.. code-block:: bash

    % ivpm --help
    % python3 -m ivpm --help

Creating a Workspace with ivpm clone
************************************
The clone sub-command creates a new workspace from an existing local or remote
Git repository and immediately initializes it by running ivpm update.

Usage:

.. code-block:: bash

    % ivpm clone [options] <src-url-or-path> [workspace_dir]

If workspace_dir is not specified, the basename of the source is used. The
workspace directory must not already exist.

Common options:
- -a / --anonymous: clone anonymously over HTTPS instead of using SSH. By default,
  HTTPS URLs are converted to SSH form (git@host:path) when applicable.
- -b / --branch <name>: checkout the specified branch. If origin/<name> exists, it
  will be checked out tracking the remote; otherwise, a new local branch is created.
- -d / --dep-set <name>: specify the dependency set for ivpm update.
- --py-uv or --py-pip: choose whether ivpm update should use "uv" or "pip"
  to manage the project-local Python virtual environment.

Examples:

.. code-block:: bash

    # Clone a repository and use the default workspace directory name
    % ivpm clone https://github.com/fvutils/ivpm

    # Clone into a specific directory and create a new branch
    % ivpm clone https://github.com/fvutils/ivpm my-ws -b feature/x

    # Clone anonymously (HTTPS) and select a dependency set
    % ivpm clone -a https://github.com/fvutils/ivpm my-ws -d default-dev


************************************
After fetching the source for an IVPM-enabled project, the `ivpm update` command
is used to fetch source dependencies and initialize a Python virtual environment
for the project.

The IVPM project, itself, is IVPM-enabled. The steps to fetch and initialize
the project are shown below (assuming ivpm has already been installed):

.. code-block:: bash

    % git clone https://github.com/fvutils/ivpm
    % cd ivpm
    % ivpm update

Note that, by default, IVPM clones sub-projects using your ssh public key. If
this key has not been registered with a Git server, then cloning any projects
from that server will fail. Git projects can be cloned using https 
(ie anonymously) by running `ivpm update -a`.

IVPM performs the following tasks as a part of the `update` operation:

- Fetches the source for each dependent project (eg .git, .tar.gz, .jar, etc)
- Identifies dependent projects that are, themselves, IVPM-enabled and 
  determines sub-dependencies
- Identifies dependent projects that are Python projects and dependencies 
  from PyPi. Installs these into the project-local Python virtual 
  environment, with source projects installed in `editable` mode.

Initializing a New IVPM Project
*******************************


