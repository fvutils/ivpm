################
Git Integration
################

Overview
========

IVPM provides deep integration with Git for managing source dependencies. 
This includes cloning repositories, tracking changes, and synchronizing with 
upstream sources.

Clone Options
=============

SSH vs HTTPS
------------

By default, IVPM converts HTTPS URLs to SSH format for authenticated access:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        # Actually clones as: git@github.com:org/my-lib.git

**Why SSH?**

- No password prompts
- Uses your SSH key
- Better for frequent operations
- Standard for development

Anonymous (HTTPS) Cloning
--------------------------

Force HTTPS cloning (no SSH key required):

**Per-package:**

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        anonymous: true

**Command-line:**

.. code-block:: bash

    $ ivpm update -a
    $ ivpm clone -a https://github.com/org/project.git

**Use cases:**

- CI/CD without SSH keys
- Public repositories
- One-time checkouts
- Read-only access

URL Formats
-----------

IVPM supports multiple Git URL formats:

.. code-block:: yaml

    # HTTPS (converted to SSH by default)
    - name: lib1
      url: https://github.com/org/lib1.git
    
    # SSH (used directly)
    - name: lib2
      url: git@github.com:org/lib2.git
    
    # File protocol (local)
    - name: lib3
      url: file:///path/to/repo.git
    
    # Git protocol
    - name: lib4
      url: git://github.com/org/lib4.git

Version Selection
=================

Branch Selection
----------------

Clone a specific branch:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        branch: develop

**Behavior:**

- Checks out the specified branch
- Tracks the remote branch
- Can be updated with ``ivpm sync``

**Command-line (clone):**

.. code-block:: bash

    $ ivpm clone https://github.com/org/project.git -b feature/new

If the branch exists remotely, it tracks it. If not, creates a new local branch.

Tag Selection
-------------

Clone a specific release tag:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        tag: v1.2.0

**Behavior:**

- Checks out the specified tag
- Detached HEAD state
- Immutable (won't change with sync)

**Use case:** Production deployments, reproducible builds

Commit Selection
----------------

Clone to a specific commit:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        commit: abc123def456

**Behavior:**

- Checks out exact commit
- Detached HEAD state
- Maximum reproducibility

**Use case:** Pinning exact versions for critical dependencies

Default Behavior
----------------

No branch/tag/commit specified:

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git

Clones the repository's default branch (usually ``main`` or ``master``).

Clone Depth
===========

Shallow Clones
--------------

Limit history depth for faster cloning:

.. code-block:: yaml

    deps:
      - name: large-repo
        url: https://github.com/org/large-repo.git
        depth: 1

**Benefits:**

- Faster clone
- Less disk space
- Reduced bandwidth

**Limitations:**

- Limited history
- Cannot easily switch branches
- Some Git operations restricted

**Depth values:**

- ``depth: 1`` - Only latest commit
- ``depth: 10`` - Last 10 commits
- Unspecified - Full history (default)

Full History
------------

.. code-block:: yaml

    deps:
      - name: my-lib
        url: https://github.com/org/my-lib.git
        # No depth specified = full history

**Benefits:**

- Complete history
- Full Git functionality
- Easy branch switching
- Better for development

**Use when:**

- Actively developing the package
- Need to browse history
- Switching between branches/tags

When to Use Each
----------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Scenario
     - Recommendation
     - Reason
   * - Active development
     - Full history
     - Need Git features
   * - Cached dependency
     - ``depth: 1``
     - Speed, space
   * - Read-only use
     - ``depth: 1``
     - Speed, space
   * - Production deploy
     - ``depth: 1`` + tag
     - Minimal, reproducible
   * - CI/CD
     - ``depth: 1``
     - Speed

Git Submodules
==============

Automatic Support
-----------------

IVPM automatically initializes submodules when ``.gitmodules`` is present:

.. code-block:: yaml

    deps:
      - name: repo-with-submodules
        url: https://github.com/org/repo.git

**What happens:**

1. Clone main repository
2. Detect ``.gitmodules``
3. Run ``git submodule update --init --recursive``
4. All submodules ready to use

No Configuration Needed
-----------------------

Submodule initialization is automatic - no special configuration required.

Nested Submodules
-----------------

The ``--recursive`` flag handles nested submodules automatically.

Example Structure::

    repo/
    ├── .gitmodules
    ├── submodule1/
    │   └── .gitmodules
    │       └── nested-submodule/
    └── submodule2/

All levels initialized automatically.

Status Command
==============

Checking Package Status
-----------------------

View the status of all Git dependencies:

.. code-block:: bash

    $ ivpm status

**Output example:**

.. code-block:: text

    Package: my-library
      Path: packages/my-library
      Branch: main
      Status: Clean
      Remote: origin/main
      Ahead: 0, Behind: 0
    
    Package: test-utils
      Path: packages/test-utils
      Branch: develop
      Status: Modified
      Modified files:
        M  src/utils.py
        ?? new_file.py
      Remote: origin/develop
      Ahead: 2, Behind: 1

What It Shows
-------------

For each Git package:

- **Package name**
- **Local path**
- **Current branch**
- **Status**: Clean, Modified, Staged, Ahead/Behind
- **Modified files** (if any)
- **Untracked files** (if any)
- **Commits ahead/behind remote**

Use Cases
---------

**Before committing:**

.. code-block:: bash

    $ ivpm status  # Check for uncommitted changes

**After pulling:**

.. code-block:: bash

    $ git pull
    $ ivpm status  # Check dependency status

**Daily standup:**

.. code-block:: bash

    $ ivpm status  # Review what you're working on

Status for Specific Package
----------------------------

.. code-block:: bash

    $ cd packages/my-library
    $ git status

Sync Command
============

Synchronizing with Upstream
----------------------------

Update all Git packages from their remote origins:

.. code-block:: bash

    $ ivpm sync

**What it does:**

For each Git package on a branch:

1. ``git fetch origin``
2. ``git merge origin/<branch>``

**Packages NOT synced:**

- Packages on tags (immutable)
- Packages on specific commits (immutable)
- Packages with uncommitted changes (safety)

Handling Conflicts
------------------

If ``ivpm sync`` encounters merge conflicts:

.. code-block:: bash

    $ ivpm sync
    # Error in packages/my-library

    $ cd packages/my-library
    $ git status
    # Resolve conflicts manually
    $ git add resolved-file.py
    $ git commit
    $ cd ../..
    $ ivpm sync  # Continue with remaining packages

Selective Sync
--------------

Sync specific packages manually:

.. code-block:: bash

    $ cd packages/specific-package
    $ git pull
    $ cd ../..

Safe Sync Strategy
------------------

.. code-block:: bash

    # Check status first
    $ ivpm status
    
    # Stash local changes if needed
    $ cd packages/my-library
    $ git stash
    $ cd ../..
    
    # Sync
    $ ivpm sync
    
    # Restore changes
    $ cd packages/my-library
    $ git stash pop
    $ cd ../..

When NOT to Sync
----------------

Don't sync if:

- You have uncommitted changes you want to keep
- You're working on a feature branch
- You've pinned to a specific commit/tag
- You're testing local modifications

Complete Git Workflows
======================

Workflow 1: Contributing to a Dependency
-----------------------------------------

.. code-block:: bash

    # 1. Create feature branch
    $ cd packages/dependency
    $ git checkout -b fix/issue-123
    
    # 2. Make changes
    $ vim src/file.py
    $ git add src/file.py
    $ git commit -m "Fix issue 123"
    
    # 3. Push to fork
    $ git remote add myfork git@github.com:me/dependency.git
    $ git push myfork fix/issue-123
    
    # 4. Create pull request on GitHub
    
    # 5. After merge, switch back
    $ git checkout main
    $ git pull
    $ cd ../..

Workflow 2: Testing Upstream Changes
-------------------------------------

.. code-block:: bash

    # 1. Check current status
    $ ivpm status
    
    # 2. Fetch latest
    $ cd packages/my-library
    $ git fetch origin
    
    # 3. Check what's new
    $ git log HEAD..origin/main
    
    # 4. Test changes
    $ git checkout origin/main  # Detached HEAD
    $ cd ../..
    $ ivpm activate -c "pytest"
    
    # 5. If good, merge
    $ cd packages/my-library
    $ git checkout main
    $ git merge origin/main
    $ cd ../..

Workflow 3: Bisecting a Bug
----------------------------

.. code-block:: bash

    # Start bisect in dependency
    $ cd packages/buggy-lib
    $ git bisect start
    $ git bisect bad  # Current version is bad
    $ git bisect good v1.0.0  # Known good version
    
    # Test each commit
    $ cd ../..
    $ ivpm activate -c "pytest"
    $ cd packages/buggy-lib
    $ git bisect good  # or 'bad'
    
    # Repeat until found
    
    # Done
    $ git bisect reset
    $ cd ../..

Workflow 4: Pinning After Testing
----------------------------------

.. code-block:: bash

    # 1. Test current state
    $ ivpm activate -c "pytest"
    
    # 2. Get current commit
    $ cd packages/my-library
    $ git rev-parse HEAD
    abc123def456...
    $ cd ../..
    
    # 3. Pin in ivpm.yaml
    # - name: my-library
    #   url: https://github.com/org/my-library.git
    #   commit: abc123def456
    
    # 4. Verify
    $ rm -rf packages/my-library
    $ ivpm update
    $ ivpm activate -c "pytest"

Integration with Git Hooks
===========================

Pre-commit Hook
---------------

Check package status before committing:

.. code-block:: bash

    # .git/hooks/pre-commit
    #!/bin/bash
    
    echo "Checking IVPM package status..."
    if ivpm status | grep -q "Modified\|Untracked"; then
        echo "Warning: Uncommitted changes in dependencies"
        ivpm status | grep -A 10 "Modified\|Untracked"
        read -p "Continue anyway? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

Post-merge Hook
---------------

Update dependencies after merge:

.. code-block:: bash

    # .git/hooks/post-merge
    #!/bin/bash
    
    if git diff-tree --name-only HEAD@{1} HEAD | grep -q "ivpm.yaml"; then
        echo "ivpm.yaml changed, running ivpm update..."
        ivpm update
    fi

Advanced Patterns
=================

Multiple Remotes
----------------

.. code-block:: bash

    $ cd packages/my-library
    
    # Add upstream remote
    $ git remote add upstream https://github.com/org/my-library.git
    
    # Fetch from upstream
    $ git fetch upstream
    
    # Merge upstream changes
    $ git merge upstream/main
    
    $ cd ../..

Working with Forks
------------------

.. code-block:: yaml

    # Use your fork
    deps:
      - name: my-library
        url: https://github.com/myuser/my-library.git
        branch: my-feature

.. code-block:: bash

    $ cd packages/my-library
    
    # Add original as upstream
    $ git remote add upstream https://github.com/org/my-library.git
    $ git fetch upstream
    
    # Keep fork updated
    $ git merge upstream/main
    $ git push origin main

Sparse Checkout
---------------

For very large repositories, use sparse checkout:

.. code-block:: bash

    $ cd packages/huge-repo
    $ git sparse-checkout init --cone
    $ git sparse-checkout set path/to/needed/files
    $ cd ../..

Best Practices
==============

1. **Use branches for development** - Not tags or commits
2. **Commit before sync** - Don't lose local changes
3. **Regular status checks** - Know what's modified
4. **Document pin decisions** - Comment why you pinned a commit
5. **Use tags for releases** - Immutable, semantic
6. **Shallow clones for cache** - Speed up cached dependencies
7. **Full history for dev** - Better debugging and history
8. **Check before updating** - ``ivpm status`` first
9. **Stash when needed** - ``git stash`` for temporary changes
10. **Use .gitignore** - Never commit ``packages/`` directory

Troubleshooting
===============

Detached HEAD State
-------------------

**Symptom:** Git says "detached HEAD"

**Cause:** Checked out a tag or commit

**Solution:**

.. code-block:: bash

    $ cd packages/my-library
    $ git checkout main  # Or any branch
    $ cd ../..

Merge Conflicts During Sync
----------------------------

.. code-block:: bash

    $ cd packages/conflicted-package
    $ git status
    $ # Resolve conflicts in files
    $ git add resolved-files
    $ git commit
    $ cd ../..

Cannot Push Changes
-------------------

**Symptom:** Permission denied when pushing

**Check:**

1. Is remote URL correct?
2. Do you have write access?
3. Is SSH key configured?

**Solution:**

.. code-block:: bash

    $ cd packages/my-library
    $ git remote -v  # Check URL
    $ git remote set-url origin git@github.com:me/my-library.git
    $ cd ../..

Submodule Issues
----------------

**Symptom:** Submodules not initialized

**Solution:**

.. code-block:: bash

    $ cd packages/repo-with-submodules
    $ git submodule update --init --recursive
    $ cd ../..

See Also
========

- :doc:`workflows` - Complete development workflows
- :doc:`package_types` - Git package configuration
- :doc:`getting_started` - Basic Git operations
