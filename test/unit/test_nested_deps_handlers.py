"""
Nested dependencies -- PR 6: handlers over the whole tree.

This is the one PR that changes code on the flat path, so the first class here
is a gate: for a workspace with no nesting, the generated artifacts must be
byte-identical to what a name-keyed accumulation produced.

The bug the re-key exists to prevent: two packages with the same name in
different scopes, one of which silently vanishes from packages.envrc / the
dv-flow map because the later one overwrote it.

See nested-deps-design.md §7.1/§7.2 and nested-deps-impl-plan.md §7.
"""
import os

from .test_base import TestBase

from ivpm.handlers.scope_keys import pkg_key, pkg_rel_dir, resolver_key
from ivpm.package import Package


def _dep(name, data_pkg, extra=""):
    return ("                    - name: %s\n"
            "                      url: file://${DATA_DIR}/%s\n"
            "                      src: dir\n"
            "                      link: false\n%s" % (name, data_pkg, extra))


class TestScopeKeyHelpers(TestBase):
    """The helpers degrade to the bare name, which is what keeps flat output
    unchanged."""

    def test_pkg_key_falls_back_to_name(self):
        pkg = Package("libA")
        self.assertEqual(pkg_key(pkg), "libA")

    def test_pkg_key_uses_scope_key_when_set(self):
        pkg = Package("libA")
        pkg.scope_key = "toolB/packages/libA"
        self.assertEqual(pkg_key(pkg), "toolB/packages/libA")

    def test_resolver_key_falls_back_to_resolved_by(self):
        pkg = Package("libA")
        pkg.resolved_by = "toolB"
        self.assertEqual(resolver_key(pkg), "toolB")
        pkg.resolved_by_key = "x/packages/toolB"
        self.assertEqual(resolver_key(pkg), "x/packages/toolB")

    def test_resolver_key_is_none_at_the_root(self):
        self.assertIsNone(resolver_key(Package("libA")))

    def test_rel_dir_prefers_the_real_path(self):
        pkg = Package("libA")
        pkg.path = os.path.join(self.testdir, "packages", "toolB",
                                "packages", "libA")
        self.assertEqual(
            pkg_rel_dir(pkg, os.path.join(self.testdir, "packages")),
            "toolB/packages/libA")

    def test_rel_dir_falls_back_outside_the_deps_dir(self):
        pkg = Package("libA")
        pkg.scope_key = "libA"
        pkg.path = "/somewhere/else/libA"
        self.assertEqual(
            pkg_rel_dir(pkg, os.path.join(self.testdir, "packages")), "libA")


class TestFlatOutputUnchanged(TestBase):
    """Gate: flat workspaces produce exactly the paths they always did."""

    def _envrc(self):
        with open(os.path.join(self.testdir, "packages",
                               "packages.envrc")) as fp:
            return fp.read()

    def test_flat_envrc_paths_are_bare_names(self):
        # Two envrc-publishing packages, one resolved by the other, so the
        # toposort has a real edge to order.
        self.mkFile("srcpkgs/leaf/ivpm.yaml",
                    "package:\n  name: leaf\n  dep-sets:\n"
                    "    - name: default-dev\n      deps: []\n")
        self.mkFile("srcpkgs/leaf/export.envrc", "export LEAF=1\n")
        self.mkFile("srcpkgs/mid/ivpm.yaml",
                    "package:\n  name: mid\n  dep-sets:\n"
                    "    - name: default-dev\n      deps:\n"
                    "        - name: leaf\n"
                    "          url: file://%s/srcpkgs/leaf\n"
                    "          src: dir\n"
                    "          link: false\n" % self.testdir)
        self.mkFile("srcpkgs/mid/export.envrc", "export MID=1\n")
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: flat_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: mid\n"
                    "              url: file://%s/srcpkgs/mid\n"
                    "              src: dir\n"
                    "              link: false\n" % self.testdir)
        self.ivpm_update(skip_venv=True)

        content = self._envrc()
        # Bare-name paths, exactly as before nesting existed.
        self.assertIn("source_env ./leaf/export.envrc", content)
        self.assertIn("source_env ./mid/export.envrc", content)
        # Dependencies are sourced before their dependents.
        self.assertLess(content.index("./leaf/"), content.index("./mid/"))


class TestNestedHandlerAccumulation(TestBase):

    def _mk_envrc_tree(self):
        """root -> boundary(nested) -> shared;  root -> shared.

        Both 'shared' packages have the same name and both publish an envrc.
        Name-keyed accumulation drops one of them.
        """
        self.mkFile("srcpkgs/shared_v1/ivpm.yaml",
                    "package:\n  name: shared\n  dep-sets:\n"
                    "    - name: default-dev\n      deps: []\n")
        self.mkFile("srcpkgs/shared_v1/export.envrc", "export SHARED=v1\n")
        self.mkFile("srcpkgs/shared_v2/ivpm.yaml",
                    "package:\n  name: shared\n  dep-sets:\n"
                    "    - name: default-dev\n      deps: []\n")
        self.mkFile("srcpkgs/shared_v2/export.envrc", "export SHARED=v2\n")
        self.mkFile("srcpkgs/boundary/ivpm.yaml",
                    "package:\n"
                    "  name: boundary\n"
                    "  deps-mode: nested\n"
                    "  dep-sets:\n"
                    "    - name: default-dev\n"
                    "      deps:\n"
                    "        - name: shared\n"
                    "          url: file://%s/srcpkgs/shared_v1\n"
                    "          src: dir\n"
                    "          link: false\n" % self.testdir)
        self.mkFile("srcpkgs/boundary/export.envrc", "export BOUNDARY=1\n")
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nest_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: shared\n"
                    "              url: file://%s/srcpkgs/shared_v2\n"
                    "              src: dir\n"
                    "              link: false\n"
                    "            - name: boundary\n"
                    "              url: file://%s/srcpkgs/boundary\n"
                    "              src: dir\n"
                    "              link: false\n"
                    % (self.testdir, self.testdir))

    def test_both_same_named_packages_reach_the_envrc(self):
        self._mk_envrc_tree()
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.testdir, "packages",
                               "packages.envrc")) as fp:
            content = fp.read()

        # This is the assertion the whole PR exists for: neither is dropped.
        self.assertIn("source_env ./shared/export.envrc", content)
        self.assertIn(
            "source_env ./boundary/packages/shared/export.envrc", content)
        self.assertIn("source_env ./boundary/export.envrc", content)

    def test_nested_entries_precede_their_boundary(self):
        self._mk_envrc_tree()
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.testdir, "packages",
                               "packages.envrc")) as fp:
            content = fp.read()
        self.assertLess(
            content.index("./boundary/packages/shared/"),
            content.index("./boundary/export.envrc"),
            "a nested dependency must be sourced before its boundary")

    def test_leaf_handlers_run_for_every_nested_package(self):
        # on_leaf_post_load fires once per package, nested ones included --
        # otherwise the envrc entries above could not exist at all.
        self._mk_envrc_tree()
        self.ivpm_update(skip_venv=True)

        for rel in ("shared", "boundary", "boundary/packages/shared"):
            self.assertTrue(
                os.path.isfile(os.path.join(
                    self.testdir, "packages", *rel.split("/"),
                    "export.envrc")),
                "%s was not materialized" % rel)


class TestDvFlowNested(TestBase):

    def test_flow_map_paths_are_scope_relative(self):
        flow = ("package:\n  name: %s\n  tasks:\n    - name: t\n")
        self.mkFile("srcpkgs/inner/ivpm.yaml",
                    "package:\n  name: inner\n  dep-sets:\n"
                    "    - name: default-dev\n      deps: []\n")
        self.mkFile("srcpkgs/inner/flow.yaml", flow % "inner_flow")
        self.mkFile("srcpkgs/boundary/ivpm.yaml",
                    "package:\n"
                    "  name: boundary\n"
                    "  deps-mode: nested\n"
                    "  dep-sets:\n"
                    "    - name: default-dev\n"
                    "      deps:\n"
                    "        - name: inner\n"
                    "          url: file://%s/srcpkgs/inner\n"
                    "          src: dir\n"
                    "          link: false\n" % self.testdir)
        self.mkFile("srcpkgs/boundary/flow.yaml", flow % "boundary_flow")
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: flow_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: boundary\n"
                    "              url: file://%s/srcpkgs/boundary\n"
                    "              src: dir\n"
                    "              link: false\n" % self.testdir)
        self.ivpm_update(skip_venv=True)

        map_path = os.path.join(
            self.testdir, "packages", "dv-flow-package-map.yaml")
        self.assertTrue(os.path.isfile(map_path), "no dv-flow package map")
        with open(map_path) as fp:
            content = fp.read()
        # The nested package is referenced where it actually lives.
        self.assertIn("boundary/packages/inner/flow.yaml", content)
        self.assertIn("boundary/flow.yaml", content)


class TestPythonDuplicateDistribution(TestBase):
    """One venv cannot hold two versions of a distribution, so nesting does
    NOT isolate Python. The handler says so rather than silently picking."""

    def _handler(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        h = PackageHandlerPython()
        h.reset()
        return h

    def _pkg(self, scope_key):
        pkg = Package("shared")
        pkg.scope_key = scope_key
        return pkg

    def test_same_scope_twice_is_not_a_collision(self):
        from ivpm import msg
        from ivpm.diagnostics import CollectingSink, DiagnosticReporter
        sink = CollectingSink()
        saved = msg.set_reporter(DiagnosticReporter(sink))
        try:
            h = self._handler()
            pkg = self._pkg("shared")
            h._record_python_pkg(pkg)
            h._record_python_pkg(pkg)
        finally:
            msg.set_reporter(saved)
        self.assertEqual(sink.records, [])

    def test_two_scopes_warn_and_keep_the_nearest_root(self):
        from ivpm import msg
        from ivpm.diagnostics import CollectingSink, DiagnosticReporter
        sink = CollectingSink()
        saved = msg.set_reporter(DiagnosticReporter(sink))
        try:
            h = self._handler()
            deep = self._pkg("boundary/packages/shared")
            shallow = self._pkg("shared")
            h._record_python_pkg(deep)
            h._record_python_pkg(shallow)
        finally:
            msg.set_reporter(saved)

        text = "\n".join(sink.messages())
        self.assertIn("shared", text)
        self.assertIn("boundary/packages/shared", text)
        self.assertIn("not the Python environment", text)
        self.assertIn("one venv cannot hold two versions", text)
        # Nearest-root wins, deterministically, regardless of arrival order.
        self.assertIs(h.pkgs_info["shared"], shallow)

    def test_nearest_root_wins_in_either_order(self):
        from ivpm import msg
        from ivpm.diagnostics import CollectingSink, DiagnosticReporter
        saved = msg.set_reporter(DiagnosticReporter(CollectingSink()))
        try:
            h = self._handler()
            shallow = self._pkg("shared")
            deep = self._pkg("boundary/packages/shared")
            h._record_python_pkg(shallow)
            h._record_python_pkg(deep)
        finally:
            msg.set_reporter(saved)
        self.assertIs(h.pkgs_info["shared"], shallow)
