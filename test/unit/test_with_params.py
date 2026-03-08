"""
Tests for 'with:' parameters affecting requirements.txt output.

Tests call PackageHandlerPython._write_requirements_txt() directly with
constructed Package objects, verifying editable/non-editable and extras
entries without requiring network I/O or venv creation.
"""
import io
import os
import tempfile
import unittest
import dataclasses as dc

from .test_base import TestBase
from ivpm.pkg_content_type import PythonTypeData, RawTypeData
from ivpm.handlers.package_handler_python import PackageHandlerPython
from ivpm.pkg_types.package_git import PackageGit
from ivpm.pkg_types.package_pypi import PackagePyPi


def _make_src_pkg(name, type_data=None):
    """Create a PackageGit with a url (src package) and optional type_data."""
    pkg = PackageGit.__new__(PackageGit)
    pkg.name = name
    pkg.url = "https://example.com/%s.git" % name
    pkg.type_data = type_data
    pkg.version = None
    pkg.extras = None
    pkg.branch = None
    pkg.commit = None
    pkg.tag = None
    pkg.depth = None
    pkg.anonymous = None
    pkg.resolved_commit = None
    pkg.pkg_type = "python"
    return pkg


def _make_pypi_pkg(name, version=None, extras=None, type_data=None):
    """Create a PackagePyPi with optional version, extras, and type_data."""
    pkg = PackagePyPi.__new__(PackagePyPi)
    pkg.name = name
    pkg.version = version
    pkg.extras = extras
    pkg.type_data = type_data
    pkg.pkg_type = "python"
    return pkg


def _write(pkgs, packages_dir="/fake/packages"):
    """Run _write_requirements_txt and return the file content as lines."""
    handler = PackageHandlerPython.__new__(PackageHandlerPython)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        fname = f.name
    try:
        handler._write_requirements_txt(packages_dir, pkgs, fname)
        with open(fname) as f:
            return [line.rstrip() for line in f if line.strip()]
    finally:
        os.unlink(fname)


class TestWriteRequirementsTxt(unittest.TestCase):
    """Direct tests of PackageHandlerPython._write_requirements_txt()."""

    # --- Source package (has url) ---

    def test_src_default_is_editable(self):
        """type: python with no type_data → editable (-e) by default."""
        pkg = _make_src_pkg("mypkg", type_data=None)
        lines = _write([pkg])
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("-e "), lines)
        self.assertIn("mypkg", lines[0])

    def test_src_with_python_type_data_editable_none(self):
        """PythonTypeData(editable=None) → editable (None means use default)."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData())
        lines = _write([pkg])
        self.assertTrue(lines[0].startswith("-e "), lines)

    def test_src_editable_false(self):
        """PythonTypeData(editable=False) → non-editable entry."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData(editable=False))
        lines = _write([pkg])
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0].startswith("-e "), lines)
        self.assertIn("mypkg", lines[0])

    def test_src_editable_true_explicit(self):
        """PythonTypeData(editable=True) → editable entry."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData(editable=True))
        lines = _write([pkg])
        self.assertTrue(lines[0].startswith("-e "), lines)

    def test_src_extras(self):
        """PythonTypeData(extras=[tests, docs]) → [tests,docs] in entry."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData(extras=["tests", "docs"]))
        lines = _write([pkg])
        self.assertIn("mypkg[tests,docs]", lines[0])

    def test_src_extras_editable(self):
        """Editable src package with extras → -e path[extras]."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData(extras=["litellm"], editable=True))
        lines = _write([pkg])
        self.assertTrue(lines[0].startswith("-e "), lines)
        self.assertIn("mypkg[litellm]", lines[0])

    def test_src_extras_noneditable(self):
        """Non-editable src package with extras → path[extras] (no -e)."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData(extras=["tests"], editable=False))
        lines = _write([pkg])
        self.assertFalse(lines[0].startswith("-e "), lines)
        self.assertIn("mypkg[tests]", lines[0])

    def test_src_no_extras_no_brackets(self):
        """No extras → no brackets in requirements entry."""
        pkg = _make_src_pkg("mypkg", type_data=PythonTypeData())
        lines = _write([pkg])
        self.assertNotIn("[", lines[0])

    # --- PyPI package (no url) ---

    def test_pypi_bare(self):
        """PyPI package with no version or extras → just name."""
        pkg = _make_pypi_pkg("requests")
        lines = _write([pkg])
        self.assertEqual(lines, ["requests"])

    def test_pypi_version_exact(self):
        """PyPI package with plain version → name==version."""
        pkg = _make_pypi_pkg("requests", version="2.31.0")
        lines = _write([pkg])
        self.assertEqual(lines, ["requests==2.31.0"])

    def test_pypi_version_constraint(self):
        """PyPI package with >= version → name>=version."""
        pkg = _make_pypi_pkg("requests", version=">=2.0")
        lines = _write([pkg])
        self.assertEqual(lines, ["requests>=2.0"])

    def test_pypi_extras_from_pkg(self):
        """PyPI package with pkg.extras (legacy path) → name[extras]."""
        pkg = _make_pypi_pkg("langchain", extras=["litellm"])
        lines = _write([pkg])
        self.assertEqual(lines, ["langchain[litellm]"])

    def test_pypi_extras_from_type_data_takes_priority(self):
        """PythonTypeData.extras overrides pkg.extras for PyPI packages."""
        pkg = _make_pypi_pkg("langchain", extras=["old"], type_data=PythonTypeData(extras=["new"]))
        lines = _write([pkg])
        self.assertIn("[new]", lines[0])
        self.assertNotIn("[old]", lines[0])

    def test_pypi_extras_and_version(self):
        """PyPI package with extras and version → name[extras]==version."""
        pkg = _make_pypi_pkg("langchain", version="0.1.0", extras=["litellm"])
        lines = _write([pkg])
        self.assertEqual(lines, ["langchain[litellm]==0.1.0"])

    # --- Multiple packages ---

    def test_multiple_packages(self):
        """Multiple packages → one entry per line."""
        pkgs = [
            _make_src_pkg("pkga", type_data=PythonTypeData(editable=False)),
            _make_src_pkg("pkgb", type_data=PythonTypeData(editable=True)),
            _make_pypi_pkg("requests", version="2.31.0"),
        ]
        lines = _write(pkgs)
        self.assertEqual(len(lines), 3)
        self.assertFalse(lines[0].startswith("-e "))   # pkga non-editable
        self.assertTrue(lines[1].startswith("-e "))    # pkgb editable
        self.assertEqual(lines[2], "requests==2.31.0")
