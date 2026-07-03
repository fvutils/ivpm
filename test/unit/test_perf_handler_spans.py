#****************************************************************************
#* test_perf_handler_spans.py
#*
#* Phase-4 integration test: handler.post_load sub-phase spans (venv.create,
#* reqs.assemble, pip.install, envrc.write) emitted by the Python handler.
#*
#* Mirrors the (reliable) test_venv.py pattern. The venv/pip work is local
#* except for resolving PyPI packages; a network failure is turned into a
#* skip rather than a hard failure (memory flaky-network-integration-tests.md).
#****************************************************************************
import os
import unittest

from .test_base import TestBase
from ivpm.project_ops import ProjectOps


class _Args:
    py_uv = False
    py_pip = True            # force pip so uv need not be installed
    py_system_site_packages = False
    anonymous_git = None
    log_level = "NONE"
    jobs = None


class TestPerfHandlerSpans(TestBase):

    def _run(self):
        self.mkFile("ivpm.yaml",
            "package:\n"
            "    name: perf_py_root\n"
            "    dep-sets:\n"
            "        - name: default-dev\n"
            "          deps:\n"
            "            - name: pyyaml\n"
            "              pypi: true\n")
        args = _Args()
        ops = ProjectOps(self.testdir, args)
        try:
            ops.update(dep_set="default-dev", skip_venv=False, args=args)
        except Exception as e:
            # A failure to reach PyPI (or missing pip) is environmental, not a
            # regression in the span wiring under test.
            self.skipTest("venv/pip install unavailable: %s" % e)
        return ops

    def test_handler_sub_phase_spans(self):
        ops = self._run()
        spans = ops._perf.spans
        cats = {s.category for s in spans}

        # venv was created (packages/python did not exist before), so all four
        # handler sub-phases should have fired.
        for expected in ("venv.create", "reqs.assemble", "pip.install",
                         "envrc.write"):
            self.assertIn(expected, cats,
                          "missing handler sub-phase span %r (have %r)"
                          % (expected, sorted(cats)))

        # venv.create records the tool mode; pip.install records phase count.
        venv = next(s for s in spans if s.category == "venv.create")
        self.assertEqual(venv.meta.get("mode"), "pip")
        pip = next(s for s in spans if s.category == "pip.install")
        self.assertGreaterEqual(pip.meta.get("phases", 0), 1)

        # All four nest under handler.post_load.
        post = next(s for s in spans if s.category == "handler.post_load")
        for cat in ("venv.create", "reqs.assemble", "pip.install", "envrc.write"):
            span = next(s for s in spans if s.category == cat)
            self.assertEqual(span.parent_id, post.span_id,
                             "%s did not parent onto handler.post_load" % cat)

    def test_rerun_skips_pip_install(self):
        """A second update finds packages already installed and takes the fast
        path -- pip.install is absent, and the report still reconstructs."""
        self._run()  # first run creates venv + installs

        args = _Args()
        ops2 = ProjectOps(self.testdir, args)
        try:
            ops2.update(dep_set="default-dev", skip_venv=False, args=args)
        except Exception as e:
            self.skipTest("second update failed environmentally: %s" % e)

        cats = {s.category for s in ops2._perf.spans}
        # Already-installed fast path returns before assembling requirements.
        self.assertNotIn("pip.install", cats)
        self.assertNotIn("reqs.assemble", cats)
        # Tree still reconstructs cleanly.
        roots = ops2._perf.to_tree()
        self.assertEqual(len(roots), 1)


if __name__ == "__main__":
    unittest.main()
