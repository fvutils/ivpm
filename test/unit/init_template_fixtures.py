"""Shared fixtures for the init-template tests."""
import os
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from unittest import mock

# `ivpm init my-project` output in ivpm 2.41.0, kept here (not read from the
# old template file, which no longer exists) so a change to the built-in
# 'default' template that alters the output fails a test.
GOLDEN_IVPM_YAML = (
    "#*****************************************************************************\n"
    "#* ivpm.yaml\n"
    "#*****************************************************************************\n"
    "package:\n"
    "   name: %s\n"
    "\n"
    "   dep-sets:\n"
    "\n"
    "   - name: default\n"
    "     deps: []\n"
    "\n"
    "   - name: default-dev\n"
    "     deps: [] \n"
    "   \n")


def golden(name):
    return GOLDEN_IVPM_YAML % name


def mk_template(path, copier_yml="project_name: x\n", files=None):
    """Write a copier template at *path*: copier.yml plus {relpath: text}."""
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "copier.yml"), "w") as fp:
        fp.write(textwrap.dedent(copier_yml))
    for rel, text in (files or {}).items():
        fp_path = os.path.join(path, rel)
        os.makedirs(os.path.dirname(fp_path), exist_ok=True)
        with open(fp_path, "w") as fp:
            fp.write(text)
    return path


def git_repo(path, tag=None):
    """Turn *path* into a git repo with one commit (fixed dates; optional tag)."""
    env = dict(os.environ,
               GIT_AUTHOR_DATE="2020-01-01T00:00:00+0000",
               GIT_COMMITTER_DATE="2020-01-01T00:00:00+0000",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=path, env=env, check=True)
    if tag:
        subprocess.run(["git", "tag", tag], cwd=path, env=env, check=True)


@contextmanager
def copier_hidden():
    """Make copier look uninstalled, even in an environment that has it."""
    import importlib.util
    real_find_spec = importlib.util.find_spec

    def _find_spec(name, *a, **kw):
        if name == "copier" or name.startswith("copier."):
            return None
        return real_find_spec(name, *a, **kw)
    saved = {k: v for k, v in sys.modules.items()
             if k == "copier" or k.startswith("copier.")}
    for k in saved:
        del sys.modules[k]
    # A None entry makes 'import copier' raise ImportError.
    try:
        with mock.patch.dict(sys.modules, {"copier": None}), \
                mock.patch.object(importlib.util, "find_spec", _find_spec):
            yield
    finally:
        # patch.dict restored sys.modules as it was on entry, minus copier.
        sys.modules.update(saved)
