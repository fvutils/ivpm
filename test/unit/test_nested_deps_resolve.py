"""
Nested dependencies -- PR 3: resolution.

Exercises the resolver end-to-end over local ``src: dir`` fixtures (no
network): scope creation, propagation, precedence, coexistence of two versions
of one package, and the cycle/depth guards.

The first test in this file is the one that matters most: a workspace that
declares no deps-mode must resolve exactly as it did before nesting existed.

See nested-deps-design.md §4/§5/§8 and nested-deps-impl-plan.md §4.
"""
import os

from .test_base import TestBase

from ivpm.diagnostics import SrcLoaderError


def _dep(name, data_pkg, extra=""):
    return ("                    - name: %s\n"
            "                      url: file://${DATA_DIR}/%s\n"
            "                      src: dir\n"
            "                      link: false\n%s" % (name, data_pkg, extra))


class TestNestedResolve(TestBase):

    def _root(self, deps, package_extra=""):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nested_root\n"
                    "%s"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % (package_extra, deps))

    def _p(self, *parts):
        return os.path.join(self.testdir, *parts)

    # -- flat regression ---------------------------------------------------

    def test_flat_workspace_is_unchanged(self):
        # No deps-mode anywhere: every transitive dep lands in the one root
        # deps-dir, exactly as before.
        self._root(_dep("nested_libC", "nested_libC"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(self._p("packages", "nested_libC")))
        self.assertTrue(os.path.isdir(self._p("packages", "nested_libD")))
        # ... and nothing nested.
        self.assertFalse(os.path.exists(
            self._p("packages", "nested_libC", "packages")))

    # -- boundaries --------------------------------------------------------

    def test_producer_declared_boundary(self):
        # toolB's own manifest says deps-mode: nested.
        self._root(_dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(self._p("packages", "nested_toolB")))
        # Its deps went into its own deps-dir, not the root's.
        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_toolB", "packages", "nested_libA")))
        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_toolB", "packages", "nested_libC")))
        self.assertFalse(os.path.exists(self._p("packages", "nested_libA")))
        self.assertFalse(os.path.exists(self._p("packages", "nested_libC")))

    def test_nested_propagates_to_a_silent_dependency(self):
        # libC declares nothing, so it inherits 'nested' from toolB's scope and
        # becomes a boundary itself.
        self._root(_dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(self._p(
            "packages", "nested_toolB", "packages", "nested_libC",
            "packages", "nested_libD")))

    def test_consumer_override_creates_a_boundary(self):
        # libC's manifest says nothing; the *consumer* declares nested.
        self._root(_dep("nested_libC", "nested_libC",
                        "                      deps-mode: nested\n"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_libC", "packages", "nested_libD")))
        self.assertFalse(os.path.exists(self._p("packages", "nested_libD")))

    def test_consumer_override_beats_producer(self):
        # toolB's manifest says nested; the consumer overrides to flatten.
        self._root(_dep("nested_toolB", "nested_toolB",
                        "                      deps-mode: flatten\n"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(self._p("packages", "nested_libA")))
        self.assertTrue(os.path.isdir(self._p("packages", "nested_libC")))
        self.assertFalse(os.path.exists(
            self._p("packages", "nested_toolB", "packages")))

    def test_root_package_level_mode_propagates(self):
        # deps-mode on the root manifest makes the whole tree nested.
        self._root(_dep("nested_libC", "nested_libC"),
                   package_extra="    deps-mode: nested\n")
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_libC", "packages", "nested_libD")))

    def test_root_dep_set_level_mode_propagates(self):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nested_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps-mode: nested\n"
                    "          deps:\n"
                    "%s" % _dep("nested_libC", "nested_libC"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_libC", "packages", "nested_libD")))

    def test_explicit_flatten_stops_propagation(self):
        # Root is nested; libC's consumer entry opts back out, so libD lands
        # inside libC's *parent* scope -- the root -- rather than under libC.
        self._root(_dep("nested_libC", "nested_libC",
                        "                      deps-mode: flatten\n"),
                   package_extra="    deps-mode: nested\n")
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(self._p("packages", "nested_libD")))
        self.assertFalse(os.path.exists(
            self._p("packages", "nested_libC", "packages")))

    # -- coexistence -------------------------------------------------------

    def test_two_versions_of_one_package_coexist(self):
        # The root pulls libA v2; toolB (nested) pulls libA v1. Flat resolution
        # could only have kept one of them.
        self._root(_dep("nested_libA", "nested_libA_v2")
                   + _dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        root_v = self._p("packages", "nested_libA", "version.txt")
        nested_v = self._p("packages", "nested_toolB", "packages",
                           "nested_libA", "version.txt")
        self.assertTrue(os.path.isfile(root_v))
        self.assertTrue(os.path.isfile(nested_v))
        with open(root_v) as fp:
            self.assertEqual(fp.read().strip(), "v2")
        with open(nested_v) as fp:
            self.assertEqual(fp.read().strip(), "v1")

    def test_nested_package_may_share_the_root_projects_name(self):
        # R4: the root-project guard is root-scope-keyed, so a *nested* package
        # of the same name is not silently skipped.
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nested_libD\n"
                    "    deps-mode: nested\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % _dep("nested_libC", "nested_libC"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_libC", "packages", "nested_libD")))

    # -- guards ------------------------------------------------------------

    def test_cycle_under_nesting_terminates(self):
        # cyc_a -> cyc_b -> cyc_a ... Flat mode absorbs this by name-dedup;
        # nesting makes it reachable, so the ancestry guard must stop it.
        self._root(_dep("nested_cyc_a", "nested_cyc_a"),
                   package_extra="    deps-mode: nested\n")
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(self._p("packages", "nested_cyc_a")))
        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_cyc_a", "packages", "nested_cyc_b")))
        # The third level is materialized but not descended into.
        third = self._p("packages", "nested_cyc_a", "packages",
                        "nested_cyc_b", "packages", "nested_cyc_a")
        self.assertTrue(os.path.isdir(third))
        self.assertFalse(os.path.exists(os.path.join(third, "packages")))

    def test_depth_cap_is_reported(self):
        os.environ["IVPM_MAX_DEP_DEPTH"] = "1"
        try:
            self._root(_dep("nested_cyc_a", "nested_cyc_a"),
                       package_extra="    deps-mode: nested\n")
            with self.assertRaises(SrcLoaderError) as ctx:
                self.ivpm_update(skip_venv=True)
            self.assertIn("IVPM_MAX_DEP_DEPTH", str(ctx.exception))
        finally:
            os.environ.pop("IVPM_MAX_DEP_DEPTH", None)

    def test_live_link_boundary_is_refused(self):
        # A `src: dir` dep with the default link:true is the user's working
        # copy; nesting it must not silently detach or write into it.
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nested_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: nested_libC\n"
                    "              url: file://${DATA_DIR}/nested_libC\n"
                    "              src: dir\n"
                    "              deps-mode: nested\n")
        with self.assertRaises(SrcLoaderError) as ctx:
            self.ivpm_update(skip_venv=True)
        text = str(ctx.exception)
        self.assertIn("live link", text)
        self.assertIn("link: false", text)
        # And the source tree was not touched.
        self.assertFalse(os.path.exists(
            os.path.join(self.data_dir, "nested_libC", "packages")))

    # -- idempotency -------------------------------------------------------

    def test_second_update_is_stable(self):
        self._root(_dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(
            self._p("packages", "nested_toolB", "packages", "nested_libC",
                    "packages", "nested_libD")))
