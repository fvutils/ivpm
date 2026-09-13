"""
Tests for malformed entries inside a dep-set's 'deps' list.

An empty YAML list item (``- `` with nothing after it) parses to ``None``.
``IvpmYamlReader.read_deps()`` dereferences ``d.srcinfo`` before checking that
``d`` is a mapping, so such a file raises a raw ``AttributeError`` out of the
reader instead of a located ``fatal()`` diagnostic.
"""
import io
import unittest

from ivpm import msg
from ivpm.diagnostics import (
    CollectingSink,
    DiagnosticReporter,
    Severity,
    SrcLoaderError,
)
from ivpm.ivpm_yaml_reader import IvpmYamlReader


# Reduced from a real sphinx-dv-flow ivpm.yaml: 'use' has a 'deps' list whose
# sole entry is empty.
_NULL_DEP_ENTRY = """\
package:
  name: sphinx-dv-flow
  dep-sets:
  - name: use
    deps:
    -
"""

# A scalar where a mapping is expected.
_SCALAR_DEP_ENTRY = """\
package:
  name: sphinx-dv-flow
  dep-sets:
  - name: use
    deps:
    - dv-flow-mgr
"""

# 'deps' key present but with no value at all -> None, not a list.
_NULL_DEPS_LIST = """\
package:
  name: sphinx-dv-flow
  dep-sets:
  - name: use
    deps:
"""


def _parse(yaml_text, name="test.yaml"):
    return IvpmYamlReader().read(io.StringIO(yaml_text), name)


class TestMalformedDepsEntry(unittest.TestCase):

    def setUp(self):
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)

    def _fatals(self):
        return [d for d in self._sink.records if d.severity == Severity.FATAL]

    def test_null_dep_entry_reports_diagnostic(self):
        """An empty list item under 'deps' must produce a fatal diagnostic,
        not an AttributeError."""
        with self.assertRaises(SrcLoaderError):
            _parse(_NULL_DEP_ENTRY, "null_entry.yaml")
        self.assertEqual(len(self._fatals()), 1,
            "Expected 1 FATAL but got: %s" % [d.format() for d in self._sink.records])

    def test_null_dep_entry_diagnostic_is_located(self):
        """The diagnostic must point at the offending line of the file."""
        with self.assertRaises(SrcLoaderError):
            _parse(_NULL_DEP_ENTRY, "null_entry.yaml")
        diag = self._fatals()[0]
        self.assertIsNotNone(diag.srcinfo,
            "Diagnostic for a malformed dep entry must carry srcinfo")
        self.assertEqual(diag.srcinfo.filename, "null_entry.yaml")

    def test_scalar_dep_entry_reports_diagnostic(self):
        """A bare string under 'deps' must produce a fatal diagnostic."""
        with self.assertRaises(SrcLoaderError):
            _parse(_SCALAR_DEP_ENTRY, "scalar_entry.yaml")
        self.assertEqual(len(self._fatals()), 1,
            "Expected 1 FATAL but got: %s" % [d.format() for d in self._sink.records])

    def test_null_deps_list_is_located(self):
        """'deps:' with no value currently reports 'deps is not a list' with
        no location, because the None value carries no srcinfo."""
        with self.assertRaises(SrcLoaderError):
            _parse(_NULL_DEPS_LIST, "null_deps.yaml")
        diag = self._fatals()[0]
        self.assertIsNotNone(diag.srcinfo,
            "Diagnostic for a null 'deps' value must carry srcinfo")


_BAD_NAME = """\
package:
  name: root
  dep-sets:
  - name: use
    deps:
    - name: ../../etc
      url: https://example.com/x.tar.gz
"""

_DOTDOT_NAME = """\
package:
  name: root
  dep-sets:
  - name: use
    deps:
    - name: ".."
      url: https://example.com/x.tar.gz
"""

_ORDINARY_NAME = """\
package:
  name: root
  dep-sets:
  - name: use
    deps:
    - name: my.pkg_v1+2-final
      url: https://example.com/x.tar.gz
"""


class TestPackageNameValidation(unittest.TestCase):
    """K3: a dependency name becomes a directory in deps/ and in the shared
    cache, and nothing checked it on the way there."""

    def setUp(self):
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)

    def _fatals(self):
        return [d for d in self._sink.records if d.severity == Severity.FATAL]

    def test_a_traversing_name_is_rejected_with_a_location(self):
        with self.assertRaises(SrcLoaderError):
            _parse(_BAD_NAME, "bad_name.yaml")
        diag = self._fatals()[0]
        self.assertIn("Invalid package name", diag.message)
        self.assertIsNotNone(diag.srcinfo)
        self.assertEqual(diag.srcinfo.filename, "bad_name.yaml")

    def test_dotdot_is_rejected(self):
        # The quiet one: '..' names a directory that already exists, so every
        # operation downstream succeeds against the wrong tree.
        with self.assertRaises(SrcLoaderError):
            _parse(_DOTDOT_NAME, "dotdot.yaml")
        self.assertIn("Invalid package name", self._fatals()[0].message)

    def test_an_ordinary_name_still_parses(self):
        info = _parse(_ORDINARY_NAME, "ok.yaml")
        self.assertIn("my.pkg_v1+2-final", info.get_dep_set("use").packages)
        self.assertEqual(self._fatals(), [])


if __name__ == "__main__":
    unittest.main()
