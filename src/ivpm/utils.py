'''
Created on Jun 22, 2021

@author: mballance
'''
import functools
import hashlib
import logging
import os
import re
import sys
import shutil
import subprocess
from typing import List, Optional
from ivpm.msg import info, note, fatal, warning
from ivpm.site_config import get_site_config
from pathlib import Path

_logger = logging.getLogger("ivpm.utils")


def sha256_file(path: str, _bufsize: int = 65536) -> str:
    """SHA-256 of a file's contents, read in chunks.

    Shared by the patch manifest (which fingerprints what a patch set changed)
    and by cache content verification (which fingerprints what an entry holds).
    One implementation, so the two can never disagree about what "the hash of
    this file" means.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(_bufsize), b""):
            h.update(chunk)
    return h.hexdigest()

#: The characters a package name or a cache version key may contain.  Both are
#: joined straight into a filesystem path, so anything outside this set is
#: either a path traversal or a directory separator that silently changes the
#: shape of the cache.
_SAFE_NAME_CHARS = "-A-Za-z0-9._+"   # '-' first: it is a literal, not a range
_SAFE_PATH_COMPONENT = re.compile("^[%s]+$" % _SAFE_NAME_CHARS)

#: The same set plus ``%``, which is what :func:`safe_version_key` escapes
#: *with*.  Including it makes escaping idempotent, and that matters: a lookup
#: arrives with the raw version while a cache scan reads the already-escaped
#: name off the directory, and both go through the same function.  Without
#: this, the scan would escape a second time and every escaped entry would
#: report as a key collision against itself.
_SAFE_VERSION_KEY = re.compile("^[%s%%]+$" % _SAFE_NAME_CHARS)


def package_name_problem(name) -> Optional[str]:
    """Why *name* is unusable as a package name, or ``None`` if it is fine.

    A package name is a directory name in ``deps/`` and in the cache, and
    nothing validated it before it got there. ``../../etc`` is the obvious
    case; ``.`` and ``..`` are the quiet ones, because they name a directory
    that already exists and every subsequent operation succeeds against the
    wrong tree.
    """
    if not isinstance(name, str) or not name:
        return "a package name must be a non-empty string"
    if name in (".", ".."):
        return "'%s' names a directory that already exists" % name
    if not _SAFE_PATH_COMPONENT.match(name):
        return ("'%s' contains characters that are not allowed in a package "
                "name; use letters, digits, and '. _ + -'" % name)
    return None


def safe_version_key(version: str) -> str:
    """A version key that is safe to use as a single path component.

    Version keys are not authored by hand -- they come from an ETag, a commit
    hash, a release tag -- so mangling one is better than refusing to cache.
    An ``ETag`` may legally contain ``/``, which used to produce
    ``<cache>/<pkg>/abc/def``: the fetch died with a confusing ``ENOENT`` from
    ``shutil.move``, and a cache listing reported ``abc`` as a version.

    Escaping is percent-style and applied only to characters outside the safe
    set, so every key that works today is returned unchanged -- the only keys
    this alters are ones that are already broken.
    """
    if not isinstance(version, str) or not version:
        raise ValueError("a cache version key must be a non-empty string")
    if version in (".", ".."):
        return "".join("%%%02X" % b for b in version.encode("utf-8"))
    if _SAFE_VERSION_KEY.match(version):
        return version
    return "".join(
        c if _SAFE_VERSION_KEY.match(c)
        else "".join("%%%02X" % b for b in c.encode("utf-8"))
        for c in version)


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

    Any ``git-url-map`` rewrite is applied first, so ssh/auth resolution
    operates on the remapped URL (and its host).

    This is the *first* candidate of
    :func:`ivpm.git_auth.clone_url_candidates`, which is the single
    implementation of the selection logic.  Callers that can fall back to
    another transport on an auth failure should use that instead.
    """
    # Imported lazily: git_auth consults the URL helpers in this module.
    from .git_auth import clone_url_candidates
    return clone_url_candidates(url, ssh_pref, auth_order)[0][0]


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
      
def describe_exception(exc) -> str:
    """Render *exc* for a user-facing diagnostic, with enough to act on.

    ``str(exc)`` alone is frequently useless: an ``OSError`` raised without a
    filename renders as a bare "[Errno 2] No such file or directory", naming
    neither the operation nor the path, and an exception with an empty message
    renders as the empty string. So this always names the exception type, adds
    the OSError filename(s) when the exception carries them, and points at the
    innermost ivpm frame -- which is what actually tells the user (or a bug
    report) *where* in ivpm the failure happened.
    """
    text = str(exc).strip()
    type_name = type(exc).__name__
    head = "%s: %s" % (type_name, text) if text else type_name

    extra = []
    # OSError-family: the path is the whole story, and is absent from str(exc)
    # whenever the raiser did not pass a filename.
    for f in (getattr(exc, "filename", None), getattr(exc, "filename2", None)):
        if f and str(f) not in text:
            extra.append("path: %s" % f)

    frame = _innermost_ivpm_frame(exc)
    if frame is not None:
        extra.append("raised at %s" % frame)

    return "%s (%s)" % (head, "; ".join(extra)) if extra else head


def _innermost_ivpm_frame(exc):
    """"<file>:<line> in <func>" for the deepest ivpm frame in *exc*'s traceback.

    Third-party frames (httpx, tarfile, subprocess) are skipped: they say how
    the operation failed, not which ivpm step asked for it.
    """
    import traceback

    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        return None
    ivpm_root = os.path.dirname(os.path.abspath(__file__))
    best = None
    for fs in traceback.extract_tb(tb):
        try:
            path = os.path.abspath(fs.filename)
        except Exception:
            continue
        if path.startswith(ivpm_root + os.sep) or path == ivpm_root:
            best = "%s:%d in %s" % (os.path.relpath(path, ivpm_root),
                                    fs.lineno, fs.name)
    return best


def getlocstr(e):
    # ``srcinfo`` may be present but None (a package built outside the reader),
    # so test the value, not just the attribute -- otherwise formatting a
    # diagnostic raises AttributeError and hides the diagnostic.
    if getattr(e, "srcinfo", None) is not None:
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

