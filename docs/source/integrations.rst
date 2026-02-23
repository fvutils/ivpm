############
Integrations
############

Overview
========

IVPM integrates with various build systems, editors, and CI/CD platforms 
to streamline development workflows.

CMake Integration
=================

IVPM provides CMake scripts for discovering package paths and libraries.

Using IVPM Share Directory
---------------------------

The ``ivpm share`` command returns the path to IVPM's CMake scripts:

.. code-block:: cmake

    # CMakeLists.txt
    execute_process(
        COMMAND ivpm share cmake
        OUTPUT_VARIABLE IVPM_CMAKE_PATH
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    
    list(APPEND CMAKE_MODULE_PATH ${IVPM_CMAKE_PATH})

Finding Package Information
----------------------------

Use ``ivpm pkg-info`` to query package details:

.. code-block:: cmake

    # Get include directories
    execute_process(
        COMMAND ivpm pkg-info incdirs my-package
        OUTPUT_VARIABLE MY_PACKAGE_INCLUDES
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    
    # Get library directories
    execute_process(
        COMMAND ivpm pkg-info libdirs my-package
        OUTPUT_VARIABLE MY_PACKAGE_LIBDIRS
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    
    include_directories(${MY_PACKAGE_INCLUDES})
    link_directories(${MY_PACKAGE_LIBDIRS})

Complete CMake Example
-----------------------

.. code-block:: cmake

    cmake_minimum_required(VERSION 3.10)
    project(MyProject)
    
    # Find IVPM CMake scripts
    execute_process(
        COMMAND ivpm share cmake
        OUTPUT_VARIABLE IVPM_CMAKE_PATH
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    list(APPEND CMAKE_MODULE_PATH ${IVPM_CMAKE_PATH})
    
    # Get package information
    execute_process(
        COMMAND ivpm pkg-info incdirs boost gtest
        OUTPUT_VARIABLE DEPS_INCLUDES
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    
    execute_process(
        COMMAND ivpm pkg-info libdirs boost gtest
        OUTPUT_VARIABLE DEPS_LIBDIRS
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    
    # Use package information
    include_directories(${DEPS_INCLUDES})
    link_directories(${DEPS_LIBDIRS})
    
    # Build your project
    add_executable(myapp src/main.cpp)
    target_link_libraries(myapp boost_system gtest)

VSCode Integration
==================

JSON Schema Support
-------------------

VSCode can provide autocomplete and validation for ``ivpm.yaml`` files.

**Install YAML extension:**

1. Install "YAML" extension by Red Hat
2. Create ``.vscode/settings.json``:

.. code-block:: json

    {
      "yaml.schemas": {
        "https://fvutils.github.io/ivpm/ivpm.json": "ivpm.yaml"
      }
    }

**Benefits:**

- Autocomplete for package attributes
- Validation of YAML structure
- Inline documentation
- Error highlighting

Tasks Integration
-----------------

Create ``.vscode/tasks.json`` for IVPM commands:

.. code-block:: json

    {
      "version": "2.0.0",
      "tasks": [
        {
          "label": "IVPM Update",
          "type": "shell",
          "command": "ivpm update",
          "problemMatcher": [],
          "group": "build"
        },
        {
          "label": "IVPM Status",
          "type": "shell",
          "command": "ivpm status",
          "problemMatcher": []
        },
        {
          "label": "IVPM Activate & Test",
          "type": "shell",
          "command": "ivpm activate -c pytest",
          "problemMatcher": [],
          "group": {
            "kind": "test",
            "isDefault": true
          }
        }
      ]
    }

**Usage:** Ctrl+Shift+P → "Tasks: Run Task" → Select task

Launch Configuration
--------------------

Debug Python code with IVPM environment:

.. code-block:: json

    {
      "version": "0.2.0",
      "configurations": [
        {
          "name": "Python: Current File",
          "type": "python",
          "request": "launch",
          "program": "${file}",
          "console": "integratedTerminal",
          "python": "${workspaceFolder}/packages/python/bin/python",
          "env": {
            "PYTHONPATH": "${workspaceFolder}/src"
          }
        },
        {
          "name": "Python: Pytest",
          "type": "python",
          "request": "launch",
          "module": "pytest",
          "args": ["${workspaceFolder}/test"],
          "console": "integratedTerminal",
          "python": "${workspaceFolder}/packages/python/bin/python"
        }
      ]
    }

CI/CD Integration
=================

GitHub Actions
--------------

IVPM has built-in support for the GitHub Actions cache service.  When a
workflow runs inside GHA, IVPM auto-detects the environment and transparently
stores and restores packages — no ``actions/cache`` step required.

Built-in GHA Cache Backend (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The simplest approach: just run ``ivpm update`` normally.  IVPM detects
``ACTIONS_CACHE_URL`` / ``ACTIONS_RUNTIME_TOKEN`` (set by GHA automatically)
and activates the GHA backend.

**.github/workflows/ci.yml:**

.. code-block:: yaml

    name: CI

    on:
      push:
        branches: [ main, develop ]
      pull_request:
        branches: [ main ]

    jobs:
      test:
        runs-on: ubuntu-latest

        steps:
          - uses: actions/checkout@v4

          - name: Install IVPM
            run: pip install ivpm

          - name: Update dependencies
            run: ivpm update -a -d default-dev
            # No IVPM_CACHE or actions/cache step needed.
            # IVPM auto-detects GHA and caches each package individually.

          - name: Run tests
            run: ivpm activate -c "pytest --cov=src"

          - name: Upload coverage
            uses: codecov/codecov-action@v3

Per-package GHA cache keys follow the pattern
``ivpm-pkg-{OS}-{name}-{version}`` so cache entries are reused across
branches and workflow runs.

The Python virtual environment (``packages/python``) and the pip/uv wheel
cache are also saved and restored automatically.

Explicit Cache Backend Selection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To force a specific backend regardless of environment:

.. code-block:: yaml

    - name: Update dependencies
      run: ivpm update --cache-backend gha -a -d default-dev

Or disable caching entirely:

.. code-block:: yaml

    - name: Update dependencies
      run: ivpm update --cache-backend none -a -d default-dev

Manual ``actions/cache`` Approach (Legacy)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you prefer to manage caching yourself with ``actions/cache``, or if you
are using a GHA version that does not expose ``ACTIONS_CACHE_URL``:

.. code-block:: yaml

    - name: Setup IVPM cache
      uses: actions/cache@v4
      with:
        path: ~/.cache/ivpm
        key: ivpm-${{ runner.os }}-${{ hashFiles('ivpm.yaml') }}
        restore-keys: |
          ivpm-${{ runner.os }}-

    - name: Update dependencies
      run: |
        export IVPM_CACHE=~/.cache/ivpm
        ivpm update --cache-backend filesystem -a -d default-dev

Matrix Testing
~~~~~~~~~~~~~~

.. code-block:: yaml

    strategy:
      matrix:
        python-version: ['3.9', '3.10', '3.11', '3.12']
        os: [ubuntu-latest, macos-latest, windows-latest]

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - name: Install IVPM
        run: pip install ivpm
      
      - name: Update dependencies
        run: ivpm update -a
      
      - name: Test
        run: ivpm activate -c pytest

GitLab CI
---------

**.gitlab-ci.yml:**

.. code-block:: yaml

    image: python:3.10
    
    stages:
      - build
      - test
    
    variables:
      IVPM_CACHE: ${CI_PROJECT_DIR}/.ivpm-cache
    
    cache:
      paths:
        - .ivpm-cache/
        - packages/
    
    before_script:
      - pip install ivpm
      - ivpm cache init $IVPM_CACHE
    
    build:
      stage: build
      script:
        - ivpm update -a -d default-dev
      artifacts:
        paths:
          - packages/
    
    test:
      stage: test
      script:
        - ivpm activate -c "pytest --cov=src"
        - ivpm activate -c "black --check src/"
      coverage: '/TOTAL.*\s+(\d+%)$/'

Jenkins
-------

**Jenkinsfile:**

.. code-block:: groovy

    pipeline {
        agent any
        
        environment {
            IVPM_CACHE = "${env.WORKSPACE}/.ivpm-cache"
        }
        
        stages {
            stage('Setup') {
                steps {
                    sh 'pip install ivpm'
                    sh 'ivpm cache init $IVPM_CACHE'
                }
            }
            
            stage('Dependencies') {
                steps {
                    sh 'ivpm update -a -d default-dev'
                }
            }
            
            stage('Test') {
                steps {
                    sh 'ivpm activate -c "pytest --junitxml=results.xml"'
                }
            }
            
            stage('Lint') {
                steps {
                    sh 'ivpm activate -c "black --check src/"'
                }
            }
        }
        
        post {
            always {
                junit 'results.xml'
            }
        }
    }

Docker Integration
==================

Dockerfile with IVPM
--------------------

.. code-block:: dockerfile

    FROM python:3.10-slim
    
    # Install system dependencies
    RUN apt-get update && apt-get install -y \
        git \
        build-essential \
        && rm -rf /var/lib/apt/lists/*
    
    # Install IVPM
    RUN pip install ivpm
    
    # Set up project
    WORKDIR /app
    COPY ivpm.yaml .
    
    # Fetch dependencies
    RUN ivpm update -a -d default
    
    # Copy application code
    COPY src/ ./src/
    
    # Set environment
    ENV PATH="/app/packages/python/bin:${PATH}"
    
    # Run application
    CMD ["ivpm", "activate", "-c", "python src/main.py"]

Multi-stage Build
-----------------

.. code-block:: dockerfile

    # Build stage
    FROM python:3.10 AS builder
    
    RUN pip install ivpm
    
    WORKDIR /build
    COPY ivpm.yaml .
    RUN ivpm update -a -d default
    
    COPY src/ ./src/
    RUN ivpm activate -c "python setup.py bdist_wheel"
    
    # Runtime stage
    FROM python:3.10-slim
    
    COPY --from=builder /build/dist/*.whl /tmp/
    RUN pip install /tmp/*.whl && rm /tmp/*.whl
    
    CMD ["python", "-m", "myapp"]

Development Container
---------------------

**.devcontainer/devcontainer.json:**

.. code-block:: json

    {
      "name": "IVPM Development",
      "image": "python:3.10",
      "features": {
        "ghcr.io/devcontainers/features/git:1": {}
      },
      "postCreateCommand": "pip install ivpm && ivpm update",
      "customizations": {
        "vscode": {
          "extensions": [
            "ms-python.python",
            "redhat.vscode-yaml"
          ]
        }
      }
    }

Make Integration
================

Makefile with IVPM
------------------

.. code-block:: makefile

    .PHONY: update test lint format clean
    
    # Update dependencies
    update:
    	ivpm update -d default-dev
    
    # Run tests
    test:
    	ivpm activate -c "pytest"
    
    # Run linters
    lint:
    	ivpm activate -c "black --check src/"
    	ivpm activate -c "mypy src/"
    
    # Format code
    format:
    	ivpm activate -c "black src/"
    
    # Clean build artifacts
    clean:
    	rm -rf build/ dist/ *.egg-info
    	find . -type d -name __pycache__ -exec rm -rf {} +
    
    # Full CI workflow
    ci: update test lint

FuseSoC Integration
===================

Using pkg-info with FuseSoC
----------------------------

Query paths for FuseSoC core files:

.. code-block:: bash

    $ ivpm pkg-info paths -k rtl my-ip-core

**ivpm.yaml:**

.. code-block:: yaml

    package:
      name: my-ip
      
      paths:
        rtl:
          vlog:
            - rtl/verilog
          sv:
            - rtl/systemverilog
        
        dv:
          sv:
            - tb/sv

Pre-commit Hooks
================

**.pre-commit-config.yaml:**

.. code-block:: yaml

    repos:
      - repo: local
        hooks:
          - id: ivpm-status
            name: Check IVPM package status
            entry: ivpm status
            language: system
            pass_filenames: false
          
          - id: black
            name: Format with Black
            entry: ivpm activate -c "black"
            language: system
            types: [python]
          
          - id: pytest
            name: Run tests
            entry: ivpm activate -c "pytest"
            language: system
            pass_filenames: false
            stages: [push]

Continuous Deployment
=====================

Deploy with Snapshot
--------------------

.. code-block:: bash

    # Create release snapshot
    $ ivpm snapshot --rls-deps /tmp/release-v1.0
    
    # Package
    $ tar czf release-v1.0.tar.gz -C /tmp release-v1.0
    
    # Upload to server
    $ scp release-v1.0.tar.gz server:/releases/
    
    # On server: extract and use
    $ tar xzf release-v1.0.tar.gz
    $ cd release-v1.0
    $ packages/python/bin/python src/main.py

Docker Registry
---------------

.. code-block:: bash

    # Build image
    $ docker build -t myapp:1.0 .
    
    # Push to registry
    $ docker push myapp:1.0
    
    # Deploy
    $ docker run -d myapp:1.0

Best Practices
==============

1. **Cache in CI/CD** - Speed up builds
2. **Use anonymous mode in CI** - No SSH key setup needed
3. **Pin versions for production** - Reproducible deployments
4. **Separate dev/release deps** - Minimal production images
5. **Document integration** - README for team members
6. **Test integration locally** - Before committing CI config
7. **Use schema validation** - Catch errors early
8. **Monitor cache size** - Clean periodically in CI

See Also
========

- :doc:`workflows` - Development workflows
- :doc:`reference` - Command reference
- :doc:`caching` - Cache management
