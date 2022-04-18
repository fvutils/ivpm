################
Quickstart Guide
################

Installing IVPM
================


Installation via PyPi
---------------------

.. code-block:: bash

   pip install ivpm

Installation from Source
------------------------

.. code-block:: bash

   cd ivpm
   pip install -e .
   

Running a Simple Example
========================

Let's look at a very simple example: a project with a few 
simple dependencies. 
- Ensure you have installed IVPM and check that you 
can run IVPM either by running ``ivpm`` or ``python3 -m ivpm``.

Create a directory named 'simple_proj' and copy the 
following text into a file within simple_proj named 'ivpm.yaml'.

.. code-block:: yaml

    package: 
        name: simple_proj
        version: 0.0.1
    
        dev-deps:
            # FwRISC is a small multi-cycle RISC-V core
            - name: fwrisc
              url: https://github.com/featherweight-ip/fwrisc.git
             
            # PyYAML is used by the verification environment
            - name: pyyaml
              src: pypi
             
            # RISC-V Arch Tests are used by verification as well. 
            # Grab a released version of these
            - name: riscv-arch-test
              url: https://github.com/riscv/riscv-arch-test/archive/refs/tags/2.4.4.tar.gz

Now, enter the 'simple_proj' directory and run ``ivpm update -a``. You should see the 
following directory structure:

- simple_proj
    - ivpm.yaml
    - packages
        - fwrisc
        - python
        - python_pkgs.txt
        - riscv-arch-test

The ``packages/python`` directory contains a Python virtual environment with
access to all Python packages specified as depedendencies. The ``packages/fwrisc``
directory contains a Git clone of the fwrisc repository, while the
``packages/riscv-arch-test`` directory contains the contents of the downloaded
release of the riscv-arch-test project.


