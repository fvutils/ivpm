"""Tests for 'ivpm show' command and the self-description infrastructure."""
import dataclasses
import json
import subprocess
import sys
import unittest

from .test_base import TestBase

# Path to the Python interpreter with ivpm installed
import os
_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "packages", "python", "bin", "python3",
)
_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
_ENV = {**os.environ, "PYTHONPATH": _SRC}


def _run(*args, check=True):
    """Run ivpm with the given arguments; return (stdout, returncode)."""
    cmd = [_PYTHON, "-m", "ivpm"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, env=_ENV)
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command {cmd} failed (rc={result.returncode}):\n{result.stderr}"
        )
    return result.stdout, result.returncode


class TestShowSourceInfo(unittest.TestCase):
    """Unit tests for PkgSourceInfo / source_info() classmethods."""

    def test_all_sources_have_info(self):
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        rgy = PkgTypeRgy.inst()
        for name in rgy.getSrcTypes():
            info = rgy.getSourceInfo(name)
            self.assertIsNotNone(info, f"No info for source '{name}'")
            self.assertEqual(info.name, name)
            self.assertTrue(info.description, f"Source '{name}' has no description")

    def test_git_source_has_url_param(self):
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        info = PkgTypeRgy.inst().getSourceInfo("git")
        param_names = [p.name for p in info.params]
        self.assertIn("url", param_names)
        # url should be required
        url_param = next(p for p in info.params if p.name == "url")
        self.assertTrue(url_param.required)

    def test_pypi_source_has_version_param(self):
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        info = PkgTypeRgy.inst().getSourceInfo("pypi")
        param_names = [p.name for p in info.params]
        self.assertIn("version", param_names)

    def test_string_registration_backward_compat(self):
        """register() with a bare string should still work."""
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        from ivpm.show.info_types import PkgSourceInfo
        rgy = PkgTypeRgy()  # fresh instance (no _load)
        rgy.register("test-src", lambda n, o, s: None, "A test source")
        info = rgy.getSourceInfo("test-src")
        self.assertIsInstance(info, PkgSourceInfo)
        self.assertEqual(info.description, "A test source")
        self.assertEqual(info.origin, "built-in")

    def test_plugin_origin_recorded(self):
        """register() should record the origin when provided."""
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        rgy = PkgTypeRgy()
        rgy.register("ext-src", lambda n, o, s: None, "External source", origin="my.plugin")
        info = rgy.getSourceInfo("ext-src")
        self.assertEqual(info.origin, "my.plugin")

    def test_get_all_source_info(self):
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        rgy = PkgTypeRgy.inst()
        infos = rgy.getAllSourceInfo()
        names = [i.name for i in infos]
        for expected in ("git", "pypi", "gh-rls", "http", "dir", "file", "url"):
            self.assertIn(expected, names)


class TestShowContentTypeInfo(unittest.TestCase):
    """Unit tests for ContentTypeInfo / content_type_info()."""

    def test_python_type_info(self):
        from ivpm.pkg_content_type import PythonContentType
        info = PythonContentType().content_type_info()
        self.assertEqual(info.name, "python")
        self.assertTrue(info.description)
        param_names = [p.name for p in info.params]
        self.assertIn("extras", param_names)
        self.assertIn("editable", param_names)

    def test_raw_type_info(self):
        from ivpm.pkg_content_type import RawContentType
        info = RawContentType().content_type_info()
        self.assertEqual(info.name, "raw")
        self.assertTrue(info.description)
        self.assertEqual(info.params, [])

    def test_all_types_have_info(self):
        from ivpm.pkg_content_type_rgy import PkgContentTypeRgy
        rgy = PkgContentTypeRgy.inst()
        for name in rgy.names():
            info = rgy.get(name).content_type_info()
            self.assertEqual(info.name, name)
            self.assertTrue(info.description)

    def test_json_schema_consistent(self):
        """get_json_schema() should list the same params as content_type_info()."""
        from ivpm.pkg_content_type import PythonContentType
        ct = PythonContentType()
        info = ct.content_type_info()
        schema = ct.get_json_schema()
        schema_keys = set(schema.get("properties", {}).keys())
        info_keys = {p.name for p in info.params}
        self.assertEqual(schema_keys, info_keys)


class TestShowHandlerInfo(unittest.TestCase):
    """Unit tests for HandlerInfo / handler_info()."""

    def test_python_handler_info(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        info = PackageHandlerPython.handler_info()
        self.assertEqual(info.name, "python")
        self.assertTrue(info.description)
        self.assertTrue(info.conditions)
        self.assertTrue(info.cli_options)

    def test_direnv_handler_info(self):
        from ivpm.handlers.package_handler_direnv import PackageHandlerDirenv
        info = PackageHandlerDirenv.handler_info()
        self.assertEqual(info.name, "direnv")
        self.assertTrue(info.description)

    def test_skills_handler_info(self):
        from ivpm.handlers.package_handler_skills import PackageHandlerSkills
        info = PackageHandlerSkills.handler_info()
        self.assertEqual(info.name, "skills")
        self.assertTrue(info.description)


class TestShowCLI(unittest.TestCase):
    """CLI integration tests for 'ivpm show'."""

    def test_show_all_no_rich(self):
        out, rc = _run("show", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("Package Sources", out)
        self.assertIn("Content Types", out)
        self.assertIn("Handlers", out)

    def test_show_source_list_no_rich(self):
        out, rc = _run("show", "source", "--no-rich")
        self.assertEqual(rc, 0)
        for name in ("git", "pypi", "gh-rls", "http", "dir", "url"):
            self.assertIn(name, out)

    def test_show_src_alias(self):
        """'src' is accepted as an alias for 'source'."""
        out, rc = _run("show", "src", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("git", out)

    def test_show_source_detail_no_rich(self):
        out, rc = _run("show", "source", "git", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("url", out)
        self.assertIn("branch", out)
        self.assertIn("cache", out)

    def test_show_source_unknown_exits_1(self):
        out, rc = _run("show", "source", "does-not-exist", check=False)
        self.assertEqual(rc, 1)

    def test_show_type_list_no_rich(self):
        out, rc = _run("show", "type", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("python", out)
        self.assertIn("raw", out)

    def test_show_type_detail_no_rich(self):
        out, rc = _run("show", "type", "python", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("extras", out)
        self.assertIn("editable", out)

    def test_show_type_unknown_exits_1(self):
        out, rc = _run("show", "type", "does-not-exist", check=False)
        self.assertEqual(rc, 1)

    def test_show_handler_list_no_rich(self):
        out, rc = _run("show", "handler", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("python", out)
        self.assertIn("direnv", out)
        self.assertIn("skills", out)

    def test_show_handler_detail_no_rich(self):
        out, rc = _run("show", "handler", "python", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("python", out)
        self.assertIn("Phase", out)

    def test_show_handler_unknown_exits_1(self):
        out, rc = _run("show", "handler", "does-not-exist", check=False)
        self.assertEqual(rc, 1)

    def test_show_source_json(self):
        out, rc = _run("show", "source", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)
        names = [d["name"] for d in data]
        for expected in ("git", "pypi", "gh-rls"):
            self.assertIn(expected, names)
        # Each entry must have required fields
        for entry in data:
            self.assertIn("name", entry)
            self.assertIn("description", entry)
            self.assertIn("params", entry)
            self.assertIn("origin", entry)

    def test_show_source_detail_json(self):
        out, rc = _run("show", "source", "git", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["name"], "git")
        param_names = [p["name"] for p in data["params"]]
        self.assertIn("url", param_names)

    def test_show_type_json(self):
        out, rc = _run("show", "type", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)
        names = [d["name"] for d in data]
        self.assertIn("python", names)

    def test_show_handler_json(self):
        out, rc = _run("show", "handler", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)

    def test_show_all_json(self):
        out, rc = _run("show", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("sources", data)
        self.assertIn("types", data)
        self.assertIn("handlers", data)

    def test_show_schema(self):
        out, rc = _run("show", "--schema")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("$schema", data)
        self.assertIn("x-ivpm-sources", data)
        self.assertIn("x-ivpm-content-types", data)
        # Validate that all source names appear in the schema
        src_names = list(data["x-ivpm-sources"].keys())
        for expected in ("git", "pypi", "gh-rls"):
            self.assertIn(expected, src_names)
