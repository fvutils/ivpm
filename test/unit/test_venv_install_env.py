"""
Installs into the project venv see only the venv.

IVPM's launcher puts IVPM's own libraries on PYTHONPATH. An installer that
inherits it treats everything there -- and in the user site -- as already
installed, reports success, and leaves the venv without it.

Covers:
  VE01  venv_install_env strips the leak variables and fronts the venv's bin/
  VE02  pip and uv are run with that environment, and pip without ivpm.pywrap
  VE03  A distribution on PYTHONPATH is still installed into the venv (pip, uv)
  VE04  The post-install check fails when a required distribution is missing
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.diagnostics import SrcLoaderError
from ivpm.handlers.package_handler_python import PackageHandlerPython
from ivpm.installer_run import InstallerResult
from ivpm.utils import get_venv_bindir, get_venv_python, venv_install_env

from .test_base import TestBase


DUMMY = "ivpmdummy"


def _make_handler():
    h = PackageHandlerPython()
    h.reset()
    return h


def _record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _dummy_files():
    """The files of a minimal pure-Python distribution, as {path: bytes}."""
    info = "%s-1.0.dist-info" % DUMMY
    files = {
        "%s/__init__.py" % DUMMY: b"VALUE = 1\n",
        "%s/METADATA" % info: (
            "Metadata-Version: 2.1\nName: %s\nVersion: 1.0\n" % DUMMY).encode(),
        "%s/WHEEL" % info: (
            b"Wheel-Version: 1.0\nGenerator: ivpm-test\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"),
    }
    record = "".join("%s,%s,%d\n" % (p, _record_hash(d), len(d))
                     for p, d in files.items())
    record += "%s/RECORD,,\n" % info
    files["%s/RECORD" % info] = record.encode()
    return files


def _build_wheel(wheel_dir):
    os.makedirs(wheel_dir, exist_ok=True)
    path = os.path.join(wheel_dir, "%s-1.0-py3-none-any.whl" % DUMMY)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in _dummy_files().items():
            zf.writestr(name, data)
    return path


def _install_shadow(shadow_dir):
    """Lay the distribution out as if installed, outside any venv."""
    for name, data in _dummy_files().items():
        dst = os.path.join(shadow_dir, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fp:
            fp.write(data)


def _venv_dists(python_dir):
    env = venv_install_env(python_dir)
    out = subprocess.check_output(
        [get_venv_python(python_dir), "-c",
         "import importlib.metadata as m, json;"
         "print(json.dumps([str(d._path) for d in m.distributions()"
         " if d.metadata['Name'] == %r]))" % DUMMY],
        env=env, text=True)
    return json.loads(out)


# ---------------------------------------------------------------------------
# VE01 -- the environment itself
# ---------------------------------------------------------------------------

class TestVE01Environment(unittest.TestCase):

    BASE = {
        "PATH": "/usr/bin",
        "PYTHONPATH": "/opt/ivpm/lib",
        "PYTHONHOME": "/opt/python",
        "__PYVENV_LAUNCHER__": "/opt/python/bin/python3",
        "VIRTUAL_ENV": "/some/other/venv",
        "HOME": "/home/u",
    }

    def test_VE01a_leak_variables_are_removed(self):
        env = venv_install_env("/w/packages/python", base=self.BASE)
        for var in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"):
            self.assertNotIn(var, env)

    def test_VE01b_user_site_is_disabled(self):
        env = venv_install_env("/w/packages/python", base=self.BASE)
        self.assertEqual("1", env["PYTHONNOUSERSITE"])

    def test_VE01c_virtual_env_names_the_target(self):
        env = venv_install_env("/w/packages/python", base=self.BASE)
        self.assertEqual("/w/packages/python", env["VIRTUAL_ENV"])

    def test_VE01d_venv_scripts_come_first_on_path(self):
        env = venv_install_env("/w/packages/python", base=self.BASE)
        self.assertEqual(
            [get_venv_bindir("/w/packages/python"), "/usr/bin"],
            env["PATH"].split(os.pathsep))

    def test_VE01e_everything_else_is_kept(self):
        env = venv_install_env("/w/packages/python", base=self.BASE)
        self.assertEqual("/home/u", env["HOME"])

    def test_VE01f_base_is_not_modified(self):
        base = dict(self.BASE)
        venv_install_env("/w/packages/python", base=base)
        self.assertEqual(self.BASE, base)


# ---------------------------------------------------------------------------
# VE02 -- the installer is run with it
# ---------------------------------------------------------------------------

class TestVE02InstallerEnv(TestBase):

    def _capture(self, use_uv):
        reqs = os.path.join(self.testdir, "reqs.txt")
        with open(reqs, "w") as fp:
            fp.write("pytest\n")
        seen = []

        def fake_run(cmd, **kw):
            seen.append((list(cmd), kw.get("env")))
            return InstallerResult(returncode=0, lines=[], cmd=list(cmd))

        with patch.dict(os.environ, {"PYTHONPATH": "/opt/ivpm/lib",
                                     "PYTHONHOME": "/opt/python"}), \
             patch("ivpm.handlers.package_handler_python.run_installer",
                   side_effect=fake_run), \
             patch("ivpm.handlers.package_handler_python.shutil.which",
                   return_value="/usr/bin/uv"), \
             patch("ivpm.handlers.package_handler_python.get_venv_python",
                   return_value="/venv/bin/python"):
            _make_handler()._install_requirements(
                "/venv", reqs, False, use_uv=use_uv)
        return seen

    def _assert_clean(self, env):
        # Never let a failure print the environment: it holds credentials.
        self.assertIsNotNone(env)
        self.assertFalse("PYTHONPATH" in env, "PYTHONPATH reached the installer")
        self.assertFalse("PYTHONHOME" in env, "PYTHONHOME reached the installer")
        self.assertEqual("1", env.get("PYTHONNOUSERSITE"))

    def test_VE02a_pip_env_is_clean(self):
        for _, env in self._capture(use_uv=False):
            self._assert_clean(env)

    def test_VE02b_uv_env_is_clean(self):
        for _, env in self._capture(use_uv=True):
            self._assert_clean(env)

    def test_VE02c_pip_runs_without_the_pywrap_wrapper(self):
        """pywrap handed its PYTHONPATH down to pip."""
        cmd, _ = self._capture(use_uv=False)[0]
        self.assertNotIn("ivpm.pywrap", cmd)
        self.assertEqual(["/venv/bin/python", "-m", "pip", "install"], cmd[:4])


# ---------------------------------------------------------------------------
# VE03/VE04 -- against a real venv, offline
# ---------------------------------------------------------------------------

class _RealVenvBase(TestBase):
    """A throwaway venv, a local wheel, and the same distribution on PYTHONPATH.

    Installs are offline: the index is disabled and the wheel is found through
    find-links, so a pass means the venv got its own copy from the wheel.
    """

    WITH_PIP = True

    def setUp(self):
        super().setUp()
        self.python_dir = os.path.join(self.testdir, "venv")
        cmd = [sys.executable, "-m", "venv"]
        if not self.WITH_PIP:
            cmd.append("--without-pip")
        subprocess.check_call(cmd + [self.python_dir],
                              env=venv_install_env(self.python_dir))

        self.wheel_dir = os.path.join(self.testdir, "wheels")
        _build_wheel(self.wheel_dir)
        self.shadow_dir = os.path.join(self.testdir, "shadow")
        _install_shadow(self.shadow_dir)

        self.reqs = os.path.join(self.testdir, "python_pkgs_1.txt")
        with open(self.reqs, "w") as fp:
            fp.write("%s\n" % DUMMY)

    def _leaky_install(self, use_uv):
        """Install with the shadow copy visible the way IVPM's launcher makes
        its lib/ visible: on PYTHONPATH and on this interpreter's sys.path."""
        env = {
            "PYTHONPATH": self.shadow_dir,
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": self.wheel_dir,
            "UV_NO_INDEX": "1",
            "UV_FIND_LINKS": self.wheel_dir,
        }
        with patch.dict(os.environ, env), \
             patch.object(sys, "path", [self.shadow_dir] + sys.path):
            _make_handler()._install_requirements(
                self.python_dir, self.reqs, False, use_uv=use_uv,
                suppress_output=True)

    def _assert_venv_has_own_copy(self):
        paths = _venv_dists(self.python_dir)
        self.assertEqual(1, len(paths), paths)
        self.assertTrue(
            os.path.realpath(paths[0]).startswith(
                os.path.realpath(self.python_dir) + os.sep),
            "%s resolved from outside the venv: %s" % (DUMMY, paths[0]))


class TestVE03PipInstallsDespitePythonpath(_RealVenvBase):

    def test_VE03a_pip(self):
        self._leaky_install(use_uv=False)
        self._assert_venv_has_own_copy()
        # And the venv's own Python imports it with no help from outside.
        subprocess.check_call(
            [get_venv_python(self.python_dir), "-c", "import %s" % DUMMY],
            env=venv_install_env(self.python_dir))


@unittest.skipIf(shutil.which("uv") is None, "uv is not installed")
class TestVE03UvInstallsDespitePythonpath(_RealVenvBase):

    WITH_PIP = False

    def test_VE03b_uv(self):
        self._leaky_install(use_uv=True)
        self._assert_venv_has_own_copy()


class TestVE04PostInstallCheck(_RealVenvBase):

    WITH_PIP = False

    def _check(self, lines):
        with open(self.reqs, "w") as fp:
            fp.write("\n".join(lines) + "\n")
        _make_handler()._verify_installed(self.python_dir, [self.reqs])

    def test_VE04a_missing_distribution_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as cm:
            self._check([DUMMY])
        self.assertIn(DUMMY, str(cm.exception))
        self.assertIn("python_pkgs_1.txt", str(cm.exception))

    def test_VE04b_present_distribution_passes(self):
        _install_shadow(os.path.join(
            subprocess.check_output(
                [get_venv_python(self.python_dir), "-c",
                 "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                env=venv_install_env(self.python_dir), text=True).strip()))
        self._check(["IvpmDummy>=1.0"])

    def test_VE04c_marker_requirements_are_not_checked(self):
        self._check(["%s; python_version < '3'" % DUMMY])

    def test_VE04d_options_and_wheel_paths_are_not_checked(self):
        self._check(["--pre", "./wheels/other-1.0-py3-none-any.whl"])


if __name__ == "__main__":
    unittest.main()
