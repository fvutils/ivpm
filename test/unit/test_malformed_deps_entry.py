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


if __name__ == "__main__":
    unittest.main()
