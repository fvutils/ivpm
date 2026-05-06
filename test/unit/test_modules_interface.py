"""
Unit tests for modules_interface.py.

These tests verify the parsing and detection logic by mocking
subprocess.run to return canned outputs for each variant.
"""
import os
import subprocess
import unittest
from unittest import mock

from ivpm.modules_interface import (
    ModulesInterface,
    ModulesVariant,
    ModulesError,
    detect_variant,
    _probe_modules_version,
)


def _fake_run(stdout="", stderr="", returncode=0):
    """Return a CompletedProcess with the given fields."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestDetectVariant(unittest.TestCase):
    """Tests for the detect_variant() function."""

    @mock.patch.dict(os.environ, {"LMOD_CMD": "/usr/bin/lmod"}, clear=False)
    @mock.patch("os.path.isfile", return_value=True)
    def test_detect_variant_lmod(self, mock_isfile):
        mi = detect_variant()
        self.assertEqual(mi.variant, ModulesVariant.LMOD)
        self.assertEqual(mi.cmd_path, "/usr/bin/lmod")

    @mock.patch.dict(os.environ, {
        "MODULESHOME": "/opt/modules",
    }, clear=False)
    @mock.patch("os.path.isfile", side_effect=lambda p: p == "/opt/modules/modulecmd.tcl")
    @mock.patch("subprocess.run", return_value=_fake_run(
        stderr="Modules Release 4.8.0"))
    @mock.patch("shutil.which", return_value="/usr/bin/tclsh")
    def test_detect_variant_modules_4x(self, mock_which, mock_run, mock_isfile):
        # Clear LMOD_CMD to avoid lmod detection
        with mock.patch.dict(os.environ, {"LMOD_CMD": ""}, clear=False):
            mi = detect_variant()
        self.assertEqual(mi.variant, ModulesVariant.MODULES_4X)
        self.assertEqual(mi.cmd_path, "/opt/modules/modulecmd.tcl")

    @mock.patch.dict(os.environ, {
        "MODULESHOME": "/opt/modules",
    }, clear=False)
    @mock.patch("os.path.isfile", side_effect=lambda p: p == "/opt/modules/modulecmd.tcl")
    @mock.patch("subprocess.run", return_value=_fake_run(
        stderr="VERSION=3.2.10"))
    @mock.patch("shutil.which", return_value="/usr/bin/tclsh")
    def test_detect_variant_modules_3x(self, mock_which, mock_run, mock_isfile):
        with mock.patch.dict(os.environ, {"LMOD_CMD": ""}, clear=False):
            mi = detect_variant()
        self.assertEqual(mi.variant, ModulesVariant.MODULES_3X_TCL)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("shutil.which", return_value=None)
    def test_detect_variant_none(self, mock_which):
        mi = detect_variant()
        self.assertEqual(mi.variant, ModulesVariant.UNKNOWN)
        with self.assertRaises(ModulesError):
            mi.is_avail("gcc/15.2.0")

    def test_explicit_override(self):
        mi = detect_variant(variant_override="lmod", cmd_override="/custom/lmod")
        self.assertEqual(mi.variant, ModulesVariant.LMOD)
        self.assertEqual(mi.cmd_path, "/custom/lmod")


class TestModulesInterface4x(unittest.TestCase):
    """Tests for ModulesInterface with Modules 4.x variant."""

    def setUp(self):
        self.mi = ModulesInterface(
            variant=ModulesVariant.MODULES_4X,
            cmd_path="/opt/modules/modulecmd.tcl",
        )

    @mock.patch("subprocess.run")
    def test_is_avail_true(self, mock_run):
        mock_run.return_value = _fake_run(stderr="gcc/15.2.0")
        self.assertTrue(self.mi.is_avail("gcc/15.2.0"))

    @mock.patch("subprocess.run")
    def test_is_avail_false(self, mock_run):
        mock_run.return_value = _fake_run(stderr="")
        self.assertFalse(self.mi.is_avail("nonexistent/1.0"))

    @mock.patch("subprocess.run")
    @mock.patch("os.path.exists", return_value=True)
    def test_module_path_4x(self, mock_exists, mock_run):
        mock_run.return_value = _fake_run(
            stderr="/opt/modulefiles/gcc/15.2.0")
        path = self.mi.module_path("gcc/15.2.0")
        self.assertEqual(path, "/opt/modulefiles/gcc/15.2.0")

    @mock.patch("subprocess.run")
    @mock.patch("os.path.exists", return_value=False)
    def test_module_path_not_found(self, mock_exists, mock_run):
        mock_run.return_value = _fake_run(stderr="", returncode=1)
        path = self.mi.module_path("nonexistent/1.0")
        self.assertIsNone(path)

    @mock.patch("subprocess.run")
    def test_module_show_output(self, mock_run):
        show_output = "setenv GCC_HOME /opt/gcc/15.2.0\nprepend-path PATH /opt/gcc/15.2.0/bin"
        mock_run.return_value = _fake_run(stderr=show_output)
        result = self.mi.module_show("gcc/15.2.0")
        self.assertIn("GCC_HOME", result)
        self.assertIn("/opt/gcc/15.2.0", result)


class TestModulesInterfaceLmod(unittest.TestCase):
    """Tests for ModulesInterface with Lmod variant."""

    def setUp(self):
        self.mi = ModulesInterface(
            variant=ModulesVariant.LMOD,
            cmd_path="/usr/bin/lmod",
        )

    @mock.patch("subprocess.run")
    @mock.patch("os.path.exists", return_value=True)
    def test_module_path_lmod(self, mock_exists, mock_run):
        # Lmod show prints modulefile path on first stderr line
        mock_run.return_value = _fake_run(
            stderr="/opt/modulefiles/gcc/15.2.0:\nsetenv GCC_HOME /opt/gcc/15.2.0")
        path = self.mi.module_path("gcc/15.2.0")
        self.assertEqual(path, "/opt/modulefiles/gcc/15.2.0")


class TestModulesInterfaceUnknown(unittest.TestCase):
    """Tests for UNKNOWN variant raising errors."""

    def setUp(self):
        self.mi = ModulesInterface(variant=ModulesVariant.UNKNOWN)

    def test_is_avail_raises(self):
        with self.assertRaises(ModulesError):
            self.mi.is_avail("gcc/15.2.0")

    def test_module_path_raises(self):
        with self.assertRaises(ModulesError):
            self.mi.module_path("gcc/15.2.0")

    def test_module_show_raises(self):
        with self.assertRaises(ModulesError):
            self.mi.module_show("gcc/15.2.0")

    def test_avail_raises(self):
        with self.assertRaises(ModulesError):
            self.mi.avail()


class TestProbeModulesVersion(unittest.TestCase):
    """Tests for _probe_modules_version()."""

    @mock.patch("subprocess.run")
    def test_4x_detected(self, mock_run):
        mock_run.return_value = _fake_run(stderr="Modules Release 4.8.0")
        variant = _probe_modules_version("/opt/modules/modulecmd.tcl", "/usr/bin/tclsh")
        self.assertEqual(variant, ModulesVariant.MODULES_4X)

    @mock.patch("subprocess.run")
    def test_3x_detected(self, mock_run):
        mock_run.return_value = _fake_run(stderr="VERSION=3.2.10")
        variant = _probe_modules_version("/opt/modules/modulecmd.tcl", "/usr/bin/tclsh")
        self.assertEqual(variant, ModulesVariant.MODULES_3X_TCL)

    @mock.patch("subprocess.run", side_effect=FileNotFoundError("not found"))
    def test_fallback_tcl_extension(self, mock_run):
        variant = _probe_modules_version("/opt/modules/modulecmd.tcl", "/usr/bin/tclsh")
        self.assertEqual(variant, ModulesVariant.MODULES_3X_TCL)

    @mock.patch("subprocess.run", side_effect=FileNotFoundError("not found"))
    def test_fallback_no_tcl(self, mock_run):
        variant = _probe_modules_version("/opt/modules/modulecmd", None)
        self.assertEqual(variant, ModulesVariant.MODULES_4X)
