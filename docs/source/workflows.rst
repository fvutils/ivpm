######################
Development Workflows
######################

Overview
========

This guide covers common workflows for developing with IVPM, from project 
initialization through daily development and release preparation.

Project Lifecycle
=================

Starting a New Project
----------------------

**Step 1: Initialize**

.. code-block:: bash

    $ mkdir my-project
    $ cd my-project
    $ git init
    $ ivpm init my-project -v 0.1.0

**Step 2: Configure Dependencies**

Edit ``ivpm.yaml``:

.. code-block:: yaml

    package:
      name: my-project
      version: "0.1.0"
      default-dep-set: default-dev
      
      dep-sets:
        - name: default
          deps:
            - name: requests
              src: pypi
        
        - name: default-dev
          deps:
            - name: requests
              src: pypi
            - name: pytest
              src: pypi
            - name: black
              src: pypi

**Step 3: Create Project Structure**

.. code-block:: bash

    $ mkdir -p src/my_project test docs
    $ touch src/my_project/__init__.py
    $ touch test/test_basic.py
    $ echo "# My Project" > README.md
    $ echo "packages/" > .gitignore
    $ echo "build/" >> .gitignore
    $ echo "__pycache__/" >> .gitignore

**Step 4: Initialize Dependencies**

.. code-block:: bash

    $ ivpm update

**Step 5: Initial Commit**

.. code-block:: bash

    $ git add ivpm.yaml src/ test/ README.md .gitignore
    $ git commit -m "Initial project setup"

Cloning an Existing Project
----------------------------

**For end users:**

.. code-block:: bash

    $ ivpm clone https://github.com/org/project.git
    $ cd project
    # Ready to use!

**For developers:**

.. code-block:: bash

    $ ivpm clone https://github.com/org/project.git -b develop
    $ cd project
    $ direnv allow
    $ pytest

Working with Dependencies
=========================

Adding a New Dependency
-----------------------

**Step 1: Add to ivpm.yaml**

.. code-block:: yaml

    deps:
      - name: numpy
        src: pypi
        version: ">=1.20"

**Step 2: Update**

.. code-block:: bash

    $ ivpm update

**Step 3: Verify**

.. code-block:: bash

    $ direnv exec . bash -c "python -c 'import numpy; print(numpy.__version__)'"

Adding a Git Dependency
------------------------

.. code-block:: yaml

    deps:
      - name: my-library
        url: https://github.com/org/my-library.git
        branch: main

.. code-block:: bash

    $ ivpm update
    $ ls packages/my-library  # Verify it's there

Adding a Local Development Dependency
--------------------------------------

For co-development of multiple projects:

.. code-block:: yaml

    deps:
      - name: shared-lib
        url: file:///home/user/projects/shared-lib
        src: dir
        link: true

.. code-block:: bash

    $ ivpm update
    $ ls -l packages/shared-lib  # Should be a symlink

Removing a Dependency
---------------------

**Step 1: Remove from ivpm.yaml**

Delete the dependency entry.

**Step 2: Clean up**

.. code-block:: bash

    $ rm -rf packages/dependency-name
    $ ivpm update --py-force-install  # If it was a Python package

Updating Dependency Versions
-----------------------------

**For PyPI packages:**

.. code-block:: yaml

    # Before
    - name: requests
      src: pypi
      version: ">=2.28"
    
    # After
    - name: requests
      src: pypi
      version: ">=2.31"

.. code-block:: bash

    $ ivpm update --py-force-install

**For Git packages:**

.. code-block:: yaml

    # Before
    - name: lib
      url: https://github.com/org/lib.git
      tag: v1.0.0
    
    # After
    - name: lib
      url: https://github.com/org/lib.git
      tag: v1.1.0

.. code-block:: bash

    $ rm -rf packages/lib
    $ ivpm update

Tearing Down a Workspace
------------------------

To reset all dependencies (the inverse of ``update``) — gated so you do not lose
uncommitted or unpushed work:

.. code-block:: bash

    $ ivpm destroy --deps-only

To remove an entire workspace, root and all (the inverse of ``clone``):

.. code-block:: bash

    $ ivpm destroy ../scratch-workspace

Unlike ``rm -rf``, ``destroy`` refuses to delete imports holding local
modifications, untracked files, or unpushed commits, and unlinks cache-backed
dependencies instead of recursing into the shared cache. See :doc:`destroy`.

Sharing a Tool Directory Across Projects
========================================

When several projects need the same simulator and synthesis tools, install them
once into a shared tree rather than into each project's ``packages/``. See
:doc:`tool_directories`.

**One person (or a CI job) publishes the tree:**

.. code-block:: bash

    $ ivpm install -o /opt/eda \
        --from https://edapack.github.io -d digital-sim -d digital-formal \
        --from https://mycorp.internal/tools -d common

**Everyone else sources it** from their project's ``.envrc``:

.. code-block:: bash

    # .envrc
    source_env /opt/eda/packages.envrc     # shared tools
    source_env packages/packages.envrc     # this project's own deps

The project's own ``packages.envrc`` comes second so project-local
dependencies take precedence over the shared tools.

**Refreshing** replays the recorded spec -- no need to retype the sources:

.. code-block:: bash

    $ ivpm install -o /opt/eda
    $ ivpm status -p /opt/eda       # what is actually installed there

Adding or removing a source means supplying ``--from`` again, which *replaces*
the recorded spec and prints what changed.

Daily Development
=================

Morning Routine
---------------

.. code-block:: bash

    $ cd my-project
    $ git pull
    $ ivpm update  # Get any new dependencies
    $ ivpm status  # Check Git package status
    $ direnv allow

Interactive Development
-----------------------

.. code-block:: bash

    # Start environment
    $ direnv allow
    $ 
    
    # Run your code
    $ python src/main.py
    
    # Run tests
    $ pytest
    
    # Format code
    $ black src/
    
    # Type check
    $ mypy src/
    
    # Done for the session
    $ exit

Running Quick Commands
----------------------

.. code-block:: bash

    # No need to stay in activated environment
    $ direnv exec . pytest
    $ direnv exec . python script.py
    $ direnv exec . black --check src/

Working with Editable Packages
===============================

Modifying Dependency Source
----------------------------

When a dependency is installed in editable mode (Git source):

.. code-block:: bash

    $ cd packages/my-library
    $ git status
    $ # Make changes
    $ git add .
    $ git commit -m "Fix bug"
    $ git push

Changes are immediately available to your project.

Testing Changes Before Committing
----------------------------------

.. code-block:: bash

    $ cd packages/my-library
    $ # Edit files
    $ cd ../..
    $ direnv exec . pytest  # Uses modified code

Creating a Branch in a Dependency
----------------------------------

.. code-block:: bash

    $ cd packages/my-library
    $ git checkout -b feature/new-thing
    $ # Make changes
    $ git push -u origin feature/new-thing
    $ cd ../..

Update ``ivpm.yaml`` to use the new branch:

.. code-block:: yaml

    - name: my-library
      url: https://github.com/org/my-library.git
      branch: feature/new-thing

Submitting Upstream Changes
----------------------------

.. code-block:: bash

    # 1. Make changes in packages/my-library
    $ cd packages/my-library
    $ git checkout -b fix/issue-123
    $ # Make changes
    $ git commit -m "Fix issue 123"
    $ git push -u origin fix/issue-123
    
    # 2. Create pull request on GitHub
    
    # 3. After merge, update your project
    $ cd ../..
    $ vim ivpm.yaml  # Remove branch override if used
    $ ivpm sync  # Or rm -rf packages/my-library && ivpm update

Git Package Management
======================

Checking Package Status
-----------------------

.. code-block:: bash

    $ ivpm status

Output shows:

- Modified files
- Uncommitted changes
- Branch information
- Ahead/behind upstream

Example output::

    Package: my-library
      Branch: main
      Status: Clean
    
    Package: test-utils
      Branch: develop
      Status: Modified
      M  src/utils.py
      ?? new_file.txt

Syncing with Upstream
---------------------

Update all Git packages from their upstream:

.. code-block:: bash

    $ ivpm sync

This runs ``git fetch`` and ``git merge`` for each Git package.

**Selective sync:**

.. code-block:: bash

    $ cd packages/my-library
    $ git pull
    $ cd ../..

Handling Merge Conflicts
-------------------------

If ``ivpm sync`` encounters conflicts:

.. code-block:: bash

    $ ivpm sync
    # Error: Merge conflict in packages/my-library
    
    $ cd packages/my-library
    $ git status
    $ # Resolve conflicts
    $ git add .
    $ git commit
    $ cd ../..

Switching Branches
------------------

**Temporary branch switch:**

.. code-block:: bash

    $ cd packages/my-library
    $ git checkout feature-branch
    $ cd ../..
    $ direnv exec . pytest

**Permanent branch switch:**

Update ``ivpm.yaml``:

.. code-block:: yaml

    - name: my-library
      url: https://github.com/org/my-library.git
      branch: feature-branch

.. code-block:: bash

    $ rm -rf packages/my-library
    $ ivpm update

Building and Testing
====================

Building Python Extensions
--------------------------

For packages with native extensions:

.. code-block:: bash

    $ ivpm build

**Debug build:**

.. code-block:: bash

    $ ivpm build --debug

**Specific dependency set:**

.. code-block:: bash

    $ ivpm build -d default-dev

Running Tests
-------------

.. code-block:: bash

    # All tests
    $ direnv exec . pytest
    
    # Specific test file
    $ direnv exec . pytest test/test_feature.py
    
    # With coverage
    $ direnv exec . pytest --cov=src
    
    # Verbose
    $ direnv exec . pytest -v

Running Linters and Formatters
-------------------------------

.. code-block:: bash

    # Format code
    $ direnv exec . black src/ test/
    
    # Check formatting
    $ direnv exec . black --check src/
    
    # Type checking
    $ direnv exec . mypy src/
    
    # Linting
    $ direnv exec . pylint src/
    $ direnv exec . flake8 src/

Continuous Integration
----------------------

**GitHub Actions example:**

.. code-block:: yaml

    name: CI
    
    on: [push, pull_request]
    
    jobs:
      test:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v3
          
          - name: Set up Python
            uses: actions/setup-python@v4
            with:
              python-version: '3.10'
          
          - name: Install IVPM
            run: pip install ivpm
          
          - name: Update dependencies
            run: ivpm update -d default-dev
          
          - name: Run tests
            run: direnv exec . pytest --cov=src
          
          - name: Check formatting
            run: direnv exec . black --check src/

Release Workflows
=================

Preparing a Release
-------------------

**Step 1: Switch to release dependencies**

.. code-block:: bash

    $ ivpm update -d default

**Step 2: Run full test suite**

.. code-block:: bash

    $ direnv exec . pytest

**Step 3: Build documentation**

.. code-block:: bash

    $ direnv exec . sphinx-build docs/source docs/build

**Step 4: Update version**

.. code-block:: yaml

    package:
      name: my-project
      version: "1.0.0"  # Update version

**Step 5: Commit and tag**

.. code-block:: bash

    $ git add ivpm.yaml
    $ git commit -m "Release v1.0.0"
    $ git tag -a v1.0.0 -m "Release version 1.0.0"
    $ git push origin main --tags

Creating a Snapshot
-------------------

Create a self-contained copy of your project with all dependencies:

.. code-block:: bash

    $ ivpm snapshot /path/to/snapshot-dir

This creates a directory with:

- Project source
- All dependencies
- Python packages list (``python_pkgs.txt``)
- Updated ``ivpm.yaml`` with exact versions

**Use case:** Archival, reproducible builds, offline development

Deploying to Users
------------------

**Option 1: Users clone with IVPM**

.. code-block:: bash

    $ ivpm clone https://github.com/org/project.git

**Option 2: Traditional git clone (without IVPM)**

.. code-block:: bash

    $ git clone https://github.com/org/project.git
    $ cd project
    $ pip install ivpm
    $ ivpm update

Note: ``ivpm clone`` automatically runs ``ivpm update`` after cloning, so you only
need to run ``ivpm update`` separately when using ``git clone`` directly.

**Option 2b: Reuse an already-checked-out repository**

When the root repository has already been checked out — for example by a CI
system or a prior ``git clone`` — run ``ivpm clone --here`` from inside it to
populate the workspace in place (reusing the existing clone) and run
``ivpm update``:

.. code-block:: bash

    $ git clone https://github.com/org/project.git
    $ cd project
    $ ivpm clone --here https://github.com/org/project.git

``--here`` is idempotent, so re-running it against an existing clone simply
reuses it rather than failing.

**Option 3: Snapshot distribution**

.. code-block:: bash

    $ ivpm snapshot release-v1.0
    $ tar czf project-v1.0.tar.gz release-v1.0/
    # Distribute tarball

Team Workflows
==============

Onboarding New Team Members
----------------------------

.. code-block:: bash

    # New member's machine
    $ pip install ivpm
    $ ivpm clone https://github.com/company/project.git
    $ cd project
    $ direnv allow
    $ pytest
    # Ready to develop!

Shared Cache Setup
------------------

.. code-block:: bash

    # Admin sets up shared cache
    $ sudo mkdir -p /shared/ivpm-cache
    $ sudo ivpm cache init --shared /shared/ivpm-cache
    $ sudo chown :devteam /shared/ivpm-cache
    
    # Team members add to ~/.bashrc
    export IVPM_CACHE=/shared/ivpm-cache

Benefits:

- First person downloads, everyone else gets instant symlinks
- Saves bandwidth and disk space
- Faster project initialization

Code Review Workflow
--------------------

**Reviewer:**

.. code-block:: bash

    $ git checkout review-branch
    $ ivpm update  # Get any new dependencies
    $ direnv exec . pytest  # Verify tests pass
    $ direnv exec . black --check src/  # Check formatting

Feature Branch Workflow
-----------------------

.. code-block:: bash

    # Start feature
    $ git checkout -b feature/awesome
    $ vim ivpm.yaml  # Add any needed dependencies
    $ ivpm update
    
    # Develop
    $ direnv allow
    $ # work work work
    $ pytest
    $ exit
    
    # Commit
    $ git add .
    $ git commit -m "Add awesome feature"
    $ git push -u origin feature/awesome
    
    # After merge, cleanup
    $ git checkout main
    $ git pull
    $ ivpm update

Common Scenarios
================

Scenario 1: Dependency Has a Bug
---------------------------------

**Quick fix:**

.. code-block:: bash

    $ cd packages/buggy-lib
    $ git checkout -b fix/bug-123
    $ # Fix the bug
    $ git commit -m "Fix bug"
    $ cd ../..
    $ direnv exec . pytest  # Test with fix

**Update project to use fix:**

.. code-block:: yaml

    - name: buggy-lib
      url: https://github.com/org/buggy-lib.git
      branch: fix/bug-123

Scenario 2: Need Older Version Temporarily
-------------------------------------------

.. code-block:: bash

    $ cd packages/my-lib
    $ git checkout v1.0.0
    $ cd ../..
    $ direnv exec . pytest  # Test with old version
    
    # Restore
    $ cd packages/my-lib
    $ git checkout main
    $ cd ../..

Scenario 3: Working on Multiple Projects
-----------------------------------------

.. code-block:: bash

    # Project A uses library in development mode
    $ cd project-a
    $ vim ivpm.yaml
    # - name: shared-lib
    #   url: file:///home/user/projects/shared-lib
    #   src: dir
    $ ivpm update
    
    # Edit library
    $ cd ~/projects/shared-lib
    $ # Make changes
    
    # Test in Project A
    $ cd ~/projects/project-a
    $ direnv exec . pytest  # Uses modified library
    
    # Test in Project B
    $ cd ~/projects/project-b
    $ direnv exec . pytest  # Also uses modified library

Scenario 4: Cleaning Up Stale Dependencies
-------------------------------------------

.. code-block:: bash

    # Remove packages directory
    $ rm -rf packages/

    # Recreate from scratch
    $ ivpm update

Scenario 5: Parallel Branches with Git Worktrees
-------------------------------------------------

Work on a second branch without disturbing your main checkout. IVPM reuses the
main worktree's already-fetched packages automatically:

.. code-block:: bash

    # From your main, already-updated workspace
    $ git worktree add ../myws-feature feature-branch
    $ cd ../myws-feature
    $ ivpm update    # unchanged packages linked from the main worktree; no re-clone

See :doc:`git_worktrees` for details, including how diverging dependencies and
editable packages are handled.

Best Practices
==============

1. **Commit ivpm.yaml** - Always version control your dependency configuration
2. **Use dependency sets** - Separate dev and release dependencies
3. **Pin versions for releases** - Use exact versions or tags for reproducibility
4. **Use ranges for development** - Allow flexibility with ``>=`` version specs
5. **Document custom workflows** - Add team-specific instructions to README
6. **Regular updates** - Run ``ivpm status`` and ``ivpm sync`` periodically
7. **Cache wisely** - Use ``cache: true`` for stable dependencies
8. **Test before committing** - Always run tests after dependency changes
9. **Clean builds periodically** - ``rm -rf packages/ && ivpm update``
10. **Use .gitignore** - Never commit ``packages/`` directory

See Also
========

- :doc:`getting_started` - Initial setup and basic workflows
- :doc:`handlers` - How handlers process packages
- :doc:`git_integration` - Git-specific commands and workflows
- :doc:`git_worktrees` - Accelerated updates inside linked git worktrees
- :doc:`python_packages` - Python package management
- :doc:`caching` - Using cache for better performance
- :doc:`package_lock` - Package lock file and reproducible workspaces
- :doc:`troubleshooting` - Solutions to common problems
