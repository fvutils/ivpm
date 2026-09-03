#****************************************************************************
#* test_install_isolation.py
#*
#* Narrowing a failed install phase to the package that caused it, by
#* re-installing its members one at a time.
#*
#* The property this module exists to protect: attribution never depends on
#* the installer wording its output the way IVPM expects. Isolation asks only
#* *which* package fails, never *why*, so no new failure mode can slip past it
#* and no reworded uv/npm message can break it. test_fast_path_independence
#* asserts exactly that, and is the most important test in the file -- if it
#* is ever deleted or weakened, the design's central guarantee is gone with it.
#****************************************************************************
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.content_attrib import (
    ENROLLED_EXPLICIT, ENROLLED_PROBE, ENROLLED_SRC_TYPE,
    ISO_CONFLICT, ISO_GROUP, ISO_SINGLE, ISO_SKIPPED, ISO_SUBSET,
    ContentOrigin, OriginMap, isolate, isolation_identified_by,
    isolation_note, max_isolate, set_enrollment,
)
from ivpm.diagnostics import SrcLoaderError
from ivpm.installer_run import InstallerResult
from ivpm.handlers.package_handler_python import PackageHandlerPython
from ivpm.handlers.package_handler_node import PackageHandlerNode
from ivpm.package import Package
from ivpm.pkg_content_type import NodeTypeData

from .test_base import TestBase


class _SrcInfo:
    def __init__(self, filename, lineno, linepos=1):
        self.filename = filename
        self.lineno = lineno
        self.linepos = linepos


def _origin(name, group="g", spec=None, enrolled_by=ENROLLED_EXPLICIT,
            loc=None, dist=None):
    p = Package(name)
    p.scope_key = name
    p.srcinfo = _SrcInfo(*(loc or ("/w/ivpm.yaml", 1)))
    return ContentOrigin(pkg=p, spec=spec or name, group=group,
                         enrolled_by=enrolled_by, dist=dist or name)


# ---------------------------------------------------------------------------
# The driver: one test per row of the outcome table
# ---------------------------------------------------------------------------

class TestIsolationOutcomes(unittest.TestCase):

    def _run(self, names, failing, limit=None):
        origins = [_origin(n) for n in names]
        seen = []

        def retry_one(o):
            seen.append(o.name)
            return o.name not in failing

        result = isolate(origins, retry_one, limit=limit)
        return result, seen

    def test_single_culprit_is_named(self):
        r, seen = self._run(["a", "b", "c"], {"b"})
        self.assertEqual(ISO_SINGLE, r.kind)
        self.assertEqual(["b"], [o.name for o in r.culprits])
        self.assertEqual(3, r.attempted)
        self.assertEqual(["a", "b", "c"], seen)

    def test_subset_names_every_failing_member(self):
        r, _ = self._run(["a", "b", "c"], {"a", "c"})
        self.assertEqual(ISO_SUBSET, r.kind)
        self.assertEqual(["a", "c"], [o.name for o in r.culprits])

    def test_group_blames_the_group_not_a_member(self):
        """A dependency cycle is installed as one phase. If every member fails
        alone, none of them is established as the culprit -- picking one would
        send the user to fix a package that may be fine."""
        r, _ = self._run(["a", "b"], {"a", "b"})
        self.assertEqual(ISO_GROUP, r.kind)
        self.assertEqual({"a", "b"}, {o.name for o in r.culprits})

    def test_conflict_when_each_member_is_fine_alone(self):
        """Two incompatible pins: neither package is at fault, their
        combination is. That is a different problem with a different fix."""
        r, _ = self._run(["a", "b"], set())
        self.assertEqual(ISO_CONFLICT, r.kind)
        self.assertEqual({"a", "b"}, {o.name for o in r.culprits})

    def test_oversized_phase_is_skipped_without_retrying(self):
        r, seen = self._run(["a", "b", "c"], {"b"}, limit=2)
        self.assertEqual(ISO_SKIPPED, r.kind)
        self.assertEqual(0, r.attempted)
        self.assertEqual([], seen, "a skipped isolation must not run anything")

    def test_a_single_member_needs_no_retry(self):
        r, seen = self._run(["a"], {"a"})
        self.assertEqual(ISO_SINGLE, r.kind)
        self.assertEqual([], seen)

    def test_an_unusable_retry_is_not_counted_as_evidence(self):
        """A diagnostic pass must never replace the failure it is
        diagnosing, nor invent a culprit out of its own malfunction."""
        origins = [_origin("a"), _origin("b")]

        def retry_one(o):
            if o.name == "a":
                raise OSError("installer vanished")
            return False

        r = isolate(origins, retry_one)
        self.assertEqual(ISO_SINGLE, r.kind)
        self.assertEqual(["b"], [o.name for o in r.culprits])

    def test_conclusive_only_for_single_and_subset(self):
        self.assertTrue(self._run(["a", "b"], {"a"})[0].conclusive)
        self.assertFalse(self._run(["a", "b"], {"a", "b"})[0].conclusive)
        self.assertFalse(self._run(["a", "b"], set())[0].conclusive)


class TestIsolationReporting(unittest.TestCase):

    def _result(self, names, failing, limit=None):
        return isolate([_origin(n) for n in names],
                       lambda o: o.name not in failing, limit=limit)

    def test_each_outcome_explains_itself_distinctly(self):
        texts = {
            isolation_identified_by(self._result(*args))
            for args in (
                (["a", "b", "c"], {"b"}),
                (["a", "b", "c"], {"a", "c"}),
                (["a", "b"], {"a", "b"}),
                (["a", "b"], set()),
            )
        }
        self.assertEqual(4, len(texts), texts)

    def test_a_conflict_is_described_as_a_conflict(self):
        text = isolation_identified_by(self._result(["a", "b"], set()))
        self.assertIn("conflict", text)

    def test_a_skipped_isolation_says_so(self):
        """A bound that reports itself looks like a bound. A silent one looks
        like a complete answer."""
        note = isolation_note(self._result(["a", "b", "c"], {"b"}, limit=2),
                              limit=2)
        self.assertIsNotNone(note)
        self.assertIn("IVPM_MAX_ISOLATE", note)

    def test_a_diagnostic_pass_discloses_that_it_ran(self):
        """It mutates the venv; a user inspecting it afterwards deserves to
        know why it looks the way it does."""
        note = isolation_note(self._result(["a", "b"], {"a"}))
        self.assertIn("diagnostic", note)

    def test_no_note_when_nothing_was_re_run(self):
        self.assertIsNone(isolation_note(self._result(["a"], {"a"})))


class TestMaxIsolate(unittest.TestCase):

    def _with_env(self, value):
        env = dict(os.environ)
        if value is None:
            env.pop("IVPM_MAX_ISOLATE", None)
        else:
            env["IVPM_MAX_ISOLATE"] = value
        with patch.dict(os.environ, env, clear=True):
            return max_isolate()

    def test_default(self):
        self.assertEqual(32, self._with_env(None))

    def test_override(self):
        self.assertEqual(4, self._with_env("4"))

    def test_garbage_falls_back_to_the_default(self):
        self.assertEqual(32, self._with_env("lots"))
        self.assertEqual(32, self._with_env("0"))


# ---------------------------------------------------------------------------
# The python handler, end to end through _report_install_failure
# ---------------------------------------------------------------------------

class TestPythonHandlerIsolation(TestBase):

    def _handler_with(self, names, group="python_pkgs_1.txt"):
        h = PackageHandlerPython()
        h.reset()
        for i, n in enumerate(names):
            p = Package(n)
            p.scope_key = n
            p.src_type = "git"
            p.srcinfo = _SrcInfo("/w/ivpm.yaml", 10 + i)
            set_enrollment(p, "python", ENROLLED_PROBE,
                           evidence="pyproject.toml")
            h._origins.record("-e /w/packages/%s" % n, p, group,
                              language="python", dist=n)
        return h

    def _report(self, h, lines, failing, group="python_pkgs_1.txt"):
        """Drive the failure path; return the reported message."""
        result = InstallerResult(returncode=1, lines=list(lines),
                                 cmd=["uv", "pip", "install"])

        def fake_run(cmd, **kw):
            # The isolation retry writes the single spec to a temp file and
            # passes it with -r; read it back to decide the verdict.
            path = cmd[cmd.index("-r") + 1]
            with open(path) as fp:
                spec = fp.read().strip()
            bad = any(("/%s" % n) in spec for n in failing)
            return InstallerResult(returncode=1 if bad else 0, lines=[], cmd=cmd)

        with patch("ivpm.handlers.package_handler_python.run_installer",
                   side_effect=fake_run):
            with patch("ivpm.handlers.package_handler_python.shutil.which",
                       return_value="/usr/bin/uv"):
                with self.assertRaises(SrcLoaderError) as ctx:
                    h._report_install_failure(
                        group, result, None,
                        python_dir="/venv", use_uv=True)
        return str(ctx.exception)

    def test_isolation_names_the_one_failing_package(self):
        h = self._handler_with(["alpha", "beta", "gamma"])
        out = self._report(h, ["error: something went wrong"], {"beta"})
        self.assertIn("for package 'beta'", out)
        self.assertIn("/w/ivpm.yaml:11", out)
        self.assertNotIn("for package 'alpha'", out)

    def test_fast_path_independence(self):
        """THE test for this design.

        With the installer-output parser returning nothing -- as it will the
        day uv rewords a message, and as it already does for most failures --
        attribution must still identify the culprit. If this ever fails, the
        guarantee has quietly reverted to "we can attribute the failures we
        anticipated", which is the thing the design exists to avoid.
        """
        h = self._handler_with(["alpha", "beta", "gamma"])
        with patch("ivpm.handlers.package_handler_python._installer_failed_dist",
                   return_value=None):
            out = self._report(h, ["× Failed to build `beta`"], {"beta"})
        self.assertIn("for package 'beta'", out)
        self.assertIn("diagnostic re-run", out)

    def test_fast_path_is_used_when_it_works(self):
        h = self._handler_with(["alpha", "beta", "gamma"])
        out = self._report(h, ["  × Failed to build `beta @ file:///w`"], set())
        self.assertIn("for package 'beta'", out)
        self.assertIn("named 'beta' in its output", out)

    def test_a_name_the_installer_invents_does_not_become_a_culprit(self):
        """uv may name a transitive dependency nobody declared. That name
        resolves to no origin, so the report falls through to isolation
        rather than blaming the nearest lexical match."""
        h = self._handler_with(["alpha", "beta", "gamma"])
        out = self._report(h, ["  × Failed to build `some-transitive-dep`"],
                           {"gamma"})
        self.assertIn("for package 'gamma'", out)

    def test_a_cycle_group_is_reported_as_a_group(self):
        h = self._handler_with(["alpha", "beta"])
        out = self._report(h, ["error: opaque"], {"alpha", "beta"})
        self.assertIn("contributed by 2 packages", out)
        self.assertIn("every one of them failed individually", out)

    def test_a_conflict_reports_both_import_points(self):
        h = self._handler_with(["alpha", "beta"])
        out = self._report(h, ["error: resolution impossible"], set())
        self.assertIn("conflict", out)
        self.assertIn("/w/ivpm.yaml:10", out)
        self.assertIn("/w/ivpm.yaml:11", out)

    def test_an_oversized_phase_reports_the_bound_it_hit(self):
        h = self._handler_with(["a", "b", "c"])
        with patch.dict(os.environ, {"IVPM_MAX_ISOLATE": "2"}):
            out = self._report(h, ["error: opaque"], {"b"})
        self.assertIn("IVPM_MAX_ISOLATE", out)
        self.assertIn("contributed by 3 packages", out)

    def test_a_phase_with_no_recorded_origins_still_reports(self):
        """The site-config ivpm spec is written by a path that records no
        provenance. An unattributable failure is still a reported failure."""
        h = PackageHandlerPython()
        h.reset()
        result = InstallerResult(returncode=1, lines=["error: boom"],
                                 cmd=["uv", "pip", "install"])
        with self.assertRaises(SrcLoaderError) as ctx:
            h._report_install_failure("python_pkgs_9.txt", result, None,
                                      python_dir="/venv", use_uv=True)
        self.assertIn("boom", str(ctx.exception))

    def test_retry_uses_no_deps_against_the_real_venv(self):
        """--dry-run would skip the build, which is where these failures are.
        Earlier phases are already installed, so --no-deps is safe."""
        h = self._handler_with(["alpha"])
        origin = h._origins.all()[0]
        seen = []

        def fake_run(cmd, **kw):
            seen.append(list(cmd))
            return InstallerResult(returncode=0, lines=[], cmd=cmd)

        with patch("ivpm.handlers.package_handler_python.run_installer",
                   side_effect=fake_run):
            with patch("ivpm.handlers.package_handler_python.shutil.which",
                       return_value="/usr/bin/uv"):
                self.assertTrue(
                    h._retry_one(origin, "/venv", True, False, None))

        self.assertIn("--no-deps", seen[0])
        self.assertNotIn("--dry-run", seen[0])

    def test_retry_cleans_up_its_temp_requirements_file(self):
        h = self._handler_with(["alpha"])
        origin = h._origins.all()[0]
        captured = []

        def fake_run(cmd, **kw):
            captured.append(cmd[cmd.index("-r") + 1])
            return InstallerResult(returncode=0, lines=[], cmd=cmd)

        with patch("ivpm.handlers.package_handler_python.run_installer",
                   side_effect=fake_run):
            with patch("ivpm.handlers.package_handler_python.shutil.which",
                       return_value="/usr/bin/uv"):
                h._retry_one(origin, "/venv", True, False, None)

        self.assertFalse(os.path.exists(captured[0]))


# ---------------------------------------------------------------------------
# The node handler
# ---------------------------------------------------------------------------

class TestNodeHandlerIsolation(TestBase):

    def _handler_with(self, names):
        from ivpm.pkg_types.package_npm import PackageNpm

        h = PackageHandlerNode()
        h.reset()
        self.node_dir = os.path.join(self.testdir, "packages", "node")
        os.makedirs(self.node_dir, exist_ok=True)

        for i, n in enumerate(names):
            pkg = PackageNpm(n)
            pkg.src_type = "npm"
            pkg.version = "^1.0.0"
            pkg.dev = False
            pkg.srcinfo = _SrcInfo("/w/ivpm.yaml", 20 + i)
            set_enrollment(pkg, "node", ENROLLED_SRC_TYPE)
            h._npm_pkgs[n] = pkg
        h._write_package_json(
            os.path.join(self.node_dir, "package.json"), self.node_dir)
        return h

    def _report(self, h, lines, failing):
        result = InstallerResult(returncode=1, lines=list(lines),
                                 cmd=["npm", "install"])
        self.scratch_dirs = []

        def fake_run(cmd, **kw):
            prefix = cmd[cmd.index("--prefix") + 1]
            self.scratch_dirs.append(prefix)
            with open(os.path.join(prefix, "package.json")) as fp:
                deps = json.load(fp)["dependencies"]
            bad = any(n in deps for n in failing)
            return InstallerResult(returncode=1 if bad else 0, lines=[], cmd=cmd)

        with patch("ivpm.handlers.package_handler_node.run_installer",
                   side_effect=fake_run):
            with self.assertRaises(SrcLoaderError) as ctx:
                h._report_install_failure(
                    ["npm", "install"], result, None, "npm", self.node_dir)
        return str(ctx.exception)

    def test_isolation_names_the_failing_dependency(self):
        h = self._handler_with(["left-pad", "lodash", "chalk"])
        out = self._report(h, ["npm error opaque"], {"lodash"})
        self.assertIn("for package 'lodash'", out)

    def test_fast_path_independence(self):
        """As on the python side: npm's wording must not be load-bearing."""
        h = self._handler_with(["left-pad", "lodash", "chalk"])
        with patch("ivpm.handlers.package_handler_node._installer_failed_dep",
                   return_value=None):
            out = self._report(
                h, ["npm error notarget No matching version found for lodash@^1"],
                {"lodash"})
        self.assertIn("for package 'lodash'", out)
        self.assertIn("diagnostic re-run", out)

    def test_fast_path_is_used_when_npm_names_the_dependency(self):
        h = self._handler_with(["left-pad", "lodash"])
        out = self._report(
            h, ["npm error notarget No matching version found for lodash@^99"],
            set())
        self.assertIn("for package 'lodash'", out)
        self.assertIn("npm named 'lodash'", out)

    def test_the_real_node_modules_is_not_touched(self):
        """The diagnostic pass must leave the environment the user is about to
        inspect exactly as the failure left it."""
        h = self._handler_with(["left-pad", "lodash"])
        self._report(h, ["npm error opaque"], {"lodash"})
        self.assertTrue(self.scratch_dirs)
        for prefix in self.scratch_dirs:
            self.assertNotEqual(os.path.realpath(prefix),
                                os.path.realpath(self.node_dir))

    def test_a_file_dep_is_retried_by_absolute_path(self):
        """The generated manifest keeps file: specs relative so the workspace
        stays relocatable. Resolved from a scratch directory, a relative path
        points nowhere, and every source package would look broken."""
        src_dir = os.path.join(self.testdir, "packages", "mylib")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "package.json"), "w") as fp:
            json.dump({"name": "mylib", "version": "1.0.0"}, fp)

        h = PackageHandlerNode()
        h.reset()
        node_dir = os.path.join(self.testdir, "packages", "node")
        os.makedirs(node_dir, exist_ok=True)

        pkg = Package("mylib")
        pkg.path = src_dir
        pkg.srcinfo = _SrcInfo("/w/ivpm.yaml", 5)
        h._source_pkgs["mylib"] = (pkg, NodeTypeData(dev=False, link=True))
        h._write_package_json(os.path.join(node_dir, "package.json"), node_dir)

        origin = h._origins.all()[0]
        self.assertTrue(origin.spec.startswith("mylib@file:"))
        self.assertNotIn(os.path.abspath(src_dir), origin.spec,
                         "the emitted manifest must stay relative")

        seen = {}

        def fake_run(cmd, **kw):
            prefix = cmd[cmd.index("--prefix") + 1]
            with open(os.path.join(prefix, "package.json")) as fp:
                seen.update(json.load(fp)["dependencies"])
            return InstallerResult(returncode=0, lines=[], cmd=cmd)

        with patch("ivpm.handlers.package_handler_node.run_installer",
                   side_effect=fake_run):
            h._retry_one(origin, "npm", node_dir)

        self.assertEqual("file:" + os.path.abspath(src_dir), seen["mylib"])


if __name__ == "__main__":
    unittest.main()
