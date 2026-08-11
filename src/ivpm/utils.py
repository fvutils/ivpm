'''
Created on Jun 22, 2021

@author: mballance
'''
import functools
import logging
import os
import sys
import shutil
import subprocess
from typing import List
from ivpm.msg import info, note, fatal, warning
from ivpm.site_config import apply_git_url_map, get_site_config, resolve_git_auth_order
from pathlib import Path

_logger = logging.getLogger("ivpm.utils")

def is_filesystem_root(path):
    """True when *path* is the filesystem root.

    Works on both Unix (``/``) and Windows (``C:\\``, ``D:\\``, etc.).
    """
    return os.path.dirname(path) == path


def https_to_ssh_url(url):
    """Convert an ``https://host/path`` URL to ``git@host:path`` (SSH) form.

    Leaves any non-http(s) URL (``file://``, ``ssh://``, ``git://``), already-SSH
    ``git@host:path`` forms, and non-URL local paths unchanged.
    """
    delim = url.find("://")
    if delim < 0:
        # local path or already in git@host:path form
        return url
    protocol = url[:delim].lower()
    if protocol not in ("http", "https"):
        # file://, ssh://, git://, ... are already in a form git understands
        # (and an ssh:// URL may carry a port, which scp-form cannot express)
        return url
    rest = url[delim + 3:]
    first_sl = rest.find("/")
    if first_sl < 0:
        return url
    return "git@" + rest[:first_sl] + ":" + rest[first_sl + 1:]


def url_host(url):
    """Return the host portion of an ``scheme://[user@]host[:port]/...`` URL.

    Returns ``None`` for non-URL paths and ``git@host:path`` SSH forms.
    """
    delim = url.find("://")
    if delim < 0:
        return None
    rest = url[delim + 3:]
    sl = rest.find("/")
    hostpart = rest if sl < 0 else rest[:sl]
    at = hostpart.find("@")
    if at >= 0:
        hostpart = hostpart[at + 1:]
    return hostpart.split(":")[0] or None


@functools.lru_cache(maxsize=None)
def gh_auth_available(host):
    """True when the GitHub CLI (``gh``) is installed and authenticated for *host*.

    Cached per-host so a multi-package update only probes ``gh`` once per host.
    Used to decide whether an https git URL can be cloned as-is (letting gh's
    credential helper authenticate) instead of being rewritten to SSH.
    """
    if not host:
        return False
    try:
        r = subprocess.run(
            ["gh", "auth", "status", "--hostname", host],
            capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def resolve_clone_url(url, ssh_pref, auth_order=None):
    """Resolve the URL to actually clone.

    *ssh_pref* is an explicit override (per-package option / CLI flag):
      * ``True``  -- force rewrite of https URLs to git@host:path (SSH).
      * ``False`` -- use the URL exactly as written (https/anonymous).
      * ``None``  -- no explicit override; consult *auth_order*.

    *auth_order* is the ordered list of methods to try (``gh``/``ssh``/``https``);
    when ``None`` it is resolved per-host from the site-config rules /
    ``IVPM_GIT_AUTH_ORDER`` / the default order.  The first applicable method
    wins; ``ssh``/``https`` always apply.  If the order yields nothing, fall
    back to the SSH rewrite (the historical default).

    Any ``git-url-map`` rewrite is applied first, so ssh/auth resolution below
    operates on the remapped URL (and its host).
    """
    url = apply_git_url_map(url)

    if ssh_pref is True:
        return https_to_ssh_url(url)
    if ssh_pref is False:
        return url

    host = url_host(url)
    if auth_order is None:
        auth_order = resolve_git_auth_order(host)

    for method in auth_order:
        m = method.strip().lower()
        if m == "gh":
            if gh_auth_available(host):
                return url
        elif m == "ssh":
            return https_to_ssh_url(url)
        elif m in ("https", "anonymous"):
            return url
        # unknown tokens are ignored
    return https_to_ssh_url(url)


def get_venv_bindir(python_dir):
    """Return the ``Scripts`` or ``bin`` directory inside a venv.

    Windows venvs place executables under ``Scripts/``; Unix under ``bin/``.
    """
    scripts = os.path.join(python_dir, "Scripts")
    if os.path.isdir(scripts):
        return scripts
    return os.path.join(python_dir, "bin")


def find_project_root(path):
    pt = path
    while pt != "" and not is_filesystem_root(pt):
        if os.path.isfile(os.path.join(pt, "ivpm.yaml")) and os.path.isdir(os.path.join(pt, "packages")):
            break
        else:
            pt = os.path.dirname(pt)

    if pt == "" or is_filesystem_root(pt):
        return None
    else:
        return pt

def load_project_package_info(project_dir) -> List['ProjInfo']:
    from .ivpm_yaml_reader import IvpmYamlReader
    ret = []

    if not os.path.isfile(os.path.join(project_dir, "ivpm.yaml")):
        raise Exception("Invalid project format: no ivpm.yaml file in %s" % project_dir)

    with open(os.path.join(project_dir, "ivpm.yaml"), "r") as fp:
        ret.append(IvpmYamlReader().read(fp, os.path.join(project_dir, "ivpm.yaml")))

    if os.path.isdir(os.path.join(project_dir, "packages")):
        pkgs_dir = os.path.join(project_dir, "packages")
        for f in os.listdir(pkgs_dir):
            if f != "." and f != ".." and os.path.isdir(os.path.join(pkgs_dir, f)):
                if os.path.isfile(os.path.join(pkgs_dir, f, "ivpm.yaml")):
                    with open(os.path.join(pkgs_dir, f, "ivpm.yaml"), "r") as fp:
                        ret.append(IvpmYamlReader().read(fp, os.path.join(pkgs_dir, f, "ivpm.yaml")))

    return ret


def get_sys_python():
    # Ensure that we have a python virtual environment setup
    if 'IVPM_PYTHON' in os.environ.keys() and os.environ["IVPM_PYTHON"] != "":
        # Trust what we've been told
        note("Using user-specified Python %s" % os.environ["IVPM_PYTHON"])
        python = os.environ["IVPM_PYTHON"]
    else:
        # Default to the executing python
        python = sys.executable
        out = subprocess.check_output([python, "--version"])
        out_s = out.decode().split()

        _logger.info("Using Python version: %s", out_s)
            
    return python
    
def get_venv_python(python_dir):
    # Windows venv
    if os.path.isdir(os.path.join(python_dir, "Scripts")):
        ivpm_python = os.path.join(python_dir, "Scripts", "python")
    elif os.path.isfile(os.path.join(python_dir, "bin", "python")):
        ivpm_python = os.path.join(python_dir, "bin", "python")
    else:
        fatal("Unknown python virtual-environment structure in python_dir (%s)" % python_dir)
        
    return ivpm_python

def setup_venv(python_dir, uv_pip="auto", suppress_output=False, system_site_packages=False):
    note("creating Python virtual environment")

    ivpm_install_args = get_site_config().get_ivpm_install_args()

    if uv_pip == "auto":
        # Determine if we should use pip or 'uv'
        if shutil.which("uv") is not None:
            uv_pip = "uv"
        else:
            uv_pip = "pip"
    
    python = get_sys_python()

    # Setup output redirection for subprocess calls
    if suppress_output:
        stdout_arg = subprocess.DEVNULL
        stderr_arg = subprocess.DEVNULL
    else:
        stdout_arg = None
        stderr_arg = None

    if uv_pip == "uv":
        note("Using 'uv' to manage virtual environment")
        if shutil.which("uv") is None:
            raise Exception("Unable to locate 'uv' executable")
        
        cmd = [
            shutil.which("uv"),
            "venv",
            "--python",
            python,
        ]
        if system_site_packages:
            cmd.append("--system-site-packages")
        cmd.append(python_dir)

        result = subprocess.run(
            cmd,
            stdout=stdout_arg,
            stderr=stderr_arg
        )

        if result.returncode != 0:
            raise Exception("Failed to create virtual environment")

        # Ensure 'uv' knows where to install stuff
        env = os.environ.copy()
        env["VIRTUAL_ENV"] = python_dir

        cmd = [
            shutil.which("uv"),
            "pip",
            "install",
            *ivpm_install_args,
            "setuptools",
            "wheel"
        ]

        result = subprocess.run(
            cmd,
            env=env,
            stdout=stdout_arg,
            stderr=stderr_arg
        )

        if result.returncode != 0:
            raise Exception("Installation of ivpm, setuptools, and wheel failed")

        ivpm_python = get_venv_python(python_dir)
    else:
        note("Using 'pip' to manage virtual environment")
        venv_cmd = [python, "-m", "venv"]
        if system_site_packages:
            venv_cmd.append("--system-site-packages")
        venv_cmd.append(python_dir)
        if suppress_output:
            result = subprocess.run(
                venv_cmd,
                stdout=stdout_arg,
                stderr=stderr_arg
            )
        else:
            os.system(" ".join(venv_cmd))
        note("upgrading pip")
        ivpm_python = get_venv_python(python_dir)

        if suppress_output:
            subprocess.run(
                [ivpm_python, "-m", "pip", "install", "--upgrade", "pip"],
                stdout=stdout_arg,
                stderr=stderr_arg
            )
            subprocess.run(
                [ivpm_python, "-m", "pip", "install", "--upgrade", *ivpm_install_args, "setuptools", "wheel"],
                stdout=stdout_arg,
                stderr=stderr_arg
            )
        else:
            os.system(ivpm_python + " -m pip install --upgrade pip")
            os.system(ivpm_python + " -m pip install --upgrade " + " ".join(ivpm_install_args) + " setuptools wheel")

    
    return ivpm_python
    
    
    
def which(exe : str):
    for p in os.environ['PATH'].split(os.pathsep):
        exe_file = os.path.join(p, exe)
        exe_file_e = os.path.join(p, exe + ".exe")
        if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
            return exe_file
        if os.path.isfile(exe_file_e) and os.access(exe_file_e, os.X_OK):
            return exe_file_e
    return None  
      
def getlocstr(e):
    if hasattr(e, "srcinfo"):
        return "%s:%d:%d" % (
            e.srcinfo.filename, 
            e.srcinfo.lineno,
            e.srcinfo.linepos)
    else:
        return "<no-srcinfo>"
    pass

def getpkgdir(pkg, fallback : str = None):
    """Return the directory of the ivpm.yaml that declared *pkg*.

    Relative paths in a dep entry are authored relative to the file they
    appear in, not to the root project. Falls back to *fallback* (typically
    the root project dir) when the package carries no source info."""
    si = getattr(pkg, "srcinfo", None)
    filename = getattr(si, "filename", None) if si is not None else None
    if filename:
        return os.path.dirname(os.path.abspath(filename))
    return fallback if fallback is not None else os.getcwd()

def resolve_pkg_path(pkg, path : str, fallback : str = None):
    """Expand env vars in *path* and resolve it relative to the ivpm.yaml
    that declared *pkg*."""
    path = os.path.expandvars(path)
    if os.path.isabs(path):
        return path
    return os.path.join(getpkgdir(pkg, fallback), path)

