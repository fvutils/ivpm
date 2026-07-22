#!/bin/bash
#****************************************************************************
# publish.sh
#
# Manually build and publish ivpm to PyPI.
#
# This mirrors the "Publish to PyPi" step in .github/workflows/ci.yml, but
# publishes the clean release version (the BASE in src/ivpm/__version__.py)
# rather than a CI dev build that carries a .${GITHUB_RUN_ID} suffix.
#
# Requires an API token in the environment. The CI uses the secret
# PYPI_API_TOKEN; this script accepts PYPI_API_TOKEN or PYPI_TOKEN.
#
# Usage:
#   PYPI_API_TOKEN=pypi-... ./scripts/publish.sh [--test] [--dry-run]
#
#   --test      Publish to TestPyPI instead of PyPI. Uses TEST_PYPI_API_TOKEN
#               (or TEST_PYPI_TOKEN) if set, otherwise falls back to the
#               regular token variable.
#   --dry-run   Build and validate the artifacts, but do not upload.
#****************************************************************************
set -euo pipefail

scripts_dir=$(dirname $(realpath "$0"))
root_dir=$(dirname "${scripts_dir}")

# Pick a Python interpreter, preferring the bootstrap venv if present
if test "x${IVPM_PYTHON:-}" != "x"; then
    PYTHON=${IVPM_PYTHON}
elif test -x "${root_dir}/packages/python/bin/python"; then
    PYTHON="${root_dir}/packages/python/bin/python"
else
    PYTHON=python3
fi

repository="pypi"
dry_run=0
for arg in "$@"; do
    case "${arg}" in
        --test)    repository="testpypi" ;;
        --dry-run) dry_run=1 ;;
        *)
            echo "Error: unknown argument '${arg}'" >&2
            echo "Usage: ${0} [--test] [--dry-run]" >&2
            exit 1
            ;;
    esac
done

# Resolve the token from the environment
if test "${repository}" = "testpypi"; then
    token="${TEST_PYPI_API_TOKEN:-${TEST_PYPI_TOKEN:-${PYPI_API_TOKEN:-${PYPI_TOKEN:-}}}}"
    repository_url="https://test.pypi.org/legacy/"
else
    token="${PYPI_API_TOKEN:-${PYPI_TOKEN:-}}"
    repository_url="https://upload.pypi.org/legacy/"
fi

if test "x${token}" = "x"; then
    echo "Error: no PyPI token found in the environment." >&2
    echo "  Set PYPI_API_TOKEN (the variable CI uses) or PYPI_TOKEN." >&2
    exit 1
fi

version=$(cd "${root_dir}" && PYTHONPATH="${root_dir}/src" "${PYTHON}" -c \
    "from ivpm.__version__ import _pkg_version; print(_pkg_version)")

echo "==== ivpm publish ===================================================="
echo "  Repository : ${repository} (${repository_url})"
echo "  Version    : ${version}"
echo "  Python     : ${PYTHON}"
echo "  Dry run    : $([ ${dry_run} -eq 1 ] && echo yes || echo no)"
echo "====================================================================="

# Ensure build tooling is available. 'packaging' is pinned up to date so that
# 'twine check' recognizes the Metadata 2.4 License-File field emitted by
# modern setuptools.
"${PYTHON}" -m pip install --upgrade build twine 'packaging>=24.2' >/dev/null

# Build fresh artifacts
rm -rf "${root_dir}/dist"
(cd "${root_dir}" && "${PYTHON}" -m build .)

# Validate metadata before uploading
"${PYTHON}" -m twine check "${root_dir}"/dist/*

if test ${dry_run} -eq 1; then
    echo "Dry run: built and validated artifacts, skipping upload."
    ls -l "${root_dir}/dist"
    exit 0
fi

# Upload. Credentials are passed via environment to avoid leaking on the CLI.
TWINE_USERNAME="__token__" \
TWINE_PASSWORD="${token}" \
TWINE_REPOSITORY_URL="${repository_url}" \
    "${PYTHON}" -m twine upload "${root_dir}"/dist/*

echo "Published ivpm ${version} to ${repository}."
