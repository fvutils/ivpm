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

    def test_multiple_bases(self):
        """'uses' as a list composes packages from every named base."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: sim
      deps:
        - name: lib_sim
          src: pypi
    - name: gui
      deps:
        - name: lib_gui
          src: pypi
    - name: everything
      uses: [sim, gui]
      deps:
        - name: lib_extra
          src: pypi
""")
        everything = proj.get_dep_set("everything")
        self.assertIn("lib_sim",   everything.packages)
        self.assertIn("lib_gui",   everything.packages)
        self.assertIn("lib_extra", everything.packages)
        # Bases themselves are untouched
        self.assertNotIn("lib_gui", proj.get_dep_set("sim").packages)

    def test_multiple_bases_later_wins(self):
        """When two bases define the same package, the later base wins."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: stable
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: stable
    - name: edge
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: edge
    - name: combined
      uses: [stable, edge]
      deps: []
""")
        combined = proj.get_dep_set("combined")
        self.assertEqual(combined.packages["mylib"].branch, "edge")

    def test_multiple_bases_own_deps_win(self):
        """The dep-set's own deps override every base."""
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: a
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: from-a
    - name: b
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: from-b
    - name: c
      uses: [a, b]
      deps:
        - name: mylib
          url: https://github.com/org/mylib.git
          branch: from-c
""")
        self.assertEqual(proj.get_dep_set("c").packages["mylib"].branch, "from-c")

    def test_multi_base_cycle_error(self):
        """A cycle through one of several bases is still detected."""
        with self.assertRaises(Exception) as ctx:
            _parse("""
package:
  name: test_project
  dep-sets:
    - name: set_a
      uses: [set_b]
      deps: []
    - name: set_b
      uses: [other, set_a]
      deps: []
    - name: other
      deps: []
""")
        self.assertIn("Cyclic", str(ctx.exception))

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

    def test_inherited_dep_keeps_declaring_dep_set(self):
        """
        An inherited dependency keeps the dep-set of the dep-set that *declared*
        it, not the name of the dep-set that inherited it.

        'dev' uses 'use', and 'use' declares 'a'. Since 'dev' says nothing about
        'a', pulling 'dev' must load a's 'use' dep-set -- the declaring context
        travels with the dependency.
        """
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: use
      deps:
        - name: a
          src: git
          url: https://example.com/a.git
    - name: dev
      uses: use
      deps:
        - name: pytest
          src: pypi
""")
        # In its declaring dep-set, 'a' resolves against a's 'use' dep-set
        self.assertEqual("use", proj.get_dep_set("use").packages["a"].dep_set)

        dev = proj.get_dep_set("dev")
        # The inherited dep still points at 'use', *not* 'dev'
        self.assertEqual("use", dev.packages["a"].dep_set)
        # ...while 'dev's own dep defaults to 'dev'
        self.assertEqual("dev", dev.packages["pytest"].dep_set)

    def test_inheritor_default_dep_set_does_not_rewrite_inherited(self):
        """
        'default-dep-set' on the inheriting dep-set applies only to the deps that
        dep-set declares itself; inherited deps keep their base's dep-set.
        """
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: use
      deps:
        - name: a
          src: git
          url: https://example.com/a.git
    - name: dev
      uses: use
      default-dep-set: dev-tools
      deps:
        - name: b
          src: git
          url: https://example.com/b.git
""")
        dev = proj.get_dep_set("dev")
        self.assertEqual("use", dev.packages["a"].dep_set)
        self.assertEqual("dev-tools", dev.packages["b"].dep_set)

    def test_explicit_dep_set_overrides_inherited(self):
        """
        Re-declaring the dep in the inheriting dep-set with an explicit
        'dep-set' overrides the inherited entry.
        """
        proj = _parse("""
package:
  name: test_project
  dep-sets:
    - name: use
      deps:
        - name: a
          src: git
          url: https://example.com/a.git
    - name: dev
      uses: use
      deps:
        - name: a
          src: git
          url: https://example.com/a.git
          dep-set: dev
""")
        self.assertEqual("use", proj.get_dep_set("use").packages["a"].dep_set)
        self.assertEqual("dev", proj.get_dep_set("dev").packages["a"].dep_set)


class TestDepSetInheritanceProvenance(unittest.TestCase):
    """Inheritance provenance: which packages a dep-set declared itself, which
    base supplied each of the rest, and which declarations displaced a base's.

    'packages' stays the fully-resolved set throughout -- these three fields
    record only what the merge would otherwise have thrown away.
    """

    def test_three_flavours_distinguishable(self):
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: rtl-only
      deps:
        - name: uvm
          url: https://example.com/uvm.git
          tag: UVM_1_2
        - name: rtl
          src: pypi
    - name: sim-questa
      uses: rtl-only
      deps:
        - name: uvm
          url: https://example.com/uvm.git
          tag: UVM_2_0
        - name: questa-vip
          src: pypi
""")
        ds = proj.get_dep_set("sim-questa")

        # inherited, untouched
        self.assertEqual(ds.inherited_from, {"rtl": "rtl-only"})
        # declared locally (whether or not it displaced a base entry)
        self.assertEqual(sorted(ds.own_packages.keys()), ["questa-vip", "uvm"])
        # declared locally AND displaced a base entry
        self.assertEqual(list(ds.overrides.keys()), ["uvm"])
        base, displaced = ds.overrides["uvm"]
        self.assertEqual(base, "rtl-only")
        self.assertEqual(displaced.tag, "UVM_1_2")
        # ...while the resolved set holds the winning entry
        self.assertEqual(ds.packages["uvm"].tag, "UVM_2_0")
        # added: locally declared and NOT an override
        self.assertNotIn("questa-vip", ds.overrides)
        self.assertNotIn("questa-vip", ds.inherited_from)

    def test_no_uses_own_equals_packages(self):
        """A dep-set with no 'uses' still gets a meaningful own_packages, so
        consumers need no special case for the un-derived kind."""
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: a
          src: pypi
        - name: b
          src: pypi
""")
        ds = proj.get_dep_set("default")
        self.assertEqual(sorted(ds.own_packages.keys()), ["a", "b"])
        self.assertEqual(ds.own_packages, ds.packages)
        self.assertEqual(ds.inherited_from, {})
        self.assertEqual(ds.overrides, {})

    def test_uses_only_compound(self):
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: one
      deps:
        - name: a
          src: pypi
    - name: two
      deps:
        - name: b
          src: pypi
    - name: both
      uses: [one, two]
""")
        ds = proj.get_dep_set("both")
        self.assertEqual(ds.own_packages, {})
        self.assertEqual(ds.overrides, {})
        self.assertEqual(ds.inherited_from, {"a": "one", "b": "two"})

    def test_colliding_bases_attribution_matches_the_merge(self):
        """The later base wins in 'packages'; inherited_from must name that
        same base. An attribution that disagrees with the merge result is
        worse than no attribution at all."""
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: one
      deps:
        - name: shared
          url: https://example.com/shared.git
          branch: from-one
    - name: two
      deps:
        - name: shared
          url: https://example.com/shared.git
          branch: from-two
    - name: both
      uses: [one, two]
""")
        ds = proj.get_dep_set("both")
        self.assertEqual(ds.packages["shared"].branch, "from-two")
        self.assertEqual(ds.inherited_from["shared"], "two")

    def test_three_level_chain_attributes_to_direct_base(self):
        """Attribution is to the DIRECT base, not the original declarer.

        With 'ci uses sim uses rtl', a package reaching 'ci' from 'rtl' is
        reported as coming from 'sim': the question is what ci's own bases
        contribute. The full chain stays walkable via 'uses'.
        """
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: rtl
      deps:
        - name: a
          src: pypi
    - name: sim
      uses: rtl
      deps:
        - name: b
          src: pypi
    - name: ci
      uses: sim
      deps:
        - name: c
          src: pypi
""")
        ci = proj.get_dep_set("ci")
        self.assertEqual(ci.inherited_from, {"a": "sim", "b": "sim"})
        self.assertEqual(list(ci.own_packages.keys()), ["c"])
        # ...and the intermediate level attributes to ITS direct base
        self.assertEqual(proj.get_dep_set("sim").inherited_from, {"a": "rtl"})

    def test_base_not_polluted(self):
        """Resolving a derived dep-set must not write provenance onto its base."""
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: base
      deps:
        - name: a
          src: pypi
    - name: derived
      uses: base
      deps:
        - name: a
          src: pypi
          version: "2.0"
""")
        base = proj.get_dep_set("base")
        self.assertEqual(base.inherited_from, {})
        self.assertEqual(base.overrides, {})
        self.assertEqual(sorted(base.own_packages.keys()), ["a"])

    def test_copy_preserves_provenance(self):
        """PackagesInfo.copy() enumerates its fields explicitly, so a field it
        forgets vanishes silently -- exactly in the cases that involve copying."""
        proj = _parse("""
package:
  name: p
  description: X
  dep-sets:
    - name: base
      deps:
        - name: a
          url: https://example.com/a.git
          branch: one
    - name: derived
      description: Derived set.
      doc: Prose.
      kind: collection
      uses: base
      deps:
        - name: a
          url: https://example.com/a.git
          branch: two
        - name: b
          src: pypi
""")
        ds = proj.get_dep_set("derived")
        cp = ds.copy()

        self.assertEqual(cp.own_packages, ds.own_packages)
        self.assertEqual(cp.inherited_from, ds.inherited_from)
        self.assertEqual(cp.overrides, ds.overrides)
        self.assertEqual(cp.description, "Derived set.")
        self.assertEqual(cp.doc, "Prose.")
        self.assertEqual(cp.kind, "collection")
        self.assertEqual(cp.uses, ["base"])
        self.assertEqual(cp.packages, ds.packages)
        # a copy is independent: mutating it must not disturb the original
        cp.own_packages["zzz"] = None
        self.assertNotIn("zzz", ds.own_packages)

    def test_resolved_packages_unchanged_for_repo_manifest(self):
        """Regression over this repo's own ivpm.yaml: recording provenance must
        not perturb the resolved set of any dep-set it declares."""
        repo_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        manifest = os.path.join(repo_root, "ivpm.yaml")
        if not os.path.isfile(manifest):
            self.skipTest("repo ivpm.yaml not present")
        with open(manifest) as fp:
            proj = IvpmYamlReader().read(fp, manifest)

        self.assertTrue(proj.dep_set_m)
        for name, ds in proj.dep_set_m.items():
            # Every resolved name is accounted for: declared here, or supplied
            # by a base. Nothing appears from nowhere, nothing goes missing.
            accounted = set(ds.own_packages.keys()) | set(ds.inherited_from.keys())
            self.assertEqual(set(ds.packages.keys()), accounted,
                             "dep-set '%s'" % name)
            for pkg_name in ds.overrides.keys():
                self.assertIn(pkg_name, ds.own_packages)
                self.assertNotIn(pkg_name, ds.inherited_from)


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

    def test_inherited_dep_pulls_declaring_dep_set(self):
        """
        End-to-end: root 'dev' uses 'use'; 'use' declares dependency 'pkg_a'.
        'dev' says nothing about pkg_a, so pkg_a must be expanded using *its*
        'use' dep-set -- not 'dev' -- pulling leaf_proj1 and not leaf_proj2.
        """
        self.mkFile("src/pkg_a/ivpm.yaml", """
package:
    name: pkg_a
    dep-sets:
        - name: use
          default-dep-set: default
          deps:
            - name: leaf_proj1
              url: file://${DATA_DIR}/leaf_proj1
              src: dir
        - name: dev
          uses: use
          default-dep-set: default
          deps:
            - name: leaf_proj2
              url: file://${DATA_DIR}/leaf_proj2
              src: dir
""")
        self.mkFile("ivpm.yaml", """
package:
    name: test_inherit_ctx
    dep-sets:
        - name: use
          deps:
            - name: pkg_a
              url: file://${TEST_DIR}/src/pkg_a
              src: dir
        - name: dev
          uses: use
""")
        self.ivpm_update(dep_set="dev", skip_venv=True)

        pkgs = os.path.join(self.testdir, "packages")
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "pkg_a")),
                        "pkg_a should be inherited from the 'use' dep-set")
        self.assertTrue(os.path.isdir(os.path.join(pkgs, "leaf_proj1")),
                        "pkg_a must be expanded with its 'use' dep-set")
        self.assertFalse(os.path.isdir(os.path.join(pkgs, "leaf_proj2")),
                         "pkg_a must NOT be expanded with its 'dev' dep-set")


if __name__ == "__main__":
    unittest.main()
