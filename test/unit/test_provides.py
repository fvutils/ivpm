#****************************************************************************
#* test_provides.py
#*
#* 'provides:' -- a package declaring, in its own ivpm.yaml, what content it
#* carries for whoever imports it.
#*
#* Two properties carry most of the weight here:
#*
#*   - `provides: []` and an absent `provides:` are different things. Empty
#*     is "I provide nothing, stop probing me", which is the cheapest fix
#*     available to a package that keeps being mis-detected. Absent is "I
#*     said nothing", which leaves the probe running. Collapsing them --
#*     easily done by defaulting the field to {} -- silently removes the only
#*     way a package can opt out.
#*   - The merge is field-wise, not all-or-nothing. A consumer overriding one
#*     field must not discard everything else the provider declared.
#****************************************************************************
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.content_attrib import (
    ENROLLED_EXPLICIT, ENROLLED_PROVIDES, get_enrollment, probe_allowed,
)
from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.package import Package, get_type_data
from ivpm.package_updater import _merge_self_declared_types
from ivpm.pkg_content_type import NodeTypeData, PythonTypeData, RawTypeData

from .test_base import TestBase


def _read(yaml_text, path="ivpm.yaml"):
    import io
    return IvpmYamlReader().read(io.StringIO(yaml_text), path)


class _Pkg(Package):
    pass


def _pkg(name, proj_info, type_data=None):
    p = Package(name)
    p.proj_info = proj_info
    p.type_data = list(type_data or [])
    return p


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class TestProvidesReading(unittest.TestCase):

    def test_absent_provides_is_none(self):
        """None means 'did not say'. The probe still runs."""
        info = _read("package:\n  name: p\n")
        self.assertIsNone(info.provides)

    def test_empty_list_is_an_empty_list_not_none(self):
        """The single most valuable thing a package can say, and the easiest
        to lose to a `or {}` in the reader."""
        info = _read("package:\n  name: p\n  provides: []\n")
        self.assertEqual([], info.provides)
        self.assertIsNotNone(info.provides)

    def test_a_null_value_is_also_an_explicit_nothing(self):
        info = _read("package:\n  name: p\n  provides:\n")
        self.assertEqual([], info.provides)

    def test_short_form_list_of_names(self):
        info = _read("package:\n  name: p\n  provides: [python, node]\n")
        self.assertEqual(["python", "node"], [n for n, _ in info.provides])

    def test_a_bare_string_is_accepted(self):
        info = _read("package:\n  name: p\n  provides: python\n")
        self.assertEqual([("python", {})], info.provides)

    def test_payload_form_carries_its_options(self):
        info = _read(
            "package:\n"
            "  name: p\n"
            "  provides:\n"
            "    python:\n"
            "      editable: true\n"
            "      extras: [runtime]\n")
        self.assertEqual(1, len(info.provides))
        name, opts = info.provides[0]
        self.assertEqual("python", name)
        self.assertEqual(True, opts["editable"])
        self.assertEqual(["runtime"], list(opts["extras"]))

    def test_provides_and_type_are_independent_fields(self):
        info = _read("package:\n  name: p\n  type: python\n")
        self.assertEqual([("python", {})], info.self_types)
        self.assertIsNone(info.provides,
                          "a package-level 'type:' is not a 'provides:'")


# ---------------------------------------------------------------------------
# merge_over
# ---------------------------------------------------------------------------

class TestMergeOver(unittest.TestCase):

    def test_consumer_wins_per_field_provider_supplies_the_rest(self):
        """The point of a field-wise merge: overriding one setting must not
        silently discard everything else the provider declared."""
        provider = PythonTypeData(extras=["runtime"], editable=True)
        consumer = PythonTypeData(editable=False)
        merged = consumer.merge_over(provider)
        self.assertEqual(["runtime"], merged.extras)
        self.assertFalse(merged.editable)

    def test_an_unset_consumer_takes_everything_from_the_provider(self):
        provider = PythonTypeData(extras=["a"], editable=True)
        merged = PythonTypeData().merge_over(provider)
        self.assertEqual(["a"], merged.extras)
        self.assertTrue(merged.editable)

    def test_false_is_a_value_not_an_absence(self):
        """The reason every mergeable field has to default to None: with
        concrete defaults, 'unset' and 'set to the default' are the same
        value, and the provider's setting could never survive."""
        provider = NodeTypeData(dev=True, link=False)
        merged = NodeTypeData(dev=False).merge_over(provider)
        self.assertFalse(merged.dev, "an explicit False must win")
        self.assertFalse(merged.link, "and the rest still comes from provider")

    def test_the_merge_does_not_mutate_either_side(self):
        provider = PythonTypeData(extras=["a"])
        consumer = PythonTypeData(editable=False)
        consumer.merge_over(provider)
        self.assertIsNone(consumer.extras)
        self.assertIsNone(provider.editable)

    def test_merging_across_types_is_a_no_op(self):
        """Merging a python declaration onto a node one is a caller error,
        not something to paper over with a partial result."""
        consumer = PythonTypeData(editable=False)
        self.assertIs(consumer, consumer.merge_over(NodeTypeData(dev=True)))
        self.assertIs(consumer, consumer.merge_over(None))

    def test_self_declared_survives_only_if_both_sides_are(self):
        """A consumer that said anything makes the result the consumer's
        claim, and a diagnostic must then point at the dependency entry."""
        provider = PythonTypeData(extras=["a"])
        provider.self_declared = True
        merged = PythonTypeData(editable=False).merge_over(provider)
        self.assertFalse(merged.self_declared)

    def test_type_name_is_preserved(self):
        provider = PythonTypeData(extras=["a"])
        provider.type_name = "python"
        merged = PythonTypeData().merge_over(provider)
        self.assertEqual("python", merged.type_name)


class TestNodeResolved(unittest.TestCase):

    def test_defaults_are_applied_only_at_resolution(self):
        td = NodeTypeData()
        self.assertIsNone(td.dev)
        self.assertIsNone(td.link)
        r = td.resolved()
        self.assertFalse(r.dev)
        self.assertTrue(r.link)

    def test_explicit_values_survive_resolution(self):
        r = NodeTypeData(dev=True, link=False).resolved()
        self.assertTrue(r.dev)
        self.assertFalse(r.link)

    def test_resolving_does_not_mutate_the_original(self):
        td = NodeTypeData()
        td.resolved()
        self.assertIsNone(td.link)


# ---------------------------------------------------------------------------
# Precedence, end to end through the merge the updater performs
# ---------------------------------------------------------------------------

class TestPrecedence(unittest.TestCase):

    def _merged(self, provider_yaml, consumer_types=()):
        pkg = _pkg("dep", _read(provider_yaml), consumer_types)
        _merge_self_declared_types(pkg)
        return pkg

    def test_a_provider_declaration_reaches_an_unadorned_import(self):
        pkg = self._merged(
            "package:\n  name: dep\n  provides:\n    python:\n"
            "      extras: [runtime]\n")
        td = get_type_data(pkg, PythonTypeData)
        self.assertIsNotNone(td)
        self.assertEqual(["runtime"], td.extras)
        self.assertTrue(td.self_declared)

    def test_provider_extras_plus_consumer_editable_yields_both(self):
        """The headline case for field-wise merge."""
        consumer = PythonTypeData(editable=False)
        consumer.type_name = "python"
        pkg = self._merged(
            "package:\n  name: dep\n  provides:\n    python:\n"
            "      extras: [runtime]\n",
            [consumer])
        td = get_type_data(pkg, PythonTypeData)
        self.assertEqual(["runtime"], td.extras)
        self.assertFalse(td.editable)

    def test_a_consumer_override_wins(self):
        consumer = PythonTypeData(extras=["mine"])
        consumer.type_name = "python"
        pkg = self._merged(
            "package:\n  name: dep\n  provides:\n    python:\n"
            "      extras: [theirs]\n",
            [consumer])
        self.assertEqual(["mine"], get_type_data(pkg, PythonTypeData).extras)

    def test_a_merged_result_is_the_consumers_claim(self):
        """Authority follows whoever last said something: a diagnostic goes
        to the dependency entry, not to the provider's file."""
        consumer = PythonTypeData(editable=False)
        consumer.type_name = "python"
        pkg = self._merged(
            "package:\n  name: dep\n  provides: [python]\n", [consumer])
        self.assertFalse(get_type_data(pkg, PythonTypeData).self_declared)

    def test_a_package_level_type_is_still_honoured(self):
        """The older spelling keeps working."""
        pkg = self._merged("package:\n  name: dep\n  type: python\n")
        self.assertIsNotNone(get_type_data(pkg, PythonTypeData))

    def test_provides_of_two_languages_produces_two_type_data(self):
        pkg = self._merged(
            "package:\n  name: dep\n  provides: [python, node]\n")
        self.assertIsNotNone(get_type_data(pkg, PythonTypeData))
        self.assertIsNotNone(get_type_data(pkg, NodeTypeData))


# ---------------------------------------------------------------------------
# provides: [] suppresses probing; absent does not
# ---------------------------------------------------------------------------

class TestProvidesGatesTheProbe(unittest.TestCase):

    def _pkg_for(self, yaml_text):
        pkg = _pkg("dep", _read(yaml_text))
        _merge_self_declared_types(pkg)
        return pkg

    def test_empty_provides_stops_every_probe(self):
        pkg = self._pkg_for("package:\n  name: dep\n  provides: []\n")
        self.assertEqual(set(), pkg.provides_declared)
        self.assertFalse(probe_allowed(pkg, "python"))
        self.assertFalse(probe_allowed(pkg, "node"))

    def test_absent_provides_leaves_the_probe_running(self):
        pkg = self._pkg_for("package:\n  name: dep\n")
        self.assertIsNone(pkg.provides_declared)
        self.assertTrue(probe_allowed(pkg, "python"))
        self.assertTrue(probe_allowed(pkg, "node"))

    def test_declaring_one_language_declines_the_other(self):
        """Having said what it provides, a package has said what it does
        not."""
        pkg = self._pkg_for("package:\n  name: dep\n  provides: [python]\n")
        self.assertFalse(probe_allowed(pkg, "node"))

    def test_a_package_level_type_does_not_suppress_probing(self):
        """'type:' says what this package is; it makes no claim about what it
        is not, so it cannot be read as an opt-out."""
        pkg = self._pkg_for("package:\n  name: dep\n  type: python\n")
        self.assertIsNone(pkg.provides_declared)
        self.assertTrue(probe_allowed(pkg, "node"))

    def test_consumer_type_raw_beats_a_providers_claim(self):
        """A consumer negative always wins: the importer is entitled to say
        'not in my workspace' about a package that claims otherwise."""
        raw = RawTypeData()
        raw.type_name = "raw"
        pkg = _pkg("dep", _read("package:\n  name: dep\n  provides: [python]\n"),
                   [raw])
        _merge_self_declared_types(pkg)
        self.assertFalse(probe_allowed(pkg, "python"))


# ---------------------------------------------------------------------------
# Enrollment attribution
# ---------------------------------------------------------------------------

class TestProvidesEnrollment(TestBase):

    def _update_info(self):
        from unittest.mock import MagicMock
        from ivpm.project_ops_info import ProjectUpdateInfo
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        return ProjectUpdateInfo(args=MagicMock(strict=False),
                                 deps_dir=deps_dir, project_dir=self.testdir)

    def _leaf(self, handler, pkg):
        handler.on_leaf_post_load(pkg, self._update_info())

    def test_a_self_declared_type_enrolls_as_provides_not_explicit(self):
        """Which one decides where a later failure is located: a provider's
        mistake belongs at the provider's file."""
        from ivpm.handlers.package_handler_python import PackageHandlerPython

        pkg = _pkg("dep", _read("package:\n  name: dep\n  provides: [python]\n"))
        pkg.src_type = "git"
        pkg.path = os.path.join(self.testdir, "packages", "dep")
        os.makedirs(pkg.path, exist_ok=True)
        _merge_self_declared_types(pkg)

        h = PackageHandlerPython()
        h.reset()
        self._leaf(h, pkg)

        self.assertIn("dep", h.src_pkg_s)
        self.assertEqual(ENROLLED_PROVIDES, get_enrollment(pkg, "python"))

    def test_a_dep_site_type_enrolls_as_explicit(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython

        td = PythonTypeData()
        td.type_name = "python"
        pkg = _pkg("dep", _read("package:\n  name: dep\n"), [td])
        pkg.src_type = "git"
        pkg.path = os.path.join(self.testdir, "packages", "dep")
        os.makedirs(pkg.path, exist_ok=True)
        _merge_self_declared_types(pkg)

        h = PackageHandlerPython()
        h.reset()
        self._leaf(h, pkg)
        self.assertEqual(ENROLLED_EXPLICIT, get_enrollment(pkg, "python"))


# ---------------------------------------------------------------------------
# The adoption note: keeping the probe fallthrough a migration state
# ---------------------------------------------------------------------------

class TestAdoptionNote(TestBase):
    """A package that ships an ivpm.yaml can say what it provides in one line.
    When one is instead enrolled by guesswork, say so -- quietly, and only to
    someone who can act on it."""

    def setUp(self):
        super().setUp()
        from ivpm import msg
        from ivpm.diagnostics import CollectingSink, DiagnosticReporter
        self._sink = CollectingSink()
        self._prev = msg.set_reporter(DiagnosticReporter(self._sink))

    def tearDown(self):
        from ivpm import msg
        msg.set_reporter(self._prev)
        return super().tearDown()

    def _pkg(self, name, with_ivpm_yaml, provides=None):
        path = os.path.join(self.testdir, "packages", name)
        os.makedirs(path, exist_ok=True)
        if with_ivpm_yaml:
            body = "package:\n  name: %s\n" % name
            if provides is not None:
                body += "  provides: [%s]\n" % ", ".join(provides)
            with open(os.path.join(path, "ivpm.yaml"), "w") as fp:
                fp.write(body)
        pkg = Package(name)
        pkg.path = path
        pkg.proj_info = _read(body) if with_ivpm_yaml else None
        if pkg.proj_info is not None:
            _merge_self_declared_types(pkg)
        # Reading a manifest with no dep-sets warns about it; that is the
        # fixture talking, not the code under test.
        del self._sink.records[:]
        return pkg

    def _messages(self):
        return [d.message for d in self._sink.records]

    def test_an_ivpm_aware_package_without_provides_is_noted(self):
        from ivpm.content_attrib import report_probe_adoption
        from ivpm.diagnostics import Severity
        pkg = self._pkg("aware", with_ivpm_yaml=True)
        report_probe_adoption(pkg, "python", evidence="pyproject.toml")
        self.assertEqual(1, len(self._sink.records))
        self.assertEqual(Severity.NOTE, self._sink.records[0].severity)
        text = self._messages()[0]
        self.assertIn("provides: [python]", text)
        self.assertIn("pyproject.toml", text)

    def test_a_package_with_no_ivpm_yaml_is_left_alone(self):
        """Nothing is being asked of that author, so a note would be advice
        the reader cannot act on."""
        from ivpm.content_attrib import report_probe_adoption
        report_probe_adoption(self._pkg("foreign", with_ivpm_yaml=False),
                              "python", evidence="pyproject.toml")
        self.assertEqual([], self._sink.records)

    def test_a_package_that_declared_provides_is_not_nagged(self):
        from ivpm.content_attrib import report_probe_adoption
        pkg = self._pkg("declared", with_ivpm_yaml=True, provides=["python"])
        report_probe_adoption(pkg, "python", evidence="pyproject.toml")
        self.assertEqual([], self._sink.records)

    def test_an_empty_provides_is_a_declaration_too(self):
        from ivpm.content_attrib import report_probe_adoption
        pkg = self._pkg("nothing", with_ivpm_yaml=True, provides=[])
        report_probe_adoption(pkg, "node", evidence="package.json")
        self.assertEqual([], self._sink.records)

    def test_strict_escalates_the_note_to_an_error(self):
        from ivpm.content_attrib import report_probe_adoption
        from ivpm.diagnostics import Severity
        pkg = self._pkg("aware", with_ivpm_yaml=True)
        report_probe_adoption(pkg, "python", strict=True)
        self.assertEqual(Severity.ERROR, self._sink.records[0].severity)

    def test_the_note_fires_from_the_python_probe(self):
        from ivpm.handlers.package_handler_python import PackageHandlerPython
        from ivpm.diagnostics import Severity
        from unittest.mock import MagicMock
        from ivpm.project_ops_info import ProjectUpdateInfo

        pkg = self._pkg("aware", with_ivpm_yaml=True)
        pkg.src_type = "git"
        with open(os.path.join(pkg.path, "pyproject.toml"), "w") as fp:
            fp.write('[project]\nname = "aware"\nversion = "1"\n')

        deps_dir = os.path.join(self.testdir, "packages")
        ui = ProjectUpdateInfo(args=MagicMock(strict=False), deps_dir=deps_dir,
                               project_dir=self.testdir)
        h = PackageHandlerPython()
        h.reset()
        h.on_leaf_post_load(pkg, ui)

        self.assertIn("aware", h.src_pkg_s, "the note must not block enrollment")
        self.assertEqual([Severity.NOTE],
                         [d.severity for d in self._sink.records])


if __name__ == "__main__":
    unittest.main()
