"""
Tests for dep-set inheritance via the 'uses' keyword.

Design rule: inheritance is resolved at parse time inside IvpmYamlReader,
so all callers of ProjInfo.get_dep_set() see the merged result transparently.
"""
import io
import os
import unittest

from .test_base import TestBase
from ivpm.ivpm_yaml_reader import IvpmYamlReader


def _parse(yaml_text):
    """Helper: parse an ivpm.yaml string and return ProjInfo."""
    reader = IvpmYamlReader()
    return reader.read(io.StringIO(yaml_text), "<test>")


class TestDepSetInheritanceParse(unittest.TestCase):
    """Unit tests that parse YAML directly, no filesystem/network needed."""

    def test_basic_inheritance(self):
        """Packages from the base dep-set appear in the child dep-set."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: default
      deps:
        - name: lib_a
          src: pypi
        - name: lib_b
          src: pypi
    - name: default-dev
      uses: default
      deps:
        - name: pytest
          src: pypi
""")
        dev = proj.get_dep_set("default-dev")
        self.assertIn("lib_a",  dev.packages)
        self.assertIn("lib_b",  dev.packages)
        self.assertIn("pytest", dev.packages)

    def test_base_unaffected(self):
        """The base dep-set itself must not gain packages from the child."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: default
      deps:
        - name: lib_a
          src: pypi
    - name: default-dev
      uses: default
      deps:
        - name: pytest
          src: pypi
""")
        base = proj.get_dep_set("default")
        self.assertIn("lib_a",  base.packages)
        self.assertNotIn("pytest", base.packages)

    def test_override_wins(self):
        """A package present in both sets keeps the child's definition."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: default
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: v1.0
    - name: default-dev
      uses: default
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: dev
""")
        dev = proj.get_dep_set("default-dev")
        self.assertIn("mylib", dev.packages)
        self.assertEqual(dev.packages["mylib"].branch, "dev")

    def test_no_extra_deps(self):
        """uses with an empty deps list inherits everything from the base."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: default
      deps:
        - name: lib_a
          src: pypi
        - name: lib_b
          src: pypi
    - name: ci
      uses: default
      deps: []
""")
        ci = proj.get_dep_set("ci")
        self.assertIn("lib_a", ci.packages)
        self.assertIn("lib_b", ci.packages)
        self.assertEqual(2, len(ci.packages))

    def test_multi_level_chain(self):
        """Three-level chain: all packages visible at the top level."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: base
      deps:
        - name: lib_base
          src: pypi
    - name: mid
      uses: base
      deps:
        - name: lib_mid
          src: pypi
    - name: top
      uses: mid
      deps:
        - name: lib_top
          src: pypi
""")
        top = proj.get_dep_set("top")
        self.assertIn("lib_base", top.packages)
        self.assertIn("lib_mid",  top.packages)
        self.assertIn("lib_top",  top.packages)

    def test_order_independent(self):
        """Child defined before its base still resolves correctly."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: default-dev
      uses: default
      deps:
        - name: pytest
          src: pypi
    - name: default
      deps:
        - name: lib_a
          src: pypi
""")
        dev = proj.get_dep_set("default-dev")
        self.assertIn("lib_a",  dev.packages)
        self.assertIn("pytest", dev.packages)

    def test_unknown_base_error(self):
        """Referencing a non-existent base dep-set raises a clear exception."""
        with self.assertRaises(Exception) as ctx:
            _parse("""
package:
  name: test_project
  dep-sets:
    - name: default-dev
      uses: nonexistent
      deps: []
""")
        self.assertIn("nonexistent", str(ctx.exception))

    def test_cycle_error(self):
        """A -> B -> A is detected and raises a cycle exception."""
        with self.assertRaises(Exception) as ctx:
            _parse("""
package:
  name: test_project
  dep-sets:
    - name: set_a
      uses: set_b
      deps: []
    - name: set_b
      uses: set_a
      deps: []
""")
        self.assertIn("Cyclic", str(ctx.exception))

    def test_no_uses_unaffected(self):
        """Dep-sets without 'uses' are not modified."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: default
      deps:
        - name: lib_a
          src: pypi
    - name: standalone
      deps:
        - name: lib_b
          src: pypi
""")
        standalone = proj.get_dep_set("standalone")
        self.assertIn("lib_b",  standalone.packages)
        self.assertNotIn("lib_a", standalone.packages)


class TestDepSetInheritanceIntegration(TestBase):
    """Integration tests: full ivpm_update() / ivpm_sync() with inheritance."""

    def test_update_with_inheritance(self):
        """ivpm update installs packages from both base and child dep-sets."""
        self.mkFile("ivpm.yaml", """
package:
    name: test_update_inherit
    dep-sets:
        - name: default
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
        - name: default-dev
          uses: default
          deps:
            - name: leaf_proj2
              url: file://${DATA_DIR}/leaf_proj2
              src: dir
""")
        self.ivpm_update(dep_set="default-dev", skip_venv=True)

        pkgs = os.path.join(self.testdir, "packages")
        # Both the inherited and the child-only package must be present
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "leaf_proj1")),
                        "leaf_proj1 (from base 'default') should be installed")
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "leaf_proj2")),
                        "leaf_proj2 (from 'default-dev') should be installed")

    def test_update_base_only(self):
        """ivpm update with the base dep-set only installs base packages."""
        self.mkFile("ivpm.yaml", """
package:
    name: test_update_base
    dep-sets:
        - name: default
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
        - name: default-dev
          uses: default
          deps:
            - name: leaf_proj2
              url: file://${DATA_DIR}/leaf_proj2
              src: dir
""")
        self.ivpm_update(dep_set="default", skip_venv=True)

        pkgs = os.path.join(self.testdir, "packages")
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "leaf_proj1")),
                        "leaf_proj1 should be installed via 'default'")
        self.assertFalse(os.path.isdir(os.path.join(pkgs, "leaf_proj2")),
                         "leaf_proj2 should NOT be installed when using 'default'")

    def test_override_wins_integration(self):
        """When the same package appears in base and child, child version wins."""
        # leaf_proj2 overrides the dep-set for leaf_proj1's sub-dep in the child.
        # Simplest check: the child's dep-set entry for a shared package is used.
        self.mkFile("ivpm.yaml", """
package:
    name: test_override
    dep-sets:
        - name: default
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
        - name: default-dev
          uses: default
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
            - name: leaf_proj2
              url: file://${DATA_DIR}/leaf_proj2
              src: dir
""")
        self.ivpm_update(dep_set="default-dev", skip_venv=True)

        pkgs = os.path.join(self.testdir, "packages")
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "leaf_proj1")))
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "leaf_proj2")))


if __name__ == "__main__":
    unittest.main()
