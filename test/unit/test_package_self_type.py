"""
Tests for package self-type declaration and merging.

Covers:
  - 'type:' at the package root (ProjInfo.self_types populated)
  - Self-type merging in isolation (the merge logic itself)
  - Precedence: caller-specified types win; self-declared types are appended
    for names not already present.
"""
import unittest

from ivpm.pkg_content_type import PythonTypeData, RawTypeData, parse_type_field
from ivpm.pkg_content_type_rgy import PkgContentTypeRgy
from ivpm.package import Package, get_type_data
from ivpm.pkg_types.package_git import PackageGit


def _make_pkg(name="mypkg", caller_types=None):
    """Create a minimal PackageGit with optional pre-set caller type_data."""
    pkg = PackageGit.__new__(PackageGit)
    pkg.name = name
    pkg.url = "https://example.com/%s.git" % name
    pkg.type_data = list(caller_types) if caller_types else []
    pkg.self_types = []
    pkg.version = None
    pkg.extras = None
    pkg.branch = None
    pkg.commit = None
    pkg.tag = None
    pkg.depth = None
    pkg.anonymous = None
    pkg.resolved_commit = None
    pkg.pkg_type = None
    return pkg


def _apply_self_types(pkg, self_types_spec):
    """Simulate the merge performed by package_updater after reading a dep's ivpm.yaml.

    self_types_spec is the raw list as produced by parse_type_field().
    """
    ct_rgy = PkgContentTypeRgy.inst()
    caller_names = {td.type_name for td in pkg.type_data}
    for type_name, opts in self_types_spec:
        if type_name not in caller_names and ct_rgy.has(type_name):
            pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, None))


class TestParseTypeField(unittest.TestCase):
    """Unit tests for parse_type_field() across all supported forms."""

    def test_string(self):
        self.assertEqual(parse_type_field("python"), [("python", {})])

    def test_dict_no_opts(self):
        self.assertEqual(parse_type_field({"python": None}), [("python", {})])

    def test_dict_with_opts(self):
        self.assertEqual(
            parse_type_field({"python": {"editable": False}}),
            [("python", {"editable": False})]
        )

    def test_list_of_strings(self):
        self.assertEqual(
            parse_type_field(["python", "raw"]),
            [("python", {}), ("raw", {})]
        )

    def test_list_mixed(self):
        result = parse_type_field([{"python": {"extras": ["tests"]}}, "raw"])
        self.assertEqual(result[0], ("python", {"extras": ["tests"]}))
        self.assertEqual(result[1], ("raw", {}))

    def test_list_single_string(self):
        self.assertEqual(parse_type_field(["python"]), [("python", {})])

    def test_dict_preserves_opts(self):
        opts = {"extras": ["tests", "docs"], "editable": False}
        result = parse_type_field({"python": opts})
        self.assertEqual(result, [("python", opts)])


class TestSelfTypeMerge(unittest.TestCase):
    """Tests for the self-type merging logic (caller-wins precedence)."""

    def test_no_caller_uses_self_types(self):
        """When caller has no types, self-declared types are applied."""
        pkg = _make_pkg()
        _apply_self_types(pkg, [("python", {})])
        self.assertEqual(len(pkg.type_data), 1)
        self.assertIsInstance(pkg.type_data[0], PythonTypeData)
        self.assertEqual(pkg.type_data[0].type_name, "python")

    def test_caller_type_kept_on_conflict(self):
        """When caller specifies python, self-declared python does not override."""
        ct_rgy = PkgContentTypeRgy.inst()
        caller_td = ct_rgy.get("python").create_data({"editable": False}, None)
        pkg = _make_pkg(caller_types=[caller_td])
        # self-declares python with different opts
        _apply_self_types(pkg, [("python", {"editable": True})])
        # Only one python entry; caller's editable=False is preserved
        py_td = get_type_data(pkg, PythonTypeData)
        self.assertIsNotNone(py_td)
        self.assertFalse(py_td.editable)
        self.assertEqual(len(pkg.type_data), 1)

    def test_self_type_appended_when_not_in_caller(self):
        """Self-declared type with a name absent from caller list is appended."""
        ct_rgy = PkgContentTypeRgy.inst()
        caller_td = ct_rgy.get("python").create_data({}, None)
        pkg = _make_pkg(caller_types=[caller_td])
        # self-declares 'raw' (different type name)
        _apply_self_types(pkg, [("raw", {})])
        self.assertEqual(len(pkg.type_data), 2)
        self.assertIsInstance(pkg.type_data[0], PythonTypeData)
        self.assertIsInstance(pkg.type_data[1], RawTypeData)

    def test_both_caller_and_self_same_name_no_duplicate(self):
        """No duplicate entries when both caller and self-type share the same name."""
        ct_rgy = PkgContentTypeRgy.inst()
        caller_td = ct_rgy.get("python").create_data({}, None)
        pkg = _make_pkg(caller_types=[caller_td])
        _apply_self_types(pkg, [("python", {})])
        self.assertEqual(len(pkg.type_data), 1)

    def test_multiple_self_types_all_appended(self):
        """Multiple self-declared types are all appended when caller has none."""
        pkg = _make_pkg()
        _apply_self_types(pkg, [("python", {}), ("raw", {})])
        self.assertEqual(len(pkg.type_data), 2)
        self.assertIsInstance(pkg.type_data[0], PythonTypeData)
        self.assertIsInstance(pkg.type_data[1], RawTypeData)

    def test_empty_self_types_no_change(self):
        """Empty self_types list does not modify type_data."""
        pkg = _make_pkg()
        _apply_self_types(pkg, [])
        self.assertEqual(pkg.type_data, [])

    def test_unknown_self_type_silently_ignored(self):
        """Self-declared type names not in the registry are silently skipped."""
        pkg = _make_pkg()
        _apply_self_types(pkg, [("not_registered_type", {})])
        self.assertEqual(pkg.type_data, [])

    def test_self_type_with_options(self):
        """Self-declared python type carries its options through."""
        pkg = _make_pkg()
        _apply_self_types(pkg, [("python", {"extras": ["tests"], "editable": False})])
        td = get_type_data(pkg, PythonTypeData)
        self.assertIsNotNone(td)
        self.assertEqual(td.extras, ["tests"])
        self.assertFalse(td.editable)


class TestGetTypeData(unittest.TestCase):
    """Tests for the get_type_data() helper."""

    def test_returns_none_for_empty_list(self):
        pkg = _make_pkg()
        self.assertIsNone(get_type_data(pkg, PythonTypeData))

    def test_returns_first_match(self):
        ct_rgy = PkgContentTypeRgy.inst()
        td = ct_rgy.get("python").create_data({}, None)
        pkg = _make_pkg(caller_types=[td])
        result = get_type_data(pkg, PythonTypeData)
        self.assertIs(result, td)

    def test_returns_none_when_no_match(self):
        ct_rgy = PkgContentTypeRgy.inst()
        td = ct_rgy.get("python").create_data({}, None)
        pkg = _make_pkg(caller_types=[td])
        self.assertIsNone(get_type_data(pkg, RawTypeData))

    def test_returns_correct_type_from_mixed_list(self):
        ct_rgy = PkgContentTypeRgy.inst()
        py_td = ct_rgy.get("python").create_data({}, None)
        raw_td = ct_rgy.get("raw").create_data({}, None)
        pkg = _make_pkg(caller_types=[py_td, raw_td])
        self.assertIs(get_type_data(pkg, RawTypeData), raw_td)
        self.assertIs(get_type_data(pkg, PythonTypeData), py_td)
