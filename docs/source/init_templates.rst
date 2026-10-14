##################################
Creating Projects from Templates
##################################

Overview
========

``ivpm init`` creates a new project from a **template**. With no options it
writes the same starter ``ivpm.yaml`` it always has::

    $ ivpm init my-project

Templates are `copier <https://copier.readthedocs.io>`_ templates: a directory
(local, or in a git repository) containing a ``copier.yml`` that declares
*questions* and holds the files to render. A template written for IVPM also
works with plain ``copier copy``.

IVPM collects templates from several places -- IVPM itself, installed Python
packages, your IVPM config files, and an environment variable -- and lets you
pick one by name::

    $ ivpm init --list
    $ ivpm init -t python-lib my-lib

Installing template support
===========================

copier is an optional extra. Install it alongside IVPM to use any template
other than the built-in ``default``:

.. code-block:: bash

    $ pip install 'ivpm[init]'

    # or, without installing it permanently
    $ uvx --with copier ivpm init -t python-lib my-lib
    $ pipx run --spec 'ivpm[init]' ivpm init -t python-lib my-lib

Without copier:

* ``ivpm init`` with the ``default`` template still works. IVPM renders it
  itself and writes byte-for-byte the same ``ivpm.yaml``.
* Listing templates (``ivpm init --list``, ``ivpm show init-templates``) works.
* Any other template fails with a message that says how to install copier.

On Python 3.9, pip installs copier 9.10, the last copier release that
supports 3.9. On newer interpreters it installs the current release.

Choosing a template
===================

``-t`` / ``--template`` takes one of:

* **A template name** from ``ivpm init --list``, e.g. ``-t python-lib``.
* **A local path**, e.g. ``-t ./my-template`` or ``-t ~/templates/fpga``.
  A value is treated as a path if it starts with ``.``, ``/`` or ``~``, or
  if it names an existing copier template directory. An existing template
  directory therefore wins over a registered template with the same name.
* **A git URL or copier shorthand**, e.g. ``-t gh:org/repo``,
  ``-t https://git.acme.com/tmpl/ip.git`` or ``-t git@host:tmpl.git``. Add
  ``--ref v2.1.0`` to select a tag, branch or commit.

Without ``-t``, IVPM uses the ``IVPM_INIT_TEMPLATE`` environment variable if
it is set. Otherwise it uses the ``default-init-template:`` config-file key,
then the site configuration's default, then ``default``.

To see what is available and where each template comes from:

.. code-block:: text

    $ ivpm show init-templates --no-rich
    general:
      default              Minimal ivpm.yaml with default and default-dev dep-sets  [built-in]
    hdl:
      acme-ip              ACME IP block skeleton  [user-config]

    $ ivpm show init-templates acme-ip      # source, trust, and its questions
    $ ivpm show init-templates --json       # for scripts and agents
    $ ivpm show init-templates --all        # include hidden templates

Where the project is created
----------------------------

The project is written into the **current directory**, as ``ivpm init`` always
has. ``NAME`` is the project's name, not a directory to create. Use ``-p``
to create the project somewhere else; the directory is created if needed:

.. code-block:: bash

    $ ivpm init -p my-lib -t python-lib my-lib

``ivpm init`` refuses to run if the directory already has an ``ivpm.yaml``,
unless you pass ``-f`` / ``--force``. ``--force`` also lets the template
overwrite any other existing files.

Answering questions
===================

A template asks its questions interactively. To answer them on the command
line instead, use:

``NAME``
    The answer to ``project_name``. When omitted, it defaults to the name of
    the project directory.

``-v VERSION``
    The answer to ``project_version``. It is short for ``-D project_version=VERSION``.

``-D KEY=VALUE``
    The answer to any question. VALUE is read as YAML, so ``-D use_hdl=true``
    gives a boolean, ``-D width=32`` an integer, and anything else a string.
    ``-D`` may be repeated.

``--defaults``
    Accept every question's default instead of prompting.

**Non-interactive use (CI, scripts, agents).** When standard input is not a
terminal, ``--defaults`` is implied. A question that has no default and no
``-D`` answer then fails with its name, so the command never hangs::

    ivpm init: Template 'python-lib': question 'license' has no default and was not answered.
      Pass it with -D license=VALUE.

To see what a template will write without writing anything, use
``--pretend``.

Suggested defaults
------------------

IVPM pre-fills these question names for any template that uses them:

.. list-table::
   :header-rows: 1

   * - Question
     - Pre-filled from
   * - ``project_name``
     - ``NAME``, else the project directory's name
   * - ``project_version``
     - ``-v``, else ``0.0.1``
   * - ``author_name`` / ``author_email``
     - ``git config user.name`` / ``user.email``
   * - ``year``
     - the current year

You can add your own suggested answers for every template with the
``init-defaults:`` config-file key (below). Answers on the command line always
win.

Adding templates without Python
===============================

The environment variable
------------------------

``IVPM_INIT_TEMPLATE_PATH`` is a list of entries separated by ``:``
(``;`` on Windows). Each entry is one of:

* **a template directory** (contains ``copier.yml``);
* **a collection** -- a directory whose immediate subdirectories are
  templates;
* **a template index file** -- a YAML file with the same list format as the
  ``init-templates:`` config key below. This is how the variable can name
  git-hosted templates, whose URLs could not appear in a ``:``-separated list.

.. code-block:: bash

    export IVPM_INIT_TEMPLATE_PATH=/tools/acme/ivpm-templates:$HOME/my-templates/remote.yaml

Relative entries are resolved against the current directory. An entry that
does not exist is reported as a warning (by ``ivpm init`` and in the Warnings
section of ``ivpm show init-templates``) and skipped.

Config files
------------

The user (``~/.config/ivpm/config.yaml``) and site (``/etc/ivpm/config.yaml``)
config files accept three keys (see :ref:`git-auth-config-files` for where the
files live and how to override their paths):

.. code-block:: yaml

    # Template 'ivpm init' uses when -t is not given
    default-init-template: acme-ip

    init-templates:
      - name: acme-ip                        # a git-hosted template
        src: https://git.acme.com/tmpl/ip.git
        ref: v2.1.0
        description: ACME IP block skeleton
        category: hdl
        trust: true                          # see "Trust" below
      - path: /tools/acme/ivpm-templates     # a template dir or a collection
      - name: fpga                           # a local template, under another name
        src: ~/work/templates/fpga

    # Suggested answers applied to every template
    init-defaults:
      author_name: Jane Doe
      license: Apache-2.0

An entry is either ``path:`` (scanned like an ``IVPM_INIT_TEMPLATE_PATH``
directory; it may also carry ``trust:``) or ``name:`` plus ``src:``, with
optional ``ref``, ``description``, ``category``, ``hidden``, ``trust``,
``defaults`` and ``next_steps``. Relative paths are resolved against the
config file's directory. A malformed entry is reported as a warning and
skipped; it never stops IVPM from running.

When the same name comes from several places
============================================

Templates are looked up in this order. The first template found with a
given name wins:

#. ``IVPM_INIT_TEMPLATE_PATH``
#. the user config file's ``init-templates:``
#. the site config file's ``init-templates:``
#. the active site configuration (``SiteConfig.get_init_templates()``)
#. installed template packs (the ``ivpm.init_templates`` entry point)
#. IVPM's built-in templates

So a more local source overrides a more general one. For example, a site can
replace IVPM's ``default`` by listing its own template named ``default`` in
``/etc/ivpm/config.yaml``, and every bare ``ivpm init`` on that machine then
uses it. An override is never hidden: ``ivpm show init-templates`` marks the
winner ``(shadows N)``, and ``ivpm show init-templates default`` lists what it
shadows and where each came from. To use a shadowed template, pass its path
to ``-t``.

Trust
=====

copier considers some template features *unsafe* because they run commands
on your machine: ``_tasks``, ``_migrations`` and ``_jinja_extensions``. IVPM
allows them only for templates it has reason to trust:

.. list-table::
   :header-rows: 1

   * - Where the template came from
     - Trusted by default
   * - built-in
     - yes
   * - an installed template pack, or the site configuration
     - yes (it is already-installed Python code)
   * - a config-file ``init-templates:`` entry
     - only with ``trust: true``
   * - ``IVPM_INIT_TEMPLATE_PATH``
     - no (environment variables are easy to inherit unknowingly)
   * - a path or URL given to ``-t``
     - no

``--trust`` trusts the template for one invocation. ``--skip-tasks`` renders
a trusted template without running its tasks. Sources you already trust in
copier's own settings file are honored as well. A refused template fails
before anything is written::

    ivpm init: Template 'acme-ip' is not trusted: Template uses potentially unsafe feature: tasks.
      If you trust it, pass --trust, or add 'trust: true' to its init-templates entry in your IVPM config file.

Git-hosted templates
====================

copier fetches git-hosted templates with your ``git`` command, using your
normal git credentials. IVPM's own git settings (``git-auth``,
``git-url-map``, the package cache) do **not** apply to template fetches.

See also
========

- :ref:`contributing-init-templates` in :doc:`extending_ivpm` -- shipping
  templates in a Python package, and writing templates for IVPM
- The ``init`` and ``show init-templates`` commands in :doc:`reference`
