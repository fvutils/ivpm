"""Tests for ivpm.yaml validation: unknown tags at package level and in with: sections."""
import io
import unittest
from .test_base import TestBase
from ivpm.ivpm_yaml_reader import IvpmYamlReader


def _parse(yaml_text, name="test.yaml"):
    """Helper: parse *yaml_text* via IvpmYamlReader and return ProjInfo."""
    fp = io.StringIO(yaml_text)
    return IvpmYamlReader().read(fp, name)


def _assert_fatal(test_case, yaml_text, *fragments):
    """Assert that parsing *yaml_text* raises an Exception whose message
    contains all of the given *fragments*."""
    with test_case.assertRaises(Exception) as ctx:
        _parse(yaml_text)
    msg = str(ctx.exception)
    for frag in fragments:
        test_case.assertIn(frag, msg,
            "Expected %r in error message: %r" % (frag, msg))


_MINIMAL_VALID = """
package:
  name: mypkg
  dep-sets:
    - name: default
      deps: []
"""


class TestPackageLevelValidation(unittest.TestCase):

    def test_valid_yaml_parses_ok(self):
        """A well-formed ivpm.yaml should parse without error."""
        info = _parse(_MINIMAL_VALID)
        self.assertEqual(info.name, "mypkg")

    def test_unknown_tag_raises(self):
        """An unknown key at the package level must raise an error."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  dep-dir: packages
  dep-sets:
    - name: default
      deps: []
""",
            "dep-dir",          # unknown key is mentioned
            "package level",    # context
        )

    def test_unknown_tag_lists_valid_tags(self):
        """Error message must list all valid package-level tags."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  junk: value
  dep-sets:
    - name: default
      deps: []
""",
            "Valid tags",
            "deps-dir",   # one of the valid tags that should appear in the list
        )

    def test_close_match_suggested(self):
        """When the unknown key is close to a valid one, a suggestion is shown."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  dep-dir: packages
  dep-sets:
    - name: default
      deps: []
""",
            "deps-dir",   # close match to 'dep-dir'
        )

    def test_no_false_positives_for_known_tags(self):
        """Every documented valid key must be accepted without error."""
        valid_yaml = """
package:
  name: mypkg
  version: "1.0"
  deps-dir: packages
  default-dep-set: default
  dep-sets:
    - name: default
      deps: []
  setup-deps: []
  paths: {}
"""
        info = _parse(valid_yaml)
        self.assertEqual(info.name, "mypkg")


class TestWithSectionValidation(unittest.TestCase):

    def test_unknown_with_handler_raises(self):
        """An unrecognised handler key under package.with must raise."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  with:
    ruby: {}
  dep-sets:
    - name: default
      deps: []
""",
            "ruby",
            "package.with",
        )

    def test_unknown_with_handler_lists_valid_keys(self):
        """Error for unknown with key must list valid keys."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  with:
    ruby: {}
  dep-sets:
    - name: default
      deps: []
""",
            "python",   # the one valid handler
        )

    def test_close_match_for_with_handler(self):
        """A typo in the with handler name should trigger a close-match suggestion."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  with:
    pythoon: {}
  dep-sets:
    - name: default
      deps: []
""",
            "python",   # close match
        )

    def test_unknown_python_with_key_raises(self):
        """An unknown key under package.with.python must raise."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  with:
    python:
      enable-venv: true
  dep-sets:
    - name: default
      deps: []
""",
            "enable-venv",
            "package.with.python",
        )

    def test_unknown_python_with_key_lists_valid_keys(self):
        """Error for unknown python with key must include all valid keys."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  with:
    python:
      junk: value
  dep-sets:
    - name: default
      deps: []
""",
            "venv",
            "system-site-packages",
            "pre-release",
        )

    def test_close_match_for_python_with_key(self):
        """A typo in a python with key should trigger a close-match suggestion."""
        _assert_fatal(self,
            """
package:
  name: mypkg
  with:
    python:
      pre-reelase: true
  dep-sets:
    - name: default
      deps: []
""",
            "pre-release",   # close match to 'pre-reelase'
        )

    def test_valid_python_with_keys_accepted(self):
        """All documented python with keys must be accepted."""
        info = _parse("""
package:
  name: mypkg
  with:
    python:
      venv: true
      system-site-packages: false
      pre-release: false
  dep-sets:
    - name: default
      deps: []
""")
        self.assertIsNotNone(info.python_config)


if __name__ == "__main__":
    unittest.main()
