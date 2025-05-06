############
Introduction
############

What is IVPM?
==============
IVPM (short for IP and Verification Package Manager) is a 
lightweight project-local package manager. It excels at 
managing projects where dependencies are co-developed with
the project. 

.. image:: imgs/ivpm_system.excalidraw.svg

Let's look at a simple example. A System-on-Chip 
(SoC) design targeting an FPGA or an ASIC is composed of
multiple IPs written in a hardware-description language (HDL)
like Verilog. These IPs must be present in order to 
simulate the SoC for verification or synthesize the SoC
to an FPGA or ASIC implementation. In order to run 
functional verification on the SoC, Bus Functional
Models (BFMs) are used by the testbench to interact
with the design. If the project dependencies are 
open source or developed in-house, it is often 
necessary to either propose fixes or enhancements to the
original developers.

- IVPM manages a project-local package repository 
  where project dependencies are stored. 

- IVPM manages package sub-dependencies. When one
  package depends on another, IVPM ensures all 
  required packages are fetched.

- IPVM allows packages to express their 'development'
  and 'release' dependencies separately. This means that
  adding a design IP to your project will only bring what 
  is needed to **use** that IP into the package repository,
  and not all the verification libraries used to verify that IP.

- IVPM provides first-class support for Python packages,
  enabling them to be installed as binary packages or 
  developed inside editable packages.

- IVPM provides Git integration, with commands to quickly
  assess the status of editable projects and update 
  project source from it's upstream repository.

- IVPM enables 'snapshotting' project dependencies to 
  create a project that contains all its required 
  dependencies.

- IVPM provides build/release utilities for C/C++ packages
  that provide a Python interface

- IVPM provides features for discovering source and library paths
  across Python and non-Python packages. This support is 
  currently focused on the needs of managing FuseSoC libraries.



