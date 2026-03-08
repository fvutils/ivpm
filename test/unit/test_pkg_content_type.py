"""
Unit tests for pkg_content_type.py and PkgContentTypeRgy.

These tests exercise YAML parsing → type_data population and validation
without performing any network I/O or package installation.
"""
import io
import unittest

from .test_base import TestBase
from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.pkg_content_type import (
    PythonTypeData, RawTypeData,
    PythonContentType, RawContentType,
)
from ivpm.pkg_content_type_rgy import PkgContentTypeRgy


def _read_yaml(yaml_text, name="test.yaml"):
    """Parse an ivpm.yaml string and return ProjInfo."""
    return IvpmYamlReader().read(io.StringIO(yaml_text), name)


def _first_pkg(proj_info):
    """Return the first package from the default-dev dep-set."""
    return list(proj_info.dep_set_m["default-dev"].packages.values())[0]


_HEADER = """
package:
    name: test_proj
    dep-sets:
        - name: default-dev
          deps:
"""


class TestPkgContentTypeRegistry(unittest.TestCase):
    """Tests for PkgContentTypeRgy singleton."""

    def test_builtin_types_registered(self):
        rgy = PkgContentTypeRgy.inst()
        self.assertTrue(rgy.has("python"))
        self.assertTrue(rgy.has("raw"))

    def test_names_sorted(self):
        rgy = PkgContentTypeRgy.inst()
        names = rgy.names()
        self.assertEqual(names, sorted(names))

    def test_duplicate_registration_raises(self):
        rgy = PkgContentTypeRgy()
        rgy.register(PythonContentType())
        with self.assertRaises(Exception):
            rgy.register(PythonContentType())

    def test_get_returns_correct_type(self):
        rgy = PkgContentTypeRgy.inst()
        self.assertIsInstance(rgy.get("python"), PythonContentType)
        self.assertIsInstance(rgy.get("raw"), RawContentType)


class TestPythonContentType(unittest.TestCase):
    """Tests for PythonContentType.create_data() directly."""

    def setUp(self):
        self.ct = PythonContentType()

    def test_empty_opts_returns_defaults(self):
        data = self.ct.create_data({}, si=None)
        self.assertIsInstance(data, PythonTypeData)
        self.assertIsNone(data.extras)
        self.assertIsNone(data.editable)

    def test_editable_false(self):
        data = self.ct.create_data({"editable": False}, si=None)
        self.assertFalse(data.editable)

    def test_editable_true(self):
        data = self.ct.create_data({"editable": True}, si=None)
        self.assertTrue(data.editable)

    def test_extras_list(self):
        data = self.ct.create_data({"extras": ["tests", "docs"]}, si=None)
        self.assertEqual(data.extras, ["tests", "docs"])

    def test_extras_single_string(self):
        data = self.ct.create_data({"extras": "litellm"}, si=None)
        self.assertEqual(data.extras, ["litellm"])

    def test_unknown_key_raises(self):
        with self.assertRaises(Exception) as ctx:
            self.ct.create_data({"unknown_key": "value"}, si=None)
        self.assertIn("unknown_key", str(ctx.exception).lower())

    def test_get_json_schema_has_extras_and_editable(self):
        schema = self.ct.get_json_schema()
        self.assertIn("extras", schema["properties"])
        self.assertIn("editable", schema["properties"])
        self.assertFalse(schema.get("additionalProperties", True))


class TestRawContentType(unittest.TestCase):
    """Tests for RawContentType.create_data() directly."""

    def setUp(self):
        self.ct = RawContentType()

    def test_empty_opts_ok(self):
        data = self.ct.create_data({}, si=None)
        self.assertIsInstance(data, RawTypeData)

    def test_any_opts_raises(self):
        with self.assertRaises(Exception):
            self.ct.create_data({"something": "value"}, si=None)


class TestYamlTypeData(TestBase):
    """Tests that IvpmYamlReader populates type_data correctly from YAML."""

    def test_type_python_no_with(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type: python
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(len(pkg.type_data), 1)
        self.assertIsInstance(pkg.type_data[0], PythonTypeData)
        self.assertIsNone(pkg.type_data[0].extras)
        self.assertIsNone(pkg.type_data[0].editable)

    def test_type_python_editable_false(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type:
                python:
                  editable: false
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(len(pkg.type_data), 1)
        self.assertIsInstance(pkg.type_data[0], PythonTypeData)
        self.assertFalse(pkg.type_data[0].editable)

    def test_type_python_editable_true(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type:
                python:
                  editable: true
        """)
        pkg = _first_pkg(proj)
        self.assertTrue(pkg.type_data[0].editable)

    def test_type_python_extras_list(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type:
                python:
                  extras: [tests, docs]
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(pkg.type_data[0].extras, ["tests", "docs"])

    def test_type_python_extras_and_editable(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type:
                python:
                  extras: [litellm]
                  editable: false
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(pkg.type_data[0].extras, ["litellm"])
        self.assertFalse(pkg.type_data[0].editable)

    def test_type_raw_no_with(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/data.tar.gz
              type: raw
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(len(pkg.type_data), 1)
        self.assertIsInstance(pkg.type_data[0], RawTypeData)

    def test_no_type_no_type_data(self):
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(pkg.type_data, [])

    def test_with_without_type_raises(self):
        with self.assertRaises(Exception) as ctx:
            _read_yaml(_HEADER + """
                - name: mypkg
                  url: https://example.com/mypkg.git
                  with:
                    editable: false
            """)
        self.assertIn("with", str(ctx.exception).lower())

    def test_unknown_type_raises(self):
        with self.assertRaises(Exception) as ctx:
            _read_yaml(_HEADER + """
                - name: mypkg
                  url: https://example.com/mypkg.git
                  type: not_a_real_type
            """)
        self.assertIn("not_a_real_type", str(ctx.exception))

    def test_python_unknown_with_key_raises(self):
        with self.assertRaises(Exception) as ctx:
            _read_yaml(_HEADER + """
                - name: mypkg
                  url: https://example.com/mypkg.git
                  type:
                    python:
                      bogus_param: true
            """)
        self.assertIn("bogus_param", str(ctx.exception))

    def test_raw_with_params_raises(self):
        with self.assertRaises(Exception):
            _read_yaml(_HEADER + """
                - name: mypkg
                  url: https://example.com/data.tar.gz
                  type:
                    raw:
                      something: value
            """)

    def test_backward_compat_pkg_type_set(self):
        """pkg_type enum/string should still be set for backward compatibility."""
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type: python
        """)
        pkg = _first_pkg(proj)
        # pkg_type should mirror the type: field for backward compat
        self.assertIsNotNone(pkg.pkg_type)

    def test_type_list_multiple_types(self):
        """A list of type strings produces multiple TypeData entries."""
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type:
                - python
                - raw
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(len(pkg.type_data), 2)
        self.assertIsInstance(pkg.type_data[0], PythonTypeData)
        self.assertIsInstance(pkg.type_data[1], RawTypeData)

    def test_type_list_mixed_string_and_dict(self):
        """A list mixing plain strings and dicts is parsed correctly."""
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type:
                - python:
                    editable: false
                - raw
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(len(pkg.type_data), 2)
        self.assertFalse(pkg.type_data[0].editable)
        self.assertIsInstance(pkg.type_data[1], RawTypeData)

    def test_type_name_recorded_on_type_data(self):
        """TypeData.type_name is set to the type string that produced it."""
        proj = _read_yaml(_HEADER + """
            - name: mypkg
              url: https://example.com/mypkg.git
              type: python
        """)
        pkg = _first_pkg(proj)
        self.assertEqual(pkg.type_data[0].type_name, "python")

    def test_package_root_type_string(self):
        """'type:' at the package root is parsed into ProjInfo.self_types."""
        proj = _read_yaml("""
package:
    name: my_lib
    type: python
    dep-sets:
        - name: default-dev
          deps: []
        """)
        self.assertEqual(proj.self_types, [("python", {})])

    def test_package_root_type_dict(self):
        """'type:' dict form at the package root is parsed correctly."""
        proj = _read_yaml("""
package:
    name: my_lib
    type:
        python:
            editable: false
    dep-sets:
        - name: default-dev
          deps: []
        """)
        self.assertEqual(proj.self_types, [("python", {"editable": False})])

    def test_package_root_type_list(self):
        """'type:' list form at the package root is parsed correctly."""
        proj = _read_yaml("""
package:
    name: my_lib
    type:
        - python
        - raw
    dep-sets:
        - name: default-dev
          deps: []
        """)
        self.assertEqual(proj.self_types, [("python", {}), ("raw", {})])
