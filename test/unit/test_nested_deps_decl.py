"""
Nested dependencies -- PR 1: declaration & data model.

Covers the three ``deps-mode`` declaration sites (package, dep-set, dependency
entry), the ``hierarchical`` alias, validation, and the not-declared/None
default that ``effective_mode`` depends on.

See nested-deps-design.md §4.1 and nested-deps-impl-plan.md §2.
"""
import os

from .test_base import TestBase

from ivpm.dep_mode import parse_deps_mode, FLATTEN, NESTED
from ivpm.diagnostics import SrcLoaderError
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo


class TestDepsModeParse(TestBase):
    """parse_deps_mode in isolation."""

    def test_canonical_values(self):
        self.assertEqual(parse_deps_mode("flatten"), FLATTEN)
        self.assertEqual(parse_deps_mode("nested"), NESTED)

    def test_hierarchical_is_an_alias_for_nested(self):
        self.assertEqual(parse_deps_mode("hierarchical"), NESTED)

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(parse_deps_mode("  Nested "), NESTED)

    def test_none_is_not_declared(self):
        # None must survive as None -- "not declared" is distinct from
        # "declared flatten" for effective_mode.
        self.assertIsNone(parse_deps_mode(None))

    def test_unknown_value_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            parse_deps_mode("sticky")
        text = str(ctx.exception)
        self.assertIn("sticky", text)
        self.assertIn("flatten", text)
        self.assertIn("nested", text)

    def test_non_string_is_fatal(self):
        with self.assertRaises(SrcLoaderError):
            parse_deps_mode(True)


class TestDepsModeDeclaration(TestBase):
    """deps-mode read from each of the three declaration sites."""

    def _read(self, content) -> ProjInfo:
        self.mkFile("ivpm.yaml", content)
        return ProjInfo.mkFromProj(self.testdir)

    def test_package_level(self):
        info = self._read("""
        package:
            name: root
            deps-mode: nested
            dep-sets:
                - name: default-dev
                  deps:
                    - name: pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
        """)
        self.assertEqual(info.deps_mode, NESTED)

    def test_package_level_hierarchical_alias(self):
        info = self._read("""
        package:
            name: root
            deps-mode: hierarchical
            dep-sets:
                - name: default-dev
                  deps:
                    - name: pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
        """)
        self.assertEqual(info.deps_mode, NESTED)

    def test_dep_set_level(self):
        info = self._read("""
        package:
            name: root
            dep-sets:
                - name: default-dev
                  deps-mode: nested
                  deps:
                    - name: pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
        """)
        self.assertEqual(info.get_dep_set("default-dev").deps_mode, NESTED)
        # Not declared at the package level -> still None there.
        self.assertIsNone(info.deps_mode)

    def test_dep_entry_level(self):
        info = self._read("""
        package:
            name: root
            dep-sets:
                - name: default-dev
                  deps:
                    - name: pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
                      deps-mode: nested
        """)
        ds = info.get_dep_set("default-dev")
        self.assertEqual(ds["pkg_a"].deps_mode, NESTED)

    def test_explicit_flatten_is_retained(self):
        # Declared flatten must NOT collapse to None: it stops propagation.
        info = self._read("""
        package:
            name: root
            dep-sets:
                - name: default-dev
                  deps:
                    - name: pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
                      deps-mode: flatten
        """)
        self.assertEqual(
            info.get_dep_set("default-dev")["pkg_a"].deps_mode, FLATTEN)

    def test_absent_everywhere_is_none(self):
        info = self._read("""
        package:
            name: root
            dep-sets:
                - name: default-dev
                  deps:
                    - name: pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
        """)
        ds = info.get_dep_set("default-dev")
        self.assertIsNone(info.deps_mode)
        self.assertIsNone(ds.deps_mode)
        self.assertIsNone(ds["pkg_a"].deps_mode)

    def test_unknown_value_is_located_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._read("""
            package:
                name: root
                deps-mode: sticky
                dep-sets:
                    - name: default-dev
                      deps:
                        - name: pkg_a
                          url: file://${DATA_DIR}/circular_pkg_a
                          src: dir
            """)
        text = str(ctx.exception)
        self.assertIn("ivpm.yaml", text)
        self.assertIn("sticky", text)

    def test_rejected_on_a_virtual_factory_dep(self):
        # A 'src: ivpm.yaml' factory occupies no directory, so it cannot host
        # a nested deps-dir.
        with self.assertRaises(SrcLoaderError) as ctx:
            self._read("""
            package:
                name: root
                dep-sets:
                    - name: default-dev
                      deps:
                        - name: factory
                          url: file://${DATA_DIR}/nested_ivpm_yaml/deps.yaml
                          src: ivpm.yaml
                          deps-mode: nested
            """)
        text = str(ctx.exception)
        self.assertIn("deps-mode", text)
        self.assertIn("factory", text)


class TestPackagesInfoCopy(TestBase):

    def test_copy_carries_deps_mode(self):
        # An omission here would be a silent inheritance bug: a dep-set that
        # 'uses' a nested base would quietly flatten.
        pi = PackagesInfo("default-dev")
        pi.deps_mode = NESTED
        self.assertEqual(pi.copy().deps_mode, NESTED)

    def test_copy_carries_none(self):
        self.assertIsNone(PackagesInfo("default-dev").copy().deps_mode)
