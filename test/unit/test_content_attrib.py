#****************************************************************************
#* test_content_attrib.py
#*
#* Attribution of a language-content install failure: which package caused it,
#* and which ivpm.yaml line imported that package.
#*
#* The invariant these tests protect is that attribution is *structural*.
#* Provenance is recorded where the installer input is emitted, and the import
#* chain is walked from links the resolver already set. Nothing here may come
#* to depend on the wording of an installer's output -- see
#* test_install_isolation.py, which asserts that directly.
#****************************************************************************
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.content_attrib import (
    ENROLLED_EXPLICIT, ENROLLED_PROBE, ENROLLED_PROVIDES, ENROLLED_SRC_TYPE,
    ContentOrigin, OriginMap, format_content_failure, get_enrollment,
    get_evidence, import_chain, loc_of, normalize_dist, render_chain,
    set_enrollment,
)
from ivpm.package import Package
from ivpm.handlers.package_handler_python import PackageHandlerPython
from ivpm.handlers.package_handler_node import PackageHandlerNode
from ivpm.pkg_content_type import NodeTypeData, PythonTypeData

from .test_base import TestBase


class _SrcInfo:
    """Stands in for the reader's source-info object."""
    def __init__(self, filename, lineno, linepos=1):
        self.filename = filename
        self.lineno = lineno
        self.linepos = linepos


def _pkg(name, scope_key=None, resolved_by_key=None, loc=None, **kw):
    p = Package(name)
    p.scope_key = scope_key or name
    p.resolved_by_key = resolved_by_key
    if loc is not None:
        p.srcinfo = _SrcInfo(*loc)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# ---------------------------------------------------------------------------
# Enrollment recording
# ---------------------------------------------------------------------------

class TestEnrollment(unittest.TestCase):

    def test_reason_and_evidence_round_trip(self):
        p = _pkg("foo")
        set_enrollment(p, "python", ENROLLED_PROBE, evidence="pyproject.toml")
        self.assertEqual(ENROLLED_PROBE, get_enrollment(p, "python"))
        self.assertEqual("pyproject.toml", get_evidence(p, "python"))

    def test_languages_are_independent(self):
        """A package can provide both, enrolled by different routes -- one
        field for the pair would have to lose one of the two answers."""
        p = _pkg("foo")
        set_enrollment(p, "python", ENROLLED_EXPLICIT)
        set_enrollment(p, "node", ENROLLED_PROBE, evidence="package.json")
        self.assertEqual(ENROLLED_EXPLICIT, get_enrollment(p, "python"))
        self.assertEqual(ENROLLED_PROBE, get_enrollment(p, "node"))

    def test_unrecorded_language_is_none(self):
        self.assertIsNone(get_enrollment(_pkg("foo"), "python"))
        self.assertIsNone(get_evidence(_pkg("foo"), "python"))

    def test_annotating_an_odd_object_does_not_raise(self):
        """Attribution degrades; it never becomes the reason a run fails."""
        class _Frozen:
            __slots__ = ()
        set_enrollment(_Frozen(), "python", ENROLLED_PROBE)


# ---------------------------------------------------------------------------
# OriginMap
# ---------------------------------------------------------------------------

class TestOriginMap(unittest.TestCase):

    def setUp(self):
        self.m = OriginMap()
        self.foo = _pkg("foo")
        self.bar = _pkg("bar")
        set_enrollment(self.foo, "python", ENROLLED_PROBE, evidence="setup.py")
        self.m.record("-e /w/packages/foo", self.foo, "g1",
                      language="python", dist="foo-dist")
        self.m.record("bar==1.0", self.bar, "g2", language="python", dist="bar")

    def test_enrollment_is_read_off_the_package(self):
        """Recorded where the line is written, from where the decision was
        made -- not threaded through by hand and liable to disagree."""
        o = self.m.by_spec("-e /w/packages/foo")
        self.assertEqual(ENROLLED_PROBE, o.enrolled_by)
        self.assertEqual("setup.py", o.evidence)

    def test_by_group_partitions(self):
        self.assertEqual(["foo"], [o.name for o in self.m.by_group("g1")])
        self.assertEqual(["bar"], [o.name for o in self.m.by_group("g2")])
        self.assertEqual([], self.m.by_group("nonexistent"))

    def test_resolve_dist_uses_the_declared_distribution_name(self):
        """A local tree's dist name need not match its directory name, and the
        installer only ever speaks the dist name."""
        self.assertIs(self.foo, self.m.resolve_dist("foo-dist").pkg)

    def test_resolve_dist_normalizes(self):
        self.assertIs(self.foo, self.m.resolve_dist("Foo_Dist").pkg)
        self.assertEqual("foo-dist", normalize_dist("Foo._Dist"))

    def test_resolve_dist_falls_back_to_the_package_name(self):
        self.assertIs(self.foo, self.m.resolve_dist("foo").pkg)

    def test_resolve_dist_is_scoped_by_group(self):
        self.assertIsNone(self.m.resolve_dist("bar", group="g1"))
        self.assertIsNotNone(self.m.resolve_dist("bar", group="g2"))

    def test_an_unknown_name_resolves_to_nothing(self):
        """An installer may name a transitive dependency nobody declared.
        Saying nothing is right; blaming the nearest match is not."""
        self.assertIsNone(self.m.resolve_dist("some-transitive-dep"))
        self.assertIsNone(self.m.resolve_dist(None))
        self.assertIsNone(self.m.resolve_dist(""))

    def test_missing_enrollment_defaults_to_probe(self):
        """The most cautious reading of an unrecorded decision: it drives the
        quietest severity and offers the escape hatch."""
        m = OriginMap()
        o = m.record("x", _pkg("x"), "g", language="python")
        self.assertEqual(ENROLLED_PROBE, o.enrolled_by)


# ---------------------------------------------------------------------------
# Import chain
# ---------------------------------------------------------------------------

class TestImportChain(unittest.TestCase):

    def test_flat_single_hop(self):
        foo = _pkg("foo", loc=("/w/ivpm.yaml", 42))
        chain = import_chain(foo, {"foo": foo})
        self.assertEqual(["foo"], [p.name for p in chain])
        self.assertIn("/w/ivpm.yaml:42:1", render_chain(chain))

    def test_nested_chain_is_ordered_root_to_leaf(self):
        bar = _pkg("bar", loc=("/w/ivpm.yaml", 42))
        foo = _pkg("foo", scope_key="bar/foo", resolved_by_key="bar",
                   loc=("/w/packages/bar/ivpm.yaml", 17))
        chain = import_chain(foo, {"bar": bar, "bar/foo": foo})
        self.assertEqual(["bar", "foo"], [p.name for p in chain])

        rendered = render_chain(chain).splitlines()
        self.assertIn("/w/ivpm.yaml:42:1", rendered[0])
        self.assertIn("bar", rendered[0])
        self.assertIn("/w/packages/bar/ivpm.yaml:17:1", rendered[1])
        self.assertIn("foo", rendered[1])

    def test_a_missing_ancestor_truncates_rather_than_raising(self):
        """A partial chain still tells the user something; an exception
        raised while reporting an error tells them nothing."""
        foo = _pkg("foo", resolved_by_key="gone")
        self.assertEqual(["foo"], [p.name for p in import_chain(foo, {})])

    def test_cycle_terminates(self):
        a = _pkg("a", resolved_by_key="b")
        b = _pkg("b", resolved_by_key="a")
        chain = import_chain(a, {"a": a, "b": b})
        self.assertLessEqual(len(chain), 3)
        self.assertIn("a", [p.name for p in chain])

    def test_depth_is_bounded(self):
        pkgs, prev = {}, None
        for i in range(200):
            p = _pkg("p%d" % i, resolved_by_key=prev)
            pkgs["p%d" % i] = p
            prev = "p%d" % i
        chain = import_chain(pkgs["p199"], pkgs, max_depth=10)
        self.assertLessEqual(len(chain), 11)

    def test_resolved_by_name_is_used_when_no_scope_key(self):
        """A flat workspace never sets resolved_by_key; the bare name is the
        only link there is."""
        bar = _pkg("bar")
        foo = Package("foo")
        foo.resolved_by = "bar"
        chain = import_chain(foo, {"bar": bar, "foo": foo})
        self.assertEqual(["bar", "foo"], [p.name for p in chain])


class TestRenderChain(unittest.TestCase):

    def test_a_package_without_a_location_renders_by_name(self):
        """An incomplete chain beats a missing one."""
        p = _pkg("root")
        self.assertIsNone(loc_of(p))
        self.assertIn("root", render_chain([p]))

    def test_srcinfo_without_a_filename_is_not_a_location(self):
        p = _pkg("p")
        p.srcinfo = _SrcInfo(None, 3)
        self.assertIsNone(loc_of(p))

    def test_missing_column_still_yields_file_and_line(self):
        p = _pkg("p")
        p.srcinfo = _SrcInfo("/w/ivpm.yaml", 7, None)
        self.assertEqual("/w/ivpm.yaml:7", loc_of(p))


# ---------------------------------------------------------------------------
# The rendered report
# ---------------------------------------------------------------------------

class TestFailureReport(unittest.TestCase):

    def _report(self, enrolled_by, evidence=None, **kw):
        bar = _pkg("bar", loc=("/w/ivpm.yaml", 42))
        foo = _pkg("foo", scope_key="bar/foo", resolved_by_key="bar",
                   loc=("/w/packages/bar/ivpm.yaml", 17))
        origin = ContentOrigin(pkg=foo, spec="-e /w/packages/foo",
                               group="python_pkgs_4.txt",
                               enrolled_by=enrolled_by, evidence=evidence)
        return format_content_failure(
            "python", [origin], {"bar": bar, "bar/foo": foo}, **kw)

    def test_report_names_the_package_and_the_import_chain(self):
        out = self._report(ENROLLED_PROBE, "pyproject.toml")
        self.assertIn("failed to install python content for package 'foo'", out)
        self.assertIn("/w/ivpm.yaml:42:1", out)
        self.assertIn("/w/packages/bar/ivpm.yaml:17:1", out)

    def test_probe_report_names_the_file_that_decided_it(self):
        """'IVPM thought this was a Python package' is unfalsifiable; 'IVPM
        found a pyproject.toml in it' is something the user can go and check."""
        self.assertIn("found pyproject.toml",
                      self._report(ENROLLED_PROBE, "pyproject.toml"))

    def test_probe_report_offers_the_escape_hatch_at_a_real_location(self):
        out = self._report(ENROLLED_PROBE, "pyproject.toml")
        self.assertIn("next step:", out)
        self.assertIn("type: raw", out)
        self.assertIn("/w/packages/bar/ivpm.yaml:17:1", out)

    def test_a_deliberate_declaration_is_not_told_to_retract_itself(self):
        """The user already said what they meant. The fix is in the package."""
        for reason in (ENROLLED_EXPLICIT, ENROLLED_PROVIDES, ENROLLED_SRC_TYPE):
            out = self._report(reason)
            self.assertNotIn("type: raw", out, reason)

    def test_each_enrollment_route_explains_itself_differently(self):
        seen = {self._report(r).split("was treated as")[1].splitlines()[0]
                for r in (ENROLLED_EXPLICIT, ENROLLED_PROVIDES,
                          ENROLLED_SRC_TYPE, ENROLLED_PROBE)}
        self.assertEqual(4, len(seen), seen)

    def test_installer_command_and_output_are_carried(self):
        out = self._report(ENROLLED_PROBE, "pyproject.toml",
                           installer="uv pip install -r reqs (exit 1)",
                           output="\nerror: missing header",
                           identified_by="a diagnostic re-run")
        self.assertIn("uv pip install -r reqs (exit 1)", out)
        self.assertIn("error: missing header", out)
        self.assertIn("identified by: a diagnostic re-run", out)

    def test_several_culprits_are_all_named(self):
        """When the failure cannot be reduced to one package, every
        contributor is reported -- picking one would be a guess."""
        a = _pkg("a", loc=("/w/ivpm.yaml", 1))
        b = _pkg("b", loc=("/w/ivpm.yaml", 2))
        origins = [ContentOrigin(pkg=p, spec=p.name, group="g",
                                 enrolled_by=ENROLLED_EXPLICIT) for p in (a, b)]
        out = format_content_failure("python", origins, {"a": a, "b": b})
        self.assertIn("contributed by 2 packages", out)
        self.assertIn("'a' imported by:", out)
        self.assertIn("'b' imported by:", out)

    def test_no_origins_still_produces_a_report(self):
        out = format_content_failure("node", [], {}, output="\nboom")
        self.assertIn("failed to install node content", out)
        self.assertIn("boom", out)


# ---------------------------------------------------------------------------
# Provenance completeness -- the handlers actually record what they emit
# ---------------------------------------------------------------------------

class TestPythonProvenanceCompleteness(TestBase):

    def _src_pkg(self, name, dist=None):
        path = os.path.join(self.testdir, "packages", name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "pyproject.toml"), "w") as fp:
            fp.write('[project]\nname = "%s"\nversion = "0.1"\n' % (dist or name))
        p = _pkg(name, loc=("/w/ivpm.yaml", 3), src_type="git", path=path)
        set_enrollment(p, "python", ENROLLED_PROBE, evidence="pyproject.toml")
        return p

    def _pypi_pkg(self, name, version=None):
        p = _pkg(name, loc=("/w/ivpm.yaml", 4), src_type="pypi", version=version)
        set_enrollment(p, "python", ENROLLED_SRC_TYPE)
        return p

    def test_every_emitted_line_resolves_to_a_package(self):
        """The completeness property: no line of a generated requirements file
        may be unattributable, or a failure on that line has no owner."""
        h = PackageHandlerPython()
        h.reset()
        pkgs = [self._src_pkg("foo", dist="foo-dist"),
                self._pypi_pkg("sphinx", "8.0"),
                self._pypi_pkg("pyyaml")]
        reqs = os.path.join(self.testdir, "python_pkgs_1.txt")
        h._write_requirements_txt(
            os.path.join(self.testdir, "packages"), pkgs, reqs)

        with open(reqs) as fp:
            written = [l.strip() for l in fp if l.strip()]

        self.assertEqual(3, len(written))
        for line in written:
            self.assertIsNotNone(h._origins.by_spec(line),
                                 "unattributed requirement: %s" % line)

    def test_a_source_package_is_keyed_by_its_declared_dist_name(self):
        """The directory is 'foo'; the installer will say 'foo-dist'."""
        h = PackageHandlerPython()
        h.reset()
        reqs = os.path.join(self.testdir, "python_pkgs_1.txt")
        h._write_requirements_txt(os.path.join(self.testdir, "packages"),
                                  [self._src_pkg("foo", dist="foo-dist")], reqs)
        self.assertEqual("foo", h._origins.resolve_dist("foo-dist").name)

    def test_pinned_and_unpinned_pypi_lines_are_both_recorded(self):
        h = PackageHandlerPython()
        h.reset()
        reqs = os.path.join(self.testdir, "python_pkgs_1.txt")
        h._write_requirements_txt(
            os.path.join(self.testdir, "packages"),
            [self._pypi_pkg("sphinx", "8.0"), self._pypi_pkg("pyyaml")], reqs)
        self.assertIsNotNone(h._origins.by_spec("sphinx==8.0"))
        self.assertIsNotNone(h._origins.by_spec("pyyaml"))

    def test_the_enrollment_reason_reaches_the_origin(self):
        h = PackageHandlerPython()
        h.reset()
        reqs = os.path.join(self.testdir, "python_pkgs_1.txt")
        h._write_requirements_txt(
            os.path.join(self.testdir, "packages"),
            [self._src_pkg("foo"), self._pypi_pkg("sphinx")], reqs)
        by_name = {o.name: o for o in h._origins.all()}
        self.assertEqual(ENROLLED_PROBE, by_name["foo"].enrolled_by)
        self.assertEqual("pyproject.toml", by_name["foo"].evidence)
        self.assertEqual(ENROLLED_SRC_TYPE, by_name["sphinx"].enrolled_by)


class TestNodeProvenanceCompleteness(TestBase):

    def _node_src_pkg(self, name, pkg_json_name):
        path = os.path.join(self.testdir, "packages", name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "package.json"), "w") as fp:
            json.dump({"name": pkg_json_name, "version": "1.0.0"}, fp)
        p = _pkg(name, loc=("/w/ivpm.yaml", 9), src_type="git", path=path)
        set_enrollment(p, "node", ENROLLED_PROBE, evidence="package.json")
        return p

    def test_every_generated_dependency_resolves_to_a_package(self):
        from ivpm.pkg_types.package_npm import PackageNpm

        h = PackageHandlerNode()
        h.reset()

        lodash = PackageNpm("lodash")
        lodash.src_type = "npm"
        lodash.version = "^4.0.0"
        lodash.dev = False
        set_enrollment(lodash, "node", ENROLLED_SRC_TYPE)
        h._npm_pkgs["lodash"] = lodash

        src = self._node_src_pkg("mylib", "@scope/mylib")
        h._source_pkgs["mylib"] = (src, NodeTypeData(dev=False, link=True))

        node_dir = os.path.join(self.testdir, "packages", "node")
        os.makedirs(node_dir, exist_ok=True)
        path = os.path.join(node_dir, "package.json")
        h._write_package_json(path, node_dir)

        with open(path) as fp:
            data = json.load(fp)

        emitted = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
        self.assertEqual({"lodash", "@scope/mylib"}, emitted)
        for dep_name in emitted:
            self.assertIsNotNone(h._origins.resolve_dist(dep_name),
                                 "unattributed dependency: %s" % dep_name)

    def test_a_source_package_is_keyed_by_its_package_json_name(self):
        """npm will speak the manifest's name, not the directory's."""
        h = PackageHandlerNode()
        h.reset()
        src = self._node_src_pkg("mylib", "@scope/mylib")
        h._source_pkgs["mylib"] = (src, NodeTypeData(dev=False, link=True))
        node_dir = os.path.join(self.testdir, "packages", "node")
        os.makedirs(node_dir, exist_ok=True)
        h._write_package_json(os.path.join(node_dir, "package.json"), node_dir)
        self.assertEqual("mylib", h._origins.resolve_dist("@scope/mylib").name)


# ---------------------------------------------------------------------------
# The documented example has to be the real one
# ---------------------------------------------------------------------------

class TestDocumentedFormat(unittest.TestCase):
    """docs/source/troubleshooting.rst walks a user through a worked example
    of this report, line by line. A documentation example that has drifted
    from the real output is worse than none: it teaches the reader to look for
    something that is not there."""

    DOC = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "docs", "source", "troubleshooting.rst")

    def _rendered(self):
        bar = _pkg("bar", loc=("ivpm.yaml", 42))
        foo = _pkg("foo", scope_key="bar/foo", resolved_by_key="bar",
                   loc=("packages/bar/ivpm.yaml", 17))
        origin = ContentOrigin(pkg=foo, spec="-e packages/foo",
                               group="python_pkgs_4.txt",
                               enrolled_by=ENROLLED_PROBE,
                               evidence="pyproject.toml")
        return format_content_failure(
            "python", [origin], {"bar": bar, "bar/foo": foo},
            group="packages/python_pkgs_4.txt",
            installer="uv pip install -r packages/python_pkgs_4.txt (exit 2)",
            identified_by="a diagnostic re-run of 3 packages, installed one "
                          "at a time")

    def test_every_rendered_line_appears_in_the_docs(self):
        with open(self.DOC) as fp:
            doc = fp.read()
        for line in self._rendered().splitlines():
            if not line.strip():
                continue
            self.assertIn(line.strip(), doc,
                          "troubleshooting.rst does not match the real "
                          "output; this line is missing:\n  %s" % line.strip())

    def test_the_documented_headings_are_the_real_ones(self):
        out = self._rendered()
        for heading in ("imported by:", "installer input:", "installer:",
                        "identified by:", "next step:"):
            self.assertIn(heading, out)


if __name__ == "__main__":
    unittest.main()
