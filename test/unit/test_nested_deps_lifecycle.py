"""
Nested dependencies -- PR 7: status / sync / destroy / show over a nested tree.

All four read the lock rather than listing the deps-dir, so once lock keys are
scope paths they reach nested packages by construction. What these tests pin
down is that they reach *every* package (the destroy safety gate especially),
label them unambiguously, and render the tree correctly.

See nested-deps-design.md §7.5 and nested-deps-impl-plan.md §8.
"""
import os

from .test_base import TestBase

from ivpm.project_ops import ProjectOps


def _dep(name, data_pkg, extra=""):
    return ("                    - name: %s\n"
            "                      url: file://${DATA_DIR}/%s\n"
            "                      src: dir\n"
            "                      link: false\n%s" % (name, data_pkg, extra))


class _Args:
    def __init__(self, **kw):
        self.anonymous_git = None
        self.dry_run = False
        self.force = False
        self.deps_only = False
        self.keep_venv = False
        self.packages_filter = None
        self.jobs = 0
        for k, v in kw.items():
            setattr(self, k, v)


class NestedWorkspace(TestBase):
    """root -> toolB(nested) -> {libA v1, libC -> libD};  root -> libA v2."""

    def setUp(self):
        super().setUp()
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nested_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s%s" % (_dep("nested_libA", "nested_libA_v2"),
                              _dep("nested_toolB", "nested_toolB")))
        self.ivpm_update(skip_venv=True)


class TestStatus(NestedWorkspace):

    def test_lists_nested_packages_by_scope_path(self):
        _, results = ProjectOps(self.testdir).status(args=_Args())
        names = sorted(r.name for r in results)

        self.assertIn("nested_libA", names)
        self.assertIn("nested_toolB", names)
        self.assertIn("nested_toolB/packages/nested_libA", names)
        self.assertIn("nested_toolB/packages/nested_libC", names)
        self.assertIn(
            "nested_toolB/packages/nested_libC/packages/nested_libD", names)

    def test_paths_point_at_the_real_locations(self):
        _, results = ProjectOps(self.testdir).status(args=_Args())
        by_name = {r.name: r for r in results}

        nested = by_name["nested_toolB/packages/nested_libA"]
        self.assertEqual(
            os.path.normpath(nested.path),
            os.path.join(self.testdir, "packages", "nested_toolB",
                         "packages", "nested_libA"))
        self.assertTrue(os.path.isdir(nested.path))

    def test_both_versions_are_distinguishable(self):
        _, results = ProjectOps(self.testdir).status(args=_Args())
        libas = [r for r in results if r.name.endswith("nested_libA")]
        self.assertEqual(len(libas), 2)
        self.assertEqual(len({r.name for r in libas}), 2,
                         "two same-named packages collapsed to one label")


class TestSync(NestedWorkspace):

    def test_reaches_every_package_in_the_tree(self):
        results = ProjectOps(self.testdir).sync(args=_Args())
        names = sorted(r.name for r in results)
        self.assertIn("nested_toolB/packages/nested_libC", names)
        self.assertIn(
            "nested_toolB/packages/nested_libC/packages/nested_libD", names)

    def test_filter_accepts_a_bare_name_or_a_scope_path(self):
        ops = ProjectOps(self.testdir)

        by_path = ops.sync(args=_Args(
            packages_filter=["nested_toolB/packages/nested_libA"]))
        self.assertEqual([r.name for r in by_path],
                         ["nested_toolB/packages/nested_libA"])

        # A bare name reaches every scope holding that name.
        by_name = ops.sync(args=_Args(packages_filter=["nested_libA"]))
        self.assertEqual(
            sorted(r.name for r in by_name),
            ["nested_libA", "nested_toolB/packages/nested_libA"])


class TestDestroy(NestedWorkspace):

    def test_gate_covers_every_package_in_the_tree(self):
        report = ProjectOps(self.testdir).destroy_plan(args=_Args())
        # The safety gate must see nested packages too -- otherwise a dirty
        # nested clone could be removed without ever being checked.
        self.assertIn("nested_toolB/packages/nested_libC", report.gate)
        self.assertIn(
            "nested_toolB/packages/nested_libC/packages/nested_libD",
            report.gate)

    def test_gate_keys_do_not_collide_on_a_shared_name(self):
        # Both libA versions must have their own verdict: a BLOCKED one must
        # not be overwritten by a SAFE one that happens to share a name.
        report = ProjectOps(self.testdir).destroy_plan(args=_Args())
        self.assertIn("nested_libA", report.gate)
        self.assertIn("nested_toolB/packages/nested_libA", report.gate)

    def test_apply_removes_the_whole_tree(self):
        ProjectOps(self.testdir).destroy_apply(args=_Args(deps_only=True))
        self.assertFalse(os.path.exists(
            os.path.join(self.testdir, "packages", "nested_toolB")))
        self.assertFalse(os.path.exists(
            os.path.join(self.testdir, "packages", "nested_libA")))

    def test_nested_packages_are_actually_classified(self):
        from ivpm.pkg_remove import SafetyLevel

        report = ProjectOps(self.testdir).destroy_plan(args=_Args())
        verdict = report.gate[
            "nested_toolB/packages/nested_libC/packages/nested_libD"]
        # A copied `dir` package has no VCS and no base snapshot, so the gate
        # can only say UNVERIFIABLE. What matters is that it was classified at
        # all -- that is what proves the gate reached into the nested scope.
        self.assertIn(verdict.level,
                      (SafetyLevel.SAFE, SafetyLevel.UNVERIFIABLE,
                       SafetyLevel.BLOCKED))


class TestShowDeps(NestedWorkspace):

    def _graph(self):
        from ivpm.show.dep_loader import DepLoader
        return DepLoader(self.testdir, dep_set="default-dev").load()

    def _find(self, nodes, name):
        for n in nodes:
            if n.name == name:
                return n
            found = self._find(n.deps, name)
            if found is not None:
                return found
        return None

    def test_tree_renders_the_nested_scope(self):
        graph = self._graph()
        toolb = self._find(graph.nodes, "nested_toolB")
        self.assertIsNotNone(toolb)
        self.assertTrue(toolb.nested, "toolB should be marked as a boundary")

        child_names = sorted(d.name for d in toolb.deps)
        self.assertEqual(child_names, ["nested_libA", "nested_libC"])

    def test_nested_nodes_carry_their_scope(self):
        graph = self._graph()
        toolb = self._find(graph.nodes, "nested_toolB")
        nested_liba = [d for d in toolb.deps if d.name == "nested_libA"][0]
        self.assertEqual(nested_liba.scope, "nested_toolB/packages/")
        # ... and the root libA is a distinct node in the root scope.
        root_liba = [d for d in graph.nodes if d.name == "nested_libA"][0]
        self.assertEqual(root_liba.scope, "")

    def test_nested_node_resolves_its_own_lock_entry(self):
        # The two libA nodes must not share one lock entry: they are different
        # packages that happen to share a name. ('dir' packages record a
        # 'path', not a 'url', so the requester is what distinguishes them.)
        graph = self._graph()
        root_liba = [d for d in graph.nodes if d.name == "nested_libA"][0]
        toolb = self._find(graph.nodes, "nested_toolB")
        nested_liba = [d for d in toolb.deps if d.name == "nested_libA"][0]
        self.assertEqual(root_liba.specifier, "root")
        self.assertEqual(nested_liba.specifier, "nested_toolB")

    def test_a_nested_scope_is_not_shadowed_by_the_root(self):
        # libA exists at the root too, but the nested one is genuinely
        # installed in its own scope -- it is not a shadowed duplicate.
        graph = self._graph()
        toolb = self._find(graph.nodes, "nested_toolB")
        nested_liba = [d for d in toolb.deps if d.name == "nested_libA"][0]
        self.assertFalse(nested_liba.shadowed)


class TestShowDepsCycle(TestBase):

    def test_elided_cycle_is_marked_and_not_expanded(self):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cyc_root\n"
                    "    deps-mode: nested\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % _dep("nested_cyc_a", "nested_cyc_a"))
        self.ivpm_update(skip_venv=True)

        from ivpm.show.dep_loader import DepLoader
        graph = DepLoader(self.testdir, dep_set="default-dev").load()

        # Walk down to the repeat.
        a = [d for d in graph.nodes if d.name == "nested_cyc_a"][0]
        b = [d for d in a.deps if d.name == "nested_cyc_b"][0]
        a2 = [d for d in b.deps if d.name == "nested_cyc_a"][0]

        self.assertIsNotNone(a2.cycle_elided,
                             "the repeated package should be marked elided")
        self.assertEqual(a2.deps, [],
                         "an elided node must not be expanded further")
