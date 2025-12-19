# Agent Development Guide

This document provides guidance for AI agents and developers working on the IVPM codebase.

## Running Tests

### Test Infrastructure

IVPM uses Python's built-in `unittest` framework for testing. Tests are located in the `test/` directory with the following structure:

```
test/
├── unit/           # Unit tests
│   ├── __init__.py
│   ├── test_base.py    # Base test class with helpers
│   ├── test_smoke.py   # Smoke tests
│   ├── test_sync.py    # Sync command tests
│   └── ...
└── rundir/         # Test execution directory (created during test runs)
```

### Running All Tests

Tests should be run from the `test/` directory with the proper PYTHONPATH set:

```bash
cd /path/to/ivpm
export PYTHONPATH=$(pwd)/src:$(pwd)/test
cd test
../packages/python/bin/python3 -m unittest
```

This is how the CI (GitHub Actions) runs tests. See `.github/workflows/ci.yml` for the exact configuration.

### Running Specific Test Files

To run a specific test file:

```bash
cd /path/to/ivpm
export PYTHONPATH=$(pwd)/src:$(pwd)/test
cd test
../packages/python/bin/python3 -m unittest unit.test_sync -v
```

### Running Specific Test Cases

To run a specific test case:

```bash
cd /path/to/ivpm
export PYTHONPATH=$(pwd)/src:$(pwd)/test
cd test
../packages/python/bin/python3 -m unittest unit.test_sync.TestSync.test_sync_editable_only -v
```

### Test Requirements

1. **Python Environment**: Tests must use the Python environment in `packages/python/bin/python3`
   - This is the IVPM-managed virtual environment
   - Do NOT use system Python or other virtual environments

2. **PYTHONPATH**: Must include both `src` and `test` directories
   - `src` - for the ivpm package modules
   - `test` - for test utilities and relative imports

3. **Working Directory**: Tests should be run from the `test/` directory
   - This matches the CI configuration
   - Ensures relative paths work correctly

4. **Import Style**: Test files use relative imports
   ```python
   from .test_base import TestBase  # Correct
   ```
   Not:
   ```python
   import sys
   sys.path.insert(...)
   from test_base import TestBase  # Incorrect for CI
   ```

### Writing New Tests

1. Create test file in `test/unit/` directory
2. Import `TestBase` using relative import: `from .test_base import TestBase`
3. Extend `TestBase` class which provides helpful methods:
   - `setUp()` - Creates isolated test directory
   - `tearDown()` - Cleans up test directory
   - `mkFile(filename, content)` - Creates test files
   - `ivpm_update(...)` - Runs IVPM update
   - `ivpm_sync(...)` - Runs IVPM sync
   - `exec(cmd, cwd)` - Executes shell commands

4. Each test gets its own clean test directory at `test/rundir/test/`

### Example Test

```python
import os
from .test_base import TestBase

class TestMyFeature(TestBase):
    
    def test_something(self):
        """Test description"""
        # Create a test project
        self.mkFile("ivpm.yaml", """
        package:
            name: test_project
            dep-sets:
                - name: default-dev
                  deps:
                    - name: some_package
                      url: https://github.com/example/package.git
        """)
        
        # Run IVPM operations
        self.ivpm_update(skip_venv=True)
        
        # Make assertions
        pkg_path = os.path.join(self.testdir, "packages/some_package")
        self.assertTrue(os.path.isdir(pkg_path))
```

### Test Environment Variables

- `DATA_DIR` - Points to `test/unit/data/` directory with test fixtures
- `TEST_DIR` - Points to the current test's working directory
- `IVPM_CACHE` - Cache directory (may be set by tests or system)

### Debugging Tests

1. **Clean test directory before running**:
   ```bash
   rm -rf test/rundir
   ```

2. **Run tests with verbose output**:
   ```bash
   ../packages/python/bin/python3 -m unittest -v
   ```

3. **Check test artifacts**: Test files are created in `test/rundir/test/`
   - You can inspect these after test failures
   - Remember to clean before next run

### Common Issues

1. **ImportError with relative imports**: 
   - Make sure you're running from the `test/` directory
   - Ensure PYTHONPATH includes the `test` directory

2. **"No module named pytest"**:
   - Don't use pytest, use unittest
   - Command: `python3 -m unittest` (not `pytest`)

3. **Test directory conflicts**:
   - Clean `test/rundir/` between full test runs
   - Tests reuse the same directory structure

4. **Package not found**:
   - Ensure `packages/python` virtual environment exists
   - Run `./bootstrap.sh` if needed to initialize

## CI/CD

Tests run automatically on:
- Push to any branch
- Pull requests
- Manual workflow dispatch

See `.github/workflows/ci.yml` for the complete CI configuration.

## Package Types and Caching

Understanding package behavior is important for testing:

- **Editable packages** (no `cache` attribute): Full git clone, writable
- **Read-only packages** (`cache: false`): Shallow clone, read-only
- **Cached packages** (`cache: true`): Symlinked from cache, read-only target

The `sync` command only updates editable (writable) packages.
