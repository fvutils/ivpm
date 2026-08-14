"""
Nested dependencies -- PR 2: the pure scope module.

No filesystem, no network: scope paths, effective-mode precedence, and the
recursion guards in isolation.

See nested-deps-design.md §4.2/§4.4 and nested-deps-impl-plan.md §3.
"""
import os
import unittest

from ivpm.dep_mode import FLATTEN, NESTED
from ivpm.dep_scope import (
    DepScope, effective_mode, scope_path, check_recursion, pkg_identity,
    max_dep_depth, _DEFAULT_MAX_DEPTH,
)
from ivpm.diagnostics import SrcLoaderError
from ivpm.package import Package
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo


def mkpkg(name, commit=None, url=None, deps_mode=None, path=None):
    """A minimally-resolved git package -- enough for pkg_identity."""
    pkg = Package(name)
    pkg.src_type = "git"
    pkg.url = url if url is not None else "https://example.invalid/%s.git" % name
    pkg.resolved_commit = commit
    pkg.deps_mode = deps_mode
    pkg.path = path if path is not None else "/ws/packages/%s" % name
    return pkg


def mkproj(deps_mode=None, dep_set_mode=None, dep_set="default"):
    """A ProjInfo standing in for a producer's manifest."""
    info = ProjInfo(is_src=True)
    info.name = "producer"
    info.deps_mode = deps_mode
    ds = PackagesInfo(dep_set)
    ds.deps_mode = dep_set_mode
    info.set_dep_set(dep_set, ds)
    return info


def root_scope(mode=FLATTEN):
    return DepScope(name="", deps_dir="/ws/packages", parent=None, mode=mode)


class TestScopePaths(unittest.TestCase):

    def test_root_path_is_empty(self):
        self.assertEqual(root_scope().path, "")
        self.assertTrue(root_scope().is_root)
        self.assertEqual(root_scope().depth, 0)

    def test_one_level(self):
        root = root_scope()
        child = root.open_child(mkpkg("toolB"), "packages", NESTED)
        self.assertEqual(child.path, "toolB/packages")
        self.assertEqual(child.depth, 1)
        self.assertEqual(child.deps_dir,
                         os.path.join("/ws/packages/toolB", "packages"))

    def test_two_levels(self):
        root = root_scope()
        b = root.open_child(mkpkg("toolB", path="/ws/packages/toolB"), "packages", NESTED)
        c = b.open_child(
            mkpkg("libC", path="/ws/packages/toolB/packages/libC"), "packages", NESTED)
        self.assertEqual(c.path, "toolB/packages/libC/packages")
        self.assertEqual(c.depth, 2)

    def test_custom_deps_dir_name(self):
        root = root_scope()
        child = root.open_child(mkpkg("toolB"), "vendor", NESTED)
        self.assertEqual(child.path, "toolB/vendor")

    def test_scope_path_of_a_package(self):
        root = root_scope()
        self.assertEqual(scope_path(root, "libA"), "libA")
        child = root.open_child(mkpkg("toolB"), "packages", NESTED)
        self.assertEqual(scope_path(child, "libA"), "toolB/packages/libA")

    def test_open_child_registers_and_is_idempotent(self):
        root = root_scope()
        pkg = mkpkg("toolB")
        first = root.open_child(pkg, "packages", NESTED)
        second = root.open_child(pkg, "packages", NESTED)
        self.assertIs(first, second)
        self.assertEqual(list(root.children.keys()), ["toolB/packages"])
        self.assertIs(first.owner, pkg)
        self.assertIs(first.parent, root)
        self.assertEqual(first.mode, NESTED)

    def test_ancestors_walks_to_root(self):
        root = root_scope()
        b = root.open_child(mkpkg("toolB"), "packages", NESTED)
        c = b.open_child(mkpkg("libC"), "packages", NESTED)
        self.assertEqual([s.path for s in c.ancestors()],
                         ["toolB/packages/libC/packages", "toolB/packages", ""])

    def test_find_package_does_not_fall_back_to_parent(self):
        # A nested sub-tree resolves independently -- that is the point.
        root = root_scope()
        root.packages["libA"] = mkpkg("libA")
        child = root.open_child(mkpkg("toolB"), "packages", NESTED)
        self.assertIsNone(child.find_package("libA"))
        self.assertIsNotNone(root.find_package("libA"))


class TestEffectiveMode(unittest.TestCase):

    def test_inherits_scope_when_nothing_declared(self):
        self.assertEqual(effective_mode(mkpkg("a"), root_scope(FLATTEN)), FLATTEN)
        self.assertEqual(effective_mode(mkpkg("a"), root_scope(NESTED)), NESTED)

    def test_producer_manifest_beats_inherited(self):
        pkg = mkpkg("a")
        pkg.proj_info = mkproj(deps_mode=NESTED)
        self.assertEqual(effective_mode(pkg, root_scope(FLATTEN)), NESTED)

    def test_producer_flatten_stops_propagation(self):
        # Explicit flatten inside a nested sub-tree must NOT be treated as
        # "not declared".
        pkg = mkpkg("a")
        pkg.proj_info = mkproj(deps_mode=FLATTEN)
        self.assertEqual(effective_mode(pkg, root_scope(NESTED)), FLATTEN)

    def test_producer_dep_set_beats_producer_package(self):
        pkg = mkpkg("a")
        pkg.dep_set = "default"
        pkg.proj_info = mkproj(deps_mode=FLATTEN, dep_set_mode=NESTED)
        self.assertEqual(effective_mode(pkg, root_scope(FLATTEN)), NESTED)

    def test_consumer_beats_producer(self):
        pkg = mkpkg("a", deps_mode=FLATTEN)
        pkg.proj_info = mkproj(deps_mode=NESTED)
        self.assertEqual(effective_mode(pkg, root_scope(NESTED)), FLATTEN)

    def test_consumer_beats_inherited(self):
        pkg = mkpkg("a", deps_mode=NESTED)
        self.assertEqual(effective_mode(pkg, root_scope(FLATTEN)), NESTED)

    def test_unresolved_producer_falls_through_to_scope(self):
        # proj_info present but silent at both levels.
        pkg = mkpkg("a")
        pkg.dep_set = "default"
        pkg.proj_info = mkproj()
        self.assertEqual(effective_mode(pkg, root_scope(NESTED)), NESTED)

    def test_unknown_dep_set_name_is_not_an_error(self):
        pkg = mkpkg("a")
        pkg.dep_set = "no-such-set"
        pkg.proj_info = mkproj(deps_mode=NESTED)
        self.assertEqual(effective_mode(pkg, root_scope(FLATTEN)), NESTED)


class TestPkgIdentity(unittest.TestCase):

    def test_same_resolution_same_identity(self):
        self.assertEqual(pkg_identity(mkpkg("a", commit="abc")),
                         pkg_identity(mkpkg("a", commit="abc")))

    def test_different_commit_different_identity(self):
        self.assertNotEqual(pkg_identity(mkpkg("a", commit="abc")),
                            pkg_identity(mkpkg("a", commit="def")))

    def test_unresolved_falls_back_to_spec(self):
        # No resolved commit -> the url still distinguishes.
        self.assertNotEqual(
            pkg_identity(mkpkg("a", url="https://example.invalid/x.git")),
            pkg_identity(mkpkg("a", url="https://example.invalid/y.git")))

    def test_context_is_not_identity(self):
        # resolved_by / dep_set describe how a package was reached, not which
        # package it is; two reachings of the same package must match.
        p1 = mkpkg("a", commit="abc")
        p1.resolved_by, p1.dep_set = "toolB", "default"
        p2 = mkpkg("a", commit="abc")
        p2.resolved_by, p2.dep_set = "libC", "dev"
        self.assertEqual(pkg_identity(p1), pkg_identity(p2))


class TestCheckRecursion(unittest.TestCase):

    def _chain(self, *pkgs):
        """Build a nested scope chain owned by *pkgs*, return the leaf scope."""
        scope = root_scope(NESTED)
        for pkg in pkgs:
            scope = scope.open_child(pkg, "packages", NESTED)
        return scope

    def test_no_repeat_returns_none(self):
        scope = self._chain(mkpkg("toolB", commit="b1"))
        self.assertIsNone(check_recursion(mkpkg("libC", commit="c1"), scope))

    def test_direct_cycle_elides_at_the_ancestor(self):
        # A -> B -> A(same): descending again would repeat the sub-tree.
        a = mkpkg("libA", commit="a1")
        b = mkpkg("toolB", commit="b1")
        scope = self._chain(a, b)
        self.assertEqual(check_recursion(mkpkg("libA", commit="a1"), scope), "")

    def test_cycle_elides_at_a_non_root_ancestor(self):
        top = mkpkg("top", commit="t1")
        a = mkpkg("libA", commit="a1")
        b = mkpkg("toolB", commit="b1")
        scope = self._chain(top, a, b)
        self.assertEqual(
            check_recursion(mkpkg("libA", commit="a1"), scope), "top/packages")

    def test_same_name_different_version_is_not_a_cycle(self):
        # Coexistence: libA v2 under libA v1 is legitimate, not a repeat.
        a1 = mkpkg("libA", commit="a1")
        b = mkpkg("toolB", commit="b1")
        scope = self._chain(a1, b)
        self.assertIsNone(check_recursion(mkpkg("libA", commit="a2"), scope))

    def test_alternating_versions_elide_at_the_repeat(self):
        # A(v1) -> B -> A(v2) -> B -> A(v1): name-only keying would elide the
        # legitimate A(v2); identity keying elides only the true repeat.
        a1 = mkpkg("libA", commit="a1")
        b1 = mkpkg("toolB", commit="b1")
        a2 = mkpkg("libA", commit="a2")
        scope = self._chain(a1, b1, a2)
        # B repeats before A(v1) does -- elide at B's enclosing scope.
        self.assertEqual(
            check_recursion(mkpkg("toolB", commit="b1"), scope),
            "libA/packages")
        # And A(v1) itself repeats at the root.
        deeper = scope.open_child(mkpkg("toolB", commit="b1"), "packages", NESTED)
        self.assertEqual(check_recursion(mkpkg("libA", commit="a1"), deeper), "")

    def test_depth_cap_fires_on_endlessly_distinct_versions(self):
        pkgs = [mkpkg("libA", commit="a%d" % i) for i in range(6)]
        scope = self._chain(*pkgs)
        with self.assertRaises(SrcLoaderError) as ctx:
            check_recursion(mkpkg("libA", commit="a99"), scope, max_depth=6)
        text = str(ctx.exception)
        self.assertIn("depth limit", text)
        self.assertIn("IVPM_MAX_DEP_DEPTH", text)
        # The full chain is named, root-first.
        self.assertIn("libA -> libA", text)

    def test_repeat_wins_over_the_depth_cap(self):
        # A genuine repeat must elide, not raise, even at the cap.
        pkgs = [mkpkg("libA", commit="a%d" % i) for i in range(6)]
        scope = self._chain(*pkgs)
        self.assertEqual(
            check_recursion(mkpkg("libA", commit="a0"), scope, max_depth=6), "")

    def test_flat_workspace_never_recurses(self):
        # The root scope has no owner, so nothing can repeat.
        self.assertIsNone(check_recursion(mkpkg("libA"), root_scope()))


class TestMaxDepDepth(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.pop("IVPM_MAX_DEP_DEPTH", None)

    def tearDown(self):
        os.environ.pop("IVPM_MAX_DEP_DEPTH", None)
        if self._saved is not None:
            os.environ["IVPM_MAX_DEP_DEPTH"] = self._saved

    def test_default(self):
        self.assertEqual(max_dep_depth(), _DEFAULT_MAX_DEPTH)

    def test_override(self):
        os.environ["IVPM_MAX_DEP_DEPTH"] = "4"
        self.assertEqual(max_dep_depth(), 4)

    def test_non_integer_is_fatal(self):
        os.environ["IVPM_MAX_DEP_DEPTH"] = "deep"
        with self.assertRaises(SrcLoaderError):
            max_dep_depth()

    def test_zero_is_fatal(self):
        os.environ["IVPM_MAX_DEP_DEPTH"] = "0"
        with self.assertRaises(SrcLoaderError):
            max_dep_depth()
