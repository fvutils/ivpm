#****************************************************************************
#* test_perf_update_phases.py
#*
#* Integration tests for Phase-2 perf wiring: the global phase spans emitted by
#* ProjectOps.update(), and correct per-package span attribution. Uses local
#* ``src: dir`` packages so the test is hermetic (no network) -- see memory
#* flaky-network-integration-tests.md.
#****************************************************************************
import os
from .test_base import TestBase

from ivpm.project_ops import ProjectOps


class TestPerfUpdatePhases(TestBase):

    def _mk_dir_pkg(self, name):
        """Create a trivial leaf dir package (no ivpm.yaml -> no transitive
        deps) under the test dir and return its file:// url."""
        pkg_dir = os.path.join(self.testdir, "srcpkgs", name)
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "README.txt"), "w") as fp:
            fp.write("pkg %s\n" % name)
        return "file://" + pkg_dir

    def _run_update(self, names):
        deps = "\n".join(
            "                    - name: %s\n"
            "                      url: %s\n"
            "                      src: dir" % (n, self._mk_dir_pkg(n))
            for n in names)
        self.mkFile("ivpm.yaml",
            "package:\n"
            "    name: perf_root\n"
            "    dep-sets:\n"
            "        - name: default-dev\n"
            "          deps:\n"
            "%s\n" % deps)

        class Args:
            anonymous_git = None
        ops = ProjectOps(self.testdir, Args())
        ops.update(dep_set="default-dev", skip_venv=True, args=Args())
        return ops

    def _categories(self, perf):
        return [s.category for s in perf.spans]

    def test_global_phase_spans_present(self):
        ops = self._run_update(["alpha"])
        perf = ops._perf
        cats = self._categories(perf)
        # Global phases wired in Phase 2.
        for expected in ("update", "init", "depset.resolve", "fetch",
                         "handler.post_load", "lock.write"):
            self.assertIn(expected, cats,
                          "missing global phase span %r (have %r)"
                          % (expected, sorted(set(cats))))

    def test_single_root_and_no_orphans(self):
        ops = self._run_update(["alpha", "beta"])
        roots = ops._perf.to_tree()
        # Exactly one root: the "update" span.
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["span"].category, "update")

    def test_phases_nest_under_root(self):
        ops = self._run_update(["alpha"])
        root = ops._perf.to_tree()[0]
        child_cats = {n["span"].category for n in root["children"]}
        # init / depset.resolve / fetch / handler.* / lock.write hang off root.
        self.assertIn("fetch", child_cats)
        self.assertIn("init", child_cats)

    def test_per_package_spans_attributed(self):
        names = ["alpha", "beta", "gamma"]
        ops = self._run_update(names)
        pkg_spans = [s for s in ops._perf.spans if s.category == "fetch.pkg"]
        # One fetch.pkg span per package, correctly named, each independently
        # timed (non-None duration). This is the structural guard that the
        # per-package timer no longer shares a single slot.
        by_pkg = {}
        for s in pkg_spans:
            by_pkg.setdefault(s.package, []).append(s)
        self.assertEqual(set(by_pkg.keys()), set(names))
        for name in names:
            self.assertEqual(len(by_pkg[name]), 1,
                             "expected exactly one span for %s" % name)
            self.assertIsNotNone(by_pkg[name][0].duration)
            self.assertGreaterEqual(by_pkg[name][0].duration, 0.0)

    def test_fetch_pkg_spans_parent_onto_fetch(self):
        ops = self._run_update(["alpha", "beta"])
        spans = ops._perf.spans
        fetch = next(s for s in spans if s.category == "fetch")
        pkg_spans = [s for s in spans if s.category == "fetch.pkg"]
        self.assertTrue(pkg_spans)
        for s in pkg_spans:
            self.assertEqual(s.parent_id, fetch.span_id,
                             "fetch.pkg span for %s did not parent onto the "
                             "fetch phase" % s.package)
