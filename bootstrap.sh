#!/bin/bash

scripts_dir=$(dirname $(realpath $0))

if test "x$IVPM_PYTHON" = "x"; then
    IVPM_PYTHON=python3
fi

${IVPM_PYTHON} -m venv ${scripts_dir}/packages/python

${scripts_dir}/packages/python/bin/python -m pip install -r requirements.txt

PYTHONPATH=${scripts_dir}/src ${scripts_dir}/packages/python/bin/python -m ivpm update

