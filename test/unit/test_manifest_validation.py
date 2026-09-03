#****************************************************************************
#* test_manifest_validation.py
#*
#* The probe gates: does a package's manifest parse, and is it something an
#* installer can install from?
#*
#* These gates are a frequency reduction, never a safety net. A manifest can
#* pass every check here and still fail to install, which is what
#* test_install_isolation.py covers. The property worth protecting in *this*
#* file is the other direction: a package that installs cleanly today must
#* keep installing cleanly. A gate that rejects working packages is strictly
#* worse than no gate at all.
#****************************************************************************
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm import msg
from ivpm.content_attrib import (
    ENROLLED_EXPLICIT, ENROLLED_PROBE, ENROLLED_PROVIDES, ENROLLED_SRC_TYPE,
    probe_allowed, report_manifest_problem, set_enrollment,
)
from ivpm.diagnostics import (
    CollectingSink, DiagnosticReporter, Severity, SrcLoaderError,
)
from ivpm.manifest_check import (
    ManifestDiag, check_node_manifest, check_python_manifest,
)
from ivpm.package import Package

from .test_base import TestBase


class _SrcInfo:
    def __init__(self, filename, lineno, linepos=1):
        self.filename = filename
        self.lineno = lineno
        self.linepos = linepos


class _ManifestBase(TestBase):

    def mkpkg(self, name, files):
        """Create a package directory containing *files* (name -> content)."""
        path = os.path.join(self.testdir, "packages", name)
        os.makedirs(path, exist_ok=True)
        for fname, content in files.items():
            with open(os.path.join(path, fname), "w") as fp:
                fp.write(content)
        return path


# ---------------------------------------------------------------------------
# Python gates
# ---------------------------------------------------------------------------

class TestPythonManifestGate(_ManifestBase):

    def test_no_python_metadata_claims_nothing(self):
        """None is not a rejection -- it means this handler has no opinion."""
        self.assertIsNone(check_python_manifest(
            self.mkpkg("plain", {"README.md": "hi"})))

    def test_a_missing_directory_claims_nothing(self):
        self.assertIsNone(check_python_manifest("/no/such/dir"))
        self.assertIsNone(check_python_manifest(None))

    def test_a_project_table_with_a_name_is_installable(self):
        d = check_python_manifest(self.mkpkg("p", {
            "pyproject.toml": '[project]\nname = "p"\nversion = "1.0"\n'}))
        self.assertTrue(d.ok)

    def test_tool_config_only_pyproject_is_not_an_install_target(self):
        """The case that makes auto-detection wrong so often: a valid file
        that is a linter config, not a package."""
        d = check_python_manifest(self.mkpkg("cfg", {
            "pyproject.toml": '[tool.ruff]\nline-length = 100\n'}))
        self.assertFalse(d.ok)
        self.assertFalse(d.malformed)
        self.assertIn("no '[project] name'", d.reason)
        self.assertIn("tool", d.reason, "say what the file does contain")

    def test_a_setuptools_project_without_a_project_table_is_installable(self):
        """build-system + setup.cfg metadata is a completely ordinary
        setuptools layout, and rejecting it would break working packages."""
        d = check_python_manifest(self.mkpkg("st", {
            "pyproject.toml": '[build-system]\nrequires = ["setuptools"]\n',
            "setup.cfg": "[metadata]\nname = st\n"}))
        self.assertTrue(d.ok)

    def test_setup_py_alone_is_accepted_unvalidated(self):
        """A setup.py cannot be validated without executing it, and executing
        an arbitrary dependency's setup.py to decide whether to install it is
        not a trade worth making. Documented limit."""
        d = check_python_manifest(self.mkpkg("legacy", {
            "setup.py": "from setuptools import setup\nsetup()\n"}))
        self.assertTrue(d.ok)

    def test_setup_py_rescues_a_pyproject_with_no_project_table(self):
        d = check_python_manifest(self.mkpkg("mixed", {
            "pyproject.toml": '[tool.black]\nline-length = 88\n',
            "setup.py": "from setuptools import setup\nsetup()\n"}))
        self.assertTrue(d.ok)

    def test_setup_cfg_without_a_name_is_not_an_install_target(self):
        d = check_python_manifest(self.mkpkg("nocfgname", {
            "setup.cfg": "[flake8]\nmax-line-length = 100\n"}))
        self.assertFalse(d.ok)
        self.assertFalse(d.malformed)

    def test_broken_toml_is_malformed_and_carries_line_and_column(self):
        """A parse error the user cannot navigate to is barely better than
        no parse error."""
        d = check_python_manifest(self.mkpkg("broken", {
            "pyproject.toml": '[project]\nname = "p"\nversion = \n'}))
        self.assertFalse(d.ok)
        self.assertTrue(d.malformed)
        self.assertIsNotNone(d.line, d.reason)
        self.assertIn("pyproject.toml:%d" % d.line, d.located())

    def test_located_degrades_when_the_parser_gave_no_position(self):
        self.assertEqual("/a/b", ManifestDiag(False, "/a/b", "x").located())
        self.assertEqual("/a/b:3", ManifestDiag(False, "/a/b", "x", 3).located())
        self.assertEqual("/a/b:3:7",
                         ManifestDiag(False, "/a/b", "x", 3, 7).located())


# ---------------------------------------------------------------------------
# Node gates
# ---------------------------------------------------------------------------

class TestNodeManifestGate(_ManifestBase):

    def test_no_package_json_claims_nothing(self):
        self.assertIsNone(check_node_manifest(
            self.mkpkg("plain", {"README.md": "hi"})))

    def test_a_named_and_versioned_package_is_installable(self):
        d = check_node_manifest(self.mkpkg("n", {
            "package.json": json.dumps({"name": "n", "version": "1.0.0"})}))
        self.assertTrue(d.ok)

    def test_truncated_json_is_malformed_with_line_and_column(self):
        d = check_node_manifest(self.mkpkg("trunc", {
            "package.json": '{\n  "name": "x",\n  "version"\n'}))
        self.assertFalse(d.ok)
        self.assertTrue(d.malformed)
        self.assertIsNotNone(d.line)
        self.assertIsNotNone(d.col)
        self.assertIn(":%d:%d" % (d.line, d.col), d.located())

    def test_a_workspace_root_is_not_a_dependency(self):
        """npm cannot install a workspace root as a dependency; enrolling one
        produces a failure with no obvious cause."""
        d = check_node_manifest(self.mkpkg("ws", {
            "package.json": json.dumps({
                "name": "monorepo", "version": "1.0.0",
                "workspaces": ["packages/*"]})}))
        self.assertFalse(d.ok)
        self.assertFalse(d.malformed)
        self.assertIn("workspace root", d.reason)

    def test_a_nameless_package_json_is_not_an_install_target(self):
        d = check_node_manifest(self.mkpkg("devonly", {
            "package.json": json.dumps({"devDependencies": {"jest": "^29"}})}))
        self.assertFalse(d.ok)
        self.assertFalse(d.malformed)
        self.assertIn("no 'name'", d.reason)

    def test_a_versionless_package_json_is_not_an_install_target(self):
        d = check_node_manifest(self.mkpkg("nover", {
            "package.json": json.dumps({"name": "x"})}))
        self.assertFalse(d.ok)
        self.assertIn("version", d.reason)

    def test_valid_json_that_is_not_an_object_is_malformed(self):
        d = check_node_manifest(self.mkpkg("arr", {"package.json": "[1,2,3]"}))
        self.assertFalse(d.ok)
        self.assertTrue(d.malformed)

    def test_a_private_package_with_a_name_is_still_installable(self):
        """private: true is not a workspace root and npm installs it fine
        from a file: spec. Rejecting it would break working setups."""
        d = check_node_manifest(self.mkpkg("priv", {
            "package.json": json.dumps({
                "name": "p", "version": "1.0.0", "private": True})}))
        self.assertTrue(d.ok)


# ---------------------------------------------------------------------------
# Severity follows authority
# ---------------------------------------------------------------------------

class TestSeverityTable(TestBase):

    def setUp(self):
        super().setUp()
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)
        return super().tearDown()

    def _pkg(self, enrolled_by):
        p = Package("foo")
        p.srcinfo = _SrcInfo("/w/ivpm.yaml", 42)
        set_enrollment(p, "python", enrolled_by)
        return p

    def _diag(self, malformed=False):
        return ManifestDiag(ok=False, path="/w/packages/foo/pyproject.toml",
                            reason="declares no '[project] name'",
                            line=1, malformed=malformed)

    def _severities(self):
        return [d.severity for d in self._sink.records]

    def test_explicit_is_fatal_at_the_dependency_entry(self):
        """The user asked for this package to be installed as python content
        and it cannot be. Carrying on would silently do nothing."""
        with self.assertRaises(SrcLoaderError):
            report_manifest_problem(self._pkg(ENROLLED_EXPLICIT),
                                    self._diag(), "python")
        rec = self._sink.records[-1]
        self.assertGreaterEqual(rec.severity, Severity.ERROR)
        self.assertIsNotNone(rec.srcinfo)
        self.assertEqual(42, rec.srcinfo.lineno)

    def test_src_type_is_fatal(self):
        with self.assertRaises(SrcLoaderError):
            report_manifest_problem(self._pkg(ENROLLED_SRC_TYPE),
                                    self._diag(), "python")

    def test_provides_is_fatal_and_names_the_provider(self):
        """The package claimed to provide content it cannot deliver. The
        importer is not at fault and cannot fix it."""
        pkg = self._pkg(ENROLLED_PROVIDES)
        pkg.path = os.path.join(self.testdir, "packages", "foo")
        os.makedirs(pkg.path, exist_ok=True)
        with open(os.path.join(pkg.path, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n  name: foo\n")

        with self.assertRaises(SrcLoaderError):
            report_manifest_problem(pkg, self._diag(), "python")
        text = self._sink.records[-1].message
        self.assertIn("declares that it provides", text)
        self.assertIn(os.path.join(pkg.path, "ivpm.yaml"), text)

    def test_a_probe_declining_a_valid_non_target_is_only_a_note(self):
        """The steady state in any workspace with foreign dependencies. A
        warning per dependency on every update is noise, and noise is what
        teaches users to skim the warnings that matter."""
        enrolled = report_manifest_problem(self._pkg(ENROLLED_PROBE),
                                           self._diag(), "python")
        self.assertFalse(enrolled)
        self.assertEqual([Severity.NOTE], self._severities())

    def test_a_probe_declining_a_malformed_manifest_warns(self):
        """Rare, and a file on disk is genuinely broken. Silence here
        reproduces the complaint this work exists to fix."""
        report_manifest_problem(self._pkg(ENROLLED_PROBE),
                                self._diag(malformed=True), "python")
        self.assertEqual([Severity.WARNING], self._severities())

    def test_strict_escalates_the_note_but_not_past_the_warning(self):
        report_manifest_problem(self._pkg(ENROLLED_PROBE), self._diag(),
                                "python", strict=True)
        self.assertEqual([Severity.ERROR], self._severities())

    def test_an_unrecorded_enrollment_is_treated_as_a_probe(self):
        """The most cautious reading: quiet, and never fatal."""
        p = Package("foo")
        p.srcinfo = _SrcInfo("/w/ivpm.yaml", 42)
        report_manifest_problem(p, self._diag(), "python")
        self.assertEqual([Severity.NOTE], self._severities())

    def test_the_report_names_the_file_and_the_reason(self):
        report_manifest_problem(self._pkg(ENROLLED_PROBE), self._diag(),
                                "python")
        text = self._sink.records[-1].message
        self.assertIn("pyproject.toml:1", text)
        self.assertIn("no '[project] name'", text)
        self.assertIn("foo", text)


# ---------------------------------------------------------------------------
# The gates in the handlers
# ---------------------------------------------------------------------------

class TestProbeEnrollment(_ManifestBase):

    def setUp(self):
        super().setUp()
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        msg.set_reporter(self._prev)
        return super().tearDown()

    def _update_info(self):
        from unittest.mock import MagicMock
        from ivpm.project_ops_info import ProjectUpdateInfo
        args = MagicMock()
        args.strict = False
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        return ProjectUpdateInfo(args=args, deps_dir=deps_dir,
                                 project_dir=self.testdir)

    def _leaf(self, handler, name, files):
        path = self.mkpkg(name, files)
        pkg = Package(name)
        pkg.src_type = "git"
        pkg.pkg_type = None
        pkg.path = path
        pkg.srcinfo = _SrcInfo("/w/ivpm.yaml", 7)
        handler.on_leaf_post_load(pkg, self._update_info())
        return pkg

    def test_python_probe_enrolls_a_real_project(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        h = PackageHandlerPython()
        h.reset()
        self._leaf(h, "real", {
            "pyproject.toml": '[project]\nname = "real"\nversion = "1"\n'})
        self.assertIn("real", h.src_pkg_s)

    def test_python_probe_declines_a_tool_config_only_pyproject(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        h = PackageHandlerPython()
        h.reset()
        self._leaf(h, "cfg", {"pyproject.toml": '[tool.ruff]\nline-length = 1\n'})
        self.assertNotIn("cfg", h.src_pkg_s)
        self.assertEqual([Severity.NOTE],
                         [d.severity for d in self._sink.records])

    def test_node_probe_declines_a_workspace_root(self):
        from ivpm.handlers.package_handler_node import PackageHandlerNode
        h = PackageHandlerNode()
        h.reset()
        self._leaf(h, "ws", {"package.json": json.dumps({
            "name": "m", "version": "1.0.0", "workspaces": ["p/*"]})})
        self.assertNotIn("ws", h._source_pkgs)

    def test_node_probe_warns_on_a_malformed_package_json(self):
        from ivpm.handlers.package_handler_node import PackageHandlerNode
        h = PackageHandlerNode()
        h.reset()
        self._leaf(h, "bad", {"package.json": '{"name": '})
        self.assertNotIn("bad", h._source_pkgs)
        self.assertEqual([Severity.WARNING],
                         [d.severity for d in self._sink.records])

    def test_a_package_with_neither_manifest_is_silent(self):
        """A package that says nothing about either language must produce no
        diagnostics at all -- most packages are this."""
        from ivpm.handlers.package_handler_node import PackageHandlerNode
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        for cls in (PackageHandlerPython, PackageHandlerNode):
            h = cls()
            h.reset()
            self._leaf(h, "plain", {"README.md": "hi"})
        self.assertEqual([], self._sink.records)


class TestCrossLanguageDecoupling(_ManifestBase):
    """`pkg_type` is a single slot shared by every language handler, so gating
    the probe on it meant one language's declaration silently switched off
    another language's auto-detection."""

    def _update_info(self):
        from unittest.mock import MagicMock
        from ivpm.project_ops_info import ProjectUpdateInfo
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        return ProjectUpdateInfo(args=MagicMock(strict=False),
                                 deps_dir=deps_dir, project_dir=self.testdir)

    def test_type_python_does_not_suppress_node_detection(self):
        from ivpm.handlers.package_handler_node import PackageHandlerNode
        path = self.mkpkg("both", {
            "pyproject.toml": '[project]\nname = "both"\nversion = "1"\n',
            "package.json": json.dumps({"name": "both", "version": "1.0.0"})})

        pkg = Package("both")
        pkg.src_type = "git"
        pkg.path = path
        pkg.srcinfo = _SrcInfo("/w/ivpm.yaml", 3)
        # The python handler ran first and claimed the shared slot.
        pkg.pkg_type = "python"

        h = PackageHandlerNode()
        h.reset()
        h.on_leaf_post_load(pkg, self._update_info())
        self.assertIn("both", h._source_pkgs,
                      "type: python must not disable node detection")

    def test_an_already_decided_language_is_not_re_probed(self):
        pkg = Package("p")
        set_enrollment(pkg, "python", ENROLLED_EXPLICIT)
        self.assertFalse(probe_allowed(pkg, "python"))
        self.assertTrue(probe_allowed(pkg, "node"),
                        "deciding python says nothing about node")

    def test_type_raw_is_the_escape_hatch_for_every_language(self):
        """The one declaration that does stop a probe -- it is the user
        saying 'this is not a Python/Node package, stop guessing'."""
        from ivpm.pkg_content_type import RawContentType
        pkg = Package("p")
        pkg.type_data = [RawContentType().create_data({}, None)]
        self.assertFalse(probe_allowed(pkg, "python"))
        self.assertFalse(probe_allowed(pkg, "node"))

    def test_type_raw_at_the_dep_site_declines_node_enrollment(self):
        from ivpm.handlers.package_handler_node import PackageHandlerNode
        from ivpm.pkg_content_type import RawContentType
        path = self.mkpkg("raw", {"package.json": json.dumps(
            {"name": "raw", "version": "1.0.0"})})
        pkg = Package("raw")
        pkg.src_type = "git"
        pkg.path = path
        pkg.type_data = [RawContentType().create_data({}, None)]
        h = PackageHandlerNode()
        h.reset()
        h.on_leaf_post_load(pkg, self._update_info())
        self.assertNotIn("raw", h._source_pkgs)


if __name__ == "__main__":
    unittest.main()
