"""Unit tests for the Python source-package install ordering."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.handlers.package_handler_python import (
    PackageHandlerPython, find_sccs, toposort_dep_groups)


class _ProjInfo(object):
    """Minimal stand-in for the ProjectInfo of a source package."""

    def __init__(self, name, deps):
        self.name = name
        self.target_dep_set = "default"
        self.dep_set_m = {"default": dict((d, None) for d in deps)}

    def has_dep_set(self, name):
        return name in self.dep_set_m

    def get_dep_set(self, name):
        return self.dep_set_m[name]


class _Pkg(object):
    def __init__(self, name, deps=None, src_type="git"):
        self.name = name
        self.src_type = src_type
        self.resolved_by = None
        self.proj_info = _ProjInfo(name, deps) if deps is not None else None


def _handler(src_pkgs, extra_pkgs=()):
    """Build a handler whose package tables describe ``src_pkgs``."""
    h = PackageHandlerPython()
    h.src_pkg_s = set(p.name for p in src_pkgs)
    h.pkgs_info = dict((p.name, p) for p in list(src_pkgs) + list(extra_pkgs))
    return h


def _index_of(groups, name):
    for i, g in enumerate(groups):
        if name in g:
            return i
    raise AssertionError("%s not present in %s" % (name, groups))


class TestFindSccs(unittest.TestCase):

    def test_acyclic_all_singletons(self):
        sccs = find_sccs({"a": {"b"}, "b": {"c"}, "c": set()})
        self.assertEqual(sorted(sccs, key=sorted), [{"a"}, {"b"}, {"c"}])

    def test_cycle_collapsed(self):
        sccs = find_sccs({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}})
        self.assertIn({"a", "b", "c"}, sccs)
        self.assertIn({"d"}, sccs)

    def test_edges_to_unknown_nodes_ignored(self):
        sccs = find_sccs({"a": {"pytest"}})
        self.assertEqual(sccs, [{"a"}])

    def test_deep_chain_does_not_recurse(self):
        # Far deeper than the interpreter recursion limit
        n = 5000
        deps_m = dict(("p%d" % i, {"p%d" % (i + 1)}) for i in range(n))
        deps_m["p%d" % n] = set()
        self.assertEqual(len(find_sccs(deps_m)), n + 1)


class TestToposortDepGroups(unittest.TestCase):

    def test_dependency_installed_first(self):
        groups = toposort_dep_groups({"app": {"lib"}, "lib": set()})
        self.assertEqual(groups, [{"lib"}, {"app"}])

    def test_cycle_members_share_a_group(self):
        # a <-> b are mutually dependent, so they must be resolved together
        groups = toposort_dep_groups({"a": {"b"}, "b": {"a"}, "c": {"a"}})
        self.assertEqual(groups, [{"a", "b"}, {"c"}])

    def test_self_edge_tolerated(self):
        groups = toposort_dep_groups({"a": {"a"}, "b": {"a"}})
        self.assertEqual(groups, [{"a"}, {"b"}])

    def test_empty(self):
        self.assertEqual(toposort_dep_groups({}), [])


class TestBuildPythonDepsM(unittest.TestCase):

    def test_edge_recorded_regardless_of_resolver(self):
        """A declared dependency orders the install even when the root
        project is what actually resolved the dependency.

        This is the zuspec-solver case: the root lists both packages, so
        neither dep is 'resolved_by' the package that declares it, but
        zuspec-be-bc still requires zuspec-solver at install time.
        """
        solver = _Pkg("zuspec-solver", deps=[])
        be_bc = _Pkg("zuspec-be-bc", deps=["zuspec-solver"])
        # Resolved at the root: resolved_by is left as None on both
        h = _handler([solver, be_bc])

        deps_m = h._build_python_deps_m()
        self.assertEqual(deps_m["zuspec-be-bc"], {"zuspec-solver"})

        groups = toposort_dep_groups(deps_m)
        self.assertLess(_index_of(groups, "zuspec-solver"),
                        _index_of(groups, "zuspec-be-bc"))

    def test_non_python_deps_excluded(self):
        """Deps that IVPM does not install into the venv are not edges."""
        app = _Pkg("app", deps=["uvm", "pytest"])
        h = _handler([app],
                     extra_pkgs=[_Pkg("uvm", src_type=".tar.gz"),
                                 _Pkg("pytest", src_type="pypi")])
        self.assertEqual(h._build_python_deps_m(), {"app": set()})

    def test_package_without_proj_info(self):
        h = _handler([_Pkg("app")])
        self.assertEqual(h._build_python_deps_m(), {"app": set()})

    def test_missing_target_dep_set(self):
        app = _Pkg("app", deps=[])
        app.proj_info.target_dep_set = "nonexistent"
        h = _handler([app])
        self.assertEqual(h._build_python_deps_m(), {"app": set()})

    def test_cyclic_declarations_do_not_raise(self):
        """A->B->C->A across sub-packages must not fail the install."""
        h = _handler([_Pkg("a", deps=["b"]),
                      _Pkg("b", deps=["c"]),
                      _Pkg("c", deps=["a"])])
        groups = toposort_dep_groups(h._build_python_deps_m())
        self.assertEqual(groups, [{"a", "b", "c"}])


if __name__ == "__main__":
    unittest.main()
