.. IVPM documentation master file, created by
   sphinx-quickstart on Sun Apr 17 19:30:15 2022.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to IVPM's documentation!
================================

IVPM (Integrated View Package Manager) is a project-local, polyglot
package manager.  It fetches dependencies from diverse sources -- git,
PyPI, npm, HTTP archives, GitHub releases, local directories, and more --
and assembles unified *views* of your project through an extensible
handler pipeline: a Python virtual environment, a Node.js environment,
a FuseSoC library map, an agent skills directory, a merged direnv file.

One YAML file.  One command.  A complete, self-contained workspace.

.. toctree::
   :maxdepth: 2
   :caption: Contents:
   
   introduction
   getting_started
   core_concepts
   handlers
   dependency_sets
   variables
   package_types
   caching
   package_lock
   python_packages
   node_packages
   github_releases
   environment_paths
   workflows
   git_integration
   show_deps
   integrations
   extending_ivpm
   troubleshooting
   reference



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
