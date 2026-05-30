"""
Unit tests for the in-tree YAML source-location loader (ivpm.yamlsrc).

These tests assert precise 1-based source spans, full scalar-type coverage
(no crash on bool/null/float), the ``.srcinfo`` attribute contract, and clean
syntax-error reporting -- without any network I/O.
"""
import io
import unittest

from ivpm.yamlsrc import load, SrcInfo, SrcLoaderError
from ivpm.yamlsrc.loader import _Str, _Int, _Float, _Dict, _List


# A small document with known line/column positions (1-based):
#
#  1: package:
#  2:   name: demo
#  3:   count: 3
#  4:   ratio: 1.5
#  5:   enabled: true
#  6:   missing: null
#  7:   deps:
#  8:     - name: a
#  9:       src: pypi
# 10:     - name: b
_DOC = (
    "package:\n"
    "  name: demo\n"
    "  count: 3\n"
    "  ratio: 1.5\n"
    "  enabled: true\n"
    "  missing: null\n"
    "  deps:\n"
    "    - name: a\n"
    "      src: pypi\n"
    "    - name: b\n"
)


def _load(text=_DOC, name="test.yaml"):
    return load(io.StringIO(text), name=name)


class TestSrcInfoLoader(unittest.TestCase):

    def test_top_level_mapping_span(self):
        data = _load()
        self.assertIsInstance(data, _Dict)
        si = data.srcinfo
        self.assertEqual(si.filename, "test.yaml")
        self.assertEqual(si.lineno, 1)
        self.assertEqual(si.linepos, 1)
        # span end is populated (exclusive)
        self.assertGreaterEqual(si.end_lineno, si.lineno)

    def test_str_value_has_srcinfo(self):
        pkg = _load()["package"]
        name = pkg["name"]
        self.assertIsInstance(name, _Str)
        self.assertEqual(name, "demo")
        self.assertEqual(name.srcinfo.lineno, 2)
        # column points at the value 'demo' (after 'name: ')
        self.assertEqual(name.srcinfo.linepos, 9)

    def test_int_value_has_srcinfo(self):
        pkg = _load()["package"]
        count = pkg["count"]
        self.assertIsInstance(count, _Int)
        self.assertEqual(count, 3)
        self.assertEqual(count.srcinfo.lineno, 3)

    def test_float_value_has_srcinfo(self):
        pkg = _load()["package"]
        ratio = pkg["ratio"]
        self.assertIsInstance(ratio, _Float)
        self.assertEqual(ratio, 1.5)
        self.assertEqual(ratio.srcinfo.lineno, 4)

    def test_bool_and_null_parse_without_crash(self):
        # Regression: the old loader raised "Unsupported element-type" for
        # non-int/str scalars. These must parse, even though the singleton
        # values cannot carry a .srcinfo attribute.
        pkg = _load()["package"]
        self.assertIs(pkg["enabled"], True)
        self.assertIsNone(pkg["missing"])
        # location is available via the enclosing mapping
        self.assertTrue(hasattr(pkg, "srcinfo"))

    def test_sequence_and_items_spans(self):
        pkg = _load()["package"]
        deps = pkg["deps"]
        self.assertIsInstance(deps, _List)
        self.assertEqual(len(deps), 2)
        self.assertEqual(deps.srcinfo.lineno, 8)
        # each dep is a mapping with its own distinct start line
        self.assertEqual(deps[0].srcinfo.lineno, 8)
        self.assertEqual(deps[1].srcinfo.lineno, 10)
        self.assertEqual(deps[0]["name"], "a")
        self.assertEqual(deps[1]["name"], "b")

    def test_str_subclass_behaves_like_str(self):
        name = _load()["package"]["name"]
        self.assertTrue(isinstance(name, str))
        self.assertEqual(name + "!", "demo!")
        d = {name: 1}
        self.assertEqual(d["demo"], 1)          # hashes/compares as str
        self.assertIn(name, {"demo"})

    def test_srcinfo_str_format(self):
        name = _load()["package"]["name"]
        self.assertEqual(str(name.srcinfo), "test.yaml:2:9")

    def test_excerpt_caret(self):
        name = _load()["package"]["name"]
        ex = name.srcinfo.excerpt()
        self.assertIsNotNone(ex)
        line, caret = ex.split("\n")
        self.assertEqual(line, "  name: demo")
        # caret sits under the value column (1-based linepos == 9 -> 8 spaces)
        self.assertEqual(caret, (" " * 8) + "^")

    def test_stringio_without_name_uses_supplied_name(self):
        s = io.StringIO("a: 1\n")
        data = load(s, name="explicit.yaml")
        self.assertEqual(data["a"].srcinfo.filename, "explicit.yaml")

    def test_syntax_error_raises_srcloadererror(self):
        bad = "package:\n  name: [unclosed\n"
        with self.assertRaises(SrcLoaderError) as ctx:
            load(io.StringIO(bad), name="bad.yaml")
        err = ctx.exception
        self.assertIsNotNone(err.srcinfo)
        self.assertEqual(err.srcinfo.filename, "bad.yaml")
        # message carries file:line:col
        self.assertIn("bad.yaml:", str(err))

    def test_empty_document(self):
        self.assertIsNone(load(io.StringIO(""), name="empty.yaml"))


class TestReaderRoundTrip(unittest.TestCase):
    """The .srcinfo contract survives the full IvpmYamlReader path."""

    _PROJ = (
        "package:\n"
        "  name: demo\n"
        "  dep-sets:\n"
        "    - name: default-dev\n"
        "      deps:\n"
        "        - name: a\n"
        "          src: pypi\n"
    )

    def test_dep_srcinfo_survives_reader(self):
        from ivpm.ivpm_yaml_reader import IvpmYamlReader
        pi = IvpmYamlReader().read(io.StringIO(self._PROJ), "proj.yaml")
        pkg = list(pi.dep_set_m["default-dev"].packages.values())[0]
        self.assertIsNotNone(pkg.srcinfo)
        self.assertEqual(pkg.srcinfo.filename, "proj.yaml")
        # the 'a' dependency mapping starts on line 6
        self.assertEqual(pkg.srcinfo.lineno, 6)

    def test_missing_name_reports_location(self):
        from ivpm.ivpm_yaml_reader import IvpmYamlReader
        bad = (
            "package:\n"
            "  name: demo\n"
            "  dep-sets:\n"
            "    - name: default-dev\n"
            "      deps:\n"
            "        - src: pypi\n"      # no 'name'
        )
        with self.assertRaises(Exception) as ctx:
            IvpmYamlReader().read(io.StringIO(bad), "proj.yaml")
        # message carries file:line:col (not <unknown> / -1)
        self.assertIn("proj.yaml:", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
