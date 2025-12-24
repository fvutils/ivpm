###############
Troubleshooting
###############

Common Issues and Solutions
============================

Installation Issues
-------------------

IVPM Command Not Found
~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** ``ivpm: command not found``

**Solutions:**

1. **Check installation:**

   .. code-block:: bash

       $ python3 -m pip list | grep ivpm

2. **Install if missing:**

   .. code-block:: bash

       $ python3 -m pip install --user ivpm

3. **Check PATH:**

   .. code-block:: bash

       $ echo $PATH | grep -o ~/.local/bin

   If not present, add to ``~/.bashrc``:

   .. code-block:: bash

       export PATH="$HOME/.local/bin:$PATH"

4. **Use Python module form:**

   .. code-block:: bash

       $ python3 -m ivpm --help

Python Version Mismatch
~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** IVPM requires Python 3.x but system has 2.x

**Solutions:**

.. code-block:: bash

    # Use python3 explicitly
    $ python3 -m pip install --user ivpm
    $ python3 -m ivpm update

Update Issues
-------------

SSH Authentication Failed
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** ``Permission denied (publickey)`` when cloning

**Cause:** SSH key not configured or not registered with Git server

**Solutions:**

1. **Use anonymous cloning:**

   .. code-block:: bash

       $ ivpm update -a

   Or in ``ivpm.yaml``:

   .. code-block:: yaml

       deps:
         - name: package
           url: https://github.com/org/package.git
           anonymous: true

2. **Configure SSH key:**

   .. code-block:: bash

       # Generate key if needed
       $ ssh-keygen -t ed25519 -C "your_email@example.com"
       
       # Add to GitHub/GitLab
       $ cat ~/.ssh/id_ed25519.pub
       # Copy and add to GitHub Settings → SSH Keys

3. **Test SSH connection:**

   .. code-block:: bash

       $ ssh -T git@github.com

Package Already Exists
~~~~~~~~~~~~~~~~~~~~~~

**Problem:** ``Directory packages/xyz already exists``

**Cause:** Dependency already fetched, possibly stale

**Solutions:**

.. code-block:: bash

    # Remove and re-fetch
    $ rm -rf packages/xyz
    $ ivpm update
    
    # Or remove all and start fresh
    $ rm -rf packages/
    $ ivpm update

Git Clone Failed
~~~~~~~~~~~~~~~~

**Problem:** Git clone errors (timeout, network, etc.)

**Solutions:**

1. **Check connectivity:**

   .. code-block:: bash

       $ ping github.com
       $ curl -I https://github.com

2. **Try anonymous:**

   .. code-block:: bash

       $ ivpm update -a

3. **Check firewall/proxy:**

   .. code-block:: bash

       # Set HTTP proxy if needed
       $ export HTTP_PROXY=http://proxy:port
       $ export HTTPS_PROXY=http://proxy:port

4. **Use local mirror:**

   .. code-block:: yaml

       deps:
         - name: package
           url: https://internal-mirror.com/package.git

Dependency Not Found
~~~~~~~~~~~~~~~~~~~~

**Problem:** ``Package xyz not found`` or similar

**Cause:** Typo, wrong dependency set, or package doesn't exist

**Solutions:**

1. **Check spelling:**

   .. code-block:: yaml

       # Wrong
       - name: reqeusts  # Typo
         src: pypi
       
       # Correct
       - name: requests
         src: pypi

2. **Check dependency set:**

   .. code-block:: bash

       $ cat ivpm.yaml  # Verify dep-set
       $ ivpm update -d default-dev  # Explicit set

3. **Verify package exists:**

   .. code-block:: bash

       # For PyPI
       $ python3 -m pip search package-name
       
       # For Git
       $ git ls-remote https://github.com/org/package.git

Python Package Issues
---------------------

Module Not Found
~~~~~~~~~~~~~~~~

**Problem:** ``ModuleNotFoundError: No module named 'xyz'``

**Cause:** Package not installed, or not in active environment

**Solutions:**

1. **Verify installation:**

   .. code-block:: bash

       $ ivpm activate -c "pip list | grep xyz"

2. **Force reinstall:**

   .. code-block:: bash

       $ ivpm update --force-py-install

3. **Check dependency set:**

   .. code-block:: bash

       $ ivpm update -d default-dev  # Ensure correct set

4. **Check if package is Python type:**

   .. code-block:: yaml

       # Add type if auto-detection fails
       - name: xyz
         url: https://github.com/org/xyz.git
         type: python

Editable Install Not Working
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Changes to package source not reflected

**Cause:** Package not installed in editable mode

**Solutions:**

1. **Check installation mode:**

   .. code-block:: bash

       $ ivpm activate -c "pip list | grep xyz"
       # Should show: xyz  <version>  <path-to-packages>

2. **Verify egg-link:**

   .. code-block:: bash

       $ ls packages/python/lib/python*/site-packages/*.egg-link

3. **Reinstall:**

   .. code-block:: bash

       $ ivpm update --force-py-install

4. **Check package has setup.py:**

   .. code-block:: bash

       $ ls packages/xyz/setup.py  # Must exist

Version Conflict
~~~~~~~~~~~~~~~~

**Problem:** ``ERROR: Cannot install xyz because these package versions have incompatible dependencies``

**Cause:** Version requirements conflict between packages

**Solutions:**

1. **Identify conflict:**

   .. code-block:: bash

       $ ivpm activate -c "pip check"

2. **Adjust version specs:**

   .. code-block:: yaml

       # Too restrictive
       - name: package-a
         src: pypi
         version: "==1.0.0"
       
       # More flexible
       - name: package-a
         src: pypi
         version: ">=1.0.0,<2.0"

3. **Check dependency tree:**

   .. code-block:: bash

       $ ivpm activate -c "pip show package-name"

Native Extension Build Failed
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Errors when building C/C++ extensions

**Cause:** Missing compiler, headers, or build dependencies

**Solutions:**

1. **Install build tools:**

   .. code-block:: bash

       # Ubuntu/Debian
       $ sudo apt-get install build-essential python3-dev
       
       # macOS
       $ xcode-select --install
       
       # Windows
       # Install Visual Studio Build Tools

2. **Check setup-deps:**

   .. code-block:: yaml

       setup-deps:
         - cython
         - setuptools
         - wheel

3. **Debug build:**

   .. code-block:: bash

       $ ivpm build --debug
       $ ivpm activate -c "python setup.py build_ext --verbose"

Cache Issues
------------

Cache Not Working
~~~~~~~~~~~~~~~~~

**Problem:** Packages not being cached

**Cause:** ``IVPM_CACHE`` not set or cache not enabled

**Solutions:**

1. **Set environment variable:**

   .. code-block:: bash

       $ export IVPM_CACHE=~/.cache/ivpm
       $ ivpm cache init $IVPM_CACHE

2. **Enable in ivpm.yaml:**

   .. code-block:: yaml

       deps:
         - name: package
           url: https://github.com/org/package.git
           cache: true

3. **Verify:**

   .. code-block:: bash

       $ echo $IVPM_CACHE
       $ ls -la $IVPM_CACHE

Broken Symlinks
~~~~~~~~~~~~~~~

**Problem:** Symlinks in ``packages/`` point to non-existent cache entries

**Cause:** Cache was cleaned or manually deleted

**Solutions:**

.. code-block:: bash

    # Remove broken symlinks
    $ find packages/ -type l ! -exec test -e {} \; -delete
    
    # Re-fetch packages
    $ ivpm update

Cache Permission Denied
~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Cannot write to shared cache

**Cause:** Incorrect permissions on shared cache

**Solutions:**

1. **Check permissions:**

   .. code-block:: bash

       $ ls -ld /shared/ivpm-cache

2. **Add to group:**

   .. code-block:: bash

       $ sudo usermod -a -G devteam $USER
       $ newgrp devteam  # Or logout/login

3. **Fix permissions:**

   .. code-block:: bash

       $ sudo chmod g+s /shared/ivpm-cache
       $ sudo chmod -R g+rw /shared/ivpm-cache

Git Issues
----------

Detached HEAD State
~~~~~~~~~~~~~~~~~~~

**Problem:** Git shows "detached HEAD" warning

**Cause:** Checked out a tag or specific commit

**This is normal for tags/commits. Not an error.**

**To fix if unintended:**

.. code-block:: bash

    $ cd packages/package-name
    $ git checkout main  # Or any branch
    $ cd ../..

Merge Conflicts
~~~~~~~~~~~~~~~

**Problem:** ``ivpm sync`` fails with merge conflicts

**Cause:** Local and remote changes conflict

**Solutions:**

.. code-block:: bash

    $ cd packages/conflicted-package
    
    # Option 1: Keep local changes
    $ git status  # See conflicts
    $ # Edit conflicted files
    $ git add .
    $ git commit
    
    # Option 2: Discard local changes
    $ git reset --hard origin/main
    
    # Option 3: Stash and reapply
    $ git stash
    $ git pull
    $ git stash pop
    
    $ cd ../..

Submodule Not Initialized
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Submodule directories empty

**Cause:** Submodules not initialized

**Solutions:**

.. code-block:: bash

    # Manual initialization
    $ cd packages/repo-with-submodules
    $ git submodule update --init --recursive
    $ cd ../..
    
    # Or re-fetch with IVPM
    $ rm -rf packages/repo-with-submodules
    $ ivpm update

Cannot Push Changes
~~~~~~~~~~~~~~~~~~~

**Problem:** Cannot push to Git repository

**Cause:** No write access or wrong remote URL

**Solutions:**

1. **Check remote:**

   .. code-block:: bash

       $ cd packages/package-name
       $ git remote -v

2. **Change to SSH:**

   .. code-block:: bash

       $ git remote set-url origin git@github.com:user/package.git

3. **Use fork:**

   .. code-block:: bash

       $ git remote add myfork git@github.com:me/package.git
       $ git push myfork branch-name

Environment Issues
------------------

Variables Not Set
~~~~~~~~~~~~~~~~~

**Problem:** Environment variables not available

**Cause:** Not using ``ivpm activate`` or ``env-sets`` not defined

**Solutions:**

1. **Use activate:**

   .. code-block:: bash

       $ ivpm activate -c "echo \$MY_VAR"

2. **Check env-sets:**

   .. code-block:: bash

       $ cat ivpm.yaml  # Verify env-sets section

3. **Re-run update:**

   .. code-block:: bash

       $ ivpm update

Variable Expansion Not Working
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** ``${VAR}`` appears literally

**Cause:** Variable not defined or wrong order

**Solutions:**

.. code-block:: yaml

    # Define variables in dependency order
    env:
      - name: BASE_DIR
        value: "/opt/base"
      
      - name: SUB_DIR
        value: "${BASE_DIR}/sub"  # Now BASE_DIR exists

Virtual Environment Issues
---------------------------

Venv Corrupted
~~~~~~~~~~~~~~

**Problem:** Virtual environment not working

**Cause:** Corruption, version mismatch, or incomplete installation

**Solutions:**

.. code-block:: bash

    # Remove and recreate
    $ rm -rf packages/python
    $ ivpm update

Wrong Python Version
~~~~~~~~~~~~~~~~~~~~

**Problem:** Virtual environment uses wrong Python version

**Cause:** Created with different Python

**Solutions:**

.. code-block:: bash

    $ rm -rf packages/python
    $ python3.10 -m ivpm update  # Use specific version

Activate Not Working
~~~~~~~~~~~~~~~~~~~~

**Problem:** ``ivpm activate`` fails or doesn't change environment

**Cause:** Shell issues or venv not created

**Solutions:**

1. **Check venv exists:**

   .. code-block:: bash

       $ ls packages/python/bin/activate

2. **Try direct activation:**

   .. code-block:: bash

       $ source packages/python/bin/activate

3. **Recreate venv:**

   .. code-block:: bash

       $ rm -rf packages/python
       $ ivpm update

Diagnostic Tools
================

Enable Debug Logging
--------------------

Get detailed information about what IVPM is doing:

.. code-block:: bash

    $ ivpm --log-level DEBUG update

Levels:

- ``NONE`` - No logging (default)
- ``WARN`` - Warnings only
- ``INFO`` - Informational messages
- ``DEBUG`` - Detailed debug information

Check IVPM Version
------------------

.. code-block:: bash

    $ ivpm --version
    $ python3 -m pip show ivpm

List Installed Packages
-----------------------

.. code-block:: bash

    $ ivpm activate -c "pip list"
    $ ivpm activate -c "pip list --format=json"

Check Dependency Tree
---------------------

.. code-block:: bash

    $ ivpm activate -c "pip show package-name"
    $ ivpm activate -c "pipdeptree"  # If installed

Verify ivpm.yaml Syntax
-----------------------

.. code-block:: bash

    $ python3 -c "import yaml; yaml.safe_load(open('ivpm.yaml'))"

Check Git Package Status
------------------------

.. code-block:: bash

    $ ivpm status  # All Git packages
    $ cd packages/package-name && git status  # Specific package

Test Cache
----------

.. code-block:: bash

    $ ivpm cache info
    $ ivpm cache info --verbose
    $ ls -la $IVPM_CACHE

Common Error Messages
=====================

"Missing 'package' section YAML file"
--------------------------------------

**Cause:** Invalid or empty ``ivpm.yaml``

**Solution:** Ensure ``ivpm.yaml`` has a ``package`` section:

.. code-block:: yaml

    package:
      name: my-project

"Dep-set X is not present in project Y"
----------------------------------------

**Cause:** Requested dependency set doesn't exist

**Solution:** Check spelling and definition:

.. code-block:: bash

    $ cat ivpm.yaml  # Verify dep-sets
    $ ivpm update -d default-dev  # Use existing set

"No such file or directory: ivpm.yaml"
---------------------------------------

**Cause:** Not in project root or file doesn't exist

**Solution:**

.. code-block:: bash

    $ ls ivpm.yaml  # Check existence
    $ cd /path/to/project  # Navigate to project root
    $ ivpm init my-project  # Create if missing

"Git command failed"
--------------------

**Cause:** Git clone/fetch/merge failed

**Solutions:**

1. Check network connectivity
2. Verify repository URL
3. Try anonymous mode (``-a``)
4. Check firewall/proxy settings

"Cannot determine source type from url"
---------------------------------------

**Cause:** URL doesn't match known patterns

**Solution:** Explicitly specify ``src``:

.. code-block:: yaml

    - name: package
      url: https://unusual-url.com/package
      src: http  # or git, file, etc.

Getting Help
============

Check Documentation
-------------------

.. code-block:: bash

    $ ivpm --help
    $ ivpm <command> --help

Online Resources:

- Documentation: https://fvutils.github.io/ivpm
- GitHub: https://github.com/fvutils/ivpm
- Issues: https://github.com/fvutils/ivpm/issues

Report a Bug
------------

When reporting bugs, include:

1. IVPM version: ``ivpm --version``
2. Python version: ``python3 --version``
3. Operating system: ``uname -a``
4. Command that failed
5. Full error message
6. Debug output: ``ivpm --log-level DEBUG <command>``

Example bug report:

.. code-block:: text

    **IVPM Version:** 0.15.0
    **Python:** 3.10.12
    **OS:** Ubuntu 22.04
    **Command:** ivpm update -d default-dev
    **Error:** Git clone failed for package xyz
    **Debug output:**
    [Attach debug log]

Ask for Help
------------

- GitHub Issues: https://github.com/fvutils/ivpm/issues
- Include minimal reproducible example
- Share your ``ivpm.yaml`` (redact sensitive info)
- Provide debug output

Prevention Tips
===============

1. **Version control ivpm.yaml** - Track changes
2. **Use .gitignore** - Don't commit ``packages/``
3. **Document custom setup** - README for team members
4. **Test before committing** - ``ivpm update && pytest``
5. **Keep IVPM updated** - ``pip install --upgrade ivpm``
6. **Use virtual environments** - Isolate projects
7. **Regular cache cleanup** - ``ivpm cache clean``
8. **Check status regularly** - ``ivpm status``
9. **Backup configurations** - Copy ``ivpm.yaml``
10. **Read error messages carefully** - They usually explain the issue

See Also
========

- :doc:`getting_started` - Basic usage
- :doc:`workflows` - Common workflows
- :doc:`reference` - Command reference
