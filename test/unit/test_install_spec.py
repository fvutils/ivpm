#****************************************************************************
#* test_install_spec.py
#*
#* Tests for the 'ivpm install' interleaved source-spec grammar: splitting
#* argv into per---from groups, parsing each group, and alias defaulting.
#* Pure unit tests -- no subprocess, no network, no I/O.
#****************************************************************************
import os
import sys
import unittest

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_UNIT_DIR))
sys.path.insert(0, os.path.join(_ROOT_DIR, "src"))

from ivpm.install_spec import (
    SourceSpec, assign_manifest_aliases, build_global_option_table,
    flatten_dep_sets, parse_source_groups, slugify_source, split_source_groups)
from ivpm.yamlsrc import SrcLoaderError


def _globals():
    """The real 'install' global option table, read off the live parser."""
    from ivpm.__main__ import find_subparser, get_parser
    return build_global_option_table(find_subparser(get_parser(), "install"))


def _parse(argv, global_options=None):
    """Split and parse in one step, as main() does."""
    global_argv, groups = split_source_groups(argv, global_options)
    return global_argv, parse_source_groups(groups, global_options)


class TestSplitSourceGroups(unittest.TestCase):

    def test_split_single_source(self):
        g, groups = split_source_groups(
            ["-o", "/opt/eda", "--from", "cat.yaml", "-d", "sim"])
        self.assertEqual(g, ["-o", "/opt/eda"])
        self.assertEqual(groups, [["--from", "cat.yaml", "-d", "sim"]])

    def test_split_multiple_sources(self):
        """Options bind to the preceding --from, not to the invocation."""
        _, groups = split_source_groups([
            "--from", "a.yaml", "-d", "sim",
            "--from", "b.yaml", "-d", "common", "--as", "corp"])
        self.assertEqual(groups, [
            ["--from", "a.yaml", "-d", "sim"],
            ["--from", "b.yaml", "-d", "common", "--as", "corp"]])

    def test_global_before_first_from(self):
        g, groups = split_source_groups(
            ["-o", "/opt/eda", "--on-collision=last-wins", "--from", "a.yaml"])
        self.assertEqual(g, ["-o", "/opt/eda", "--on-collision=last-wins"])
        self.assertEqual(len(groups), 1)

    def test_from_equals_form(self):
        _, groups = split_source_groups(["--from=a.yaml", "-d", "sim"])
        self.assertEqual(groups, [["--from=a.yaml", "-d", "sim"]])

    def test_per_source_option_before_from_errors(self):
        """Promoting -d to global would apply it to every source."""
        with self.assertRaises(SrcLoaderError) as ctx:
            split_source_groups(["-d", "sim", "--from", "a.yaml"])
        self.assertIn("--from", str(ctx.exception))

    def test_no_sources_yields_empty_groups(self):
        g, groups = split_source_groups(["-o", "/opt/eda"])
        self.assertEqual(groups, [])
        self.assertEqual(g, ["-o", "/opt/eda"])

    def test_from_value_binds_even_when_option_shaped(self):
        _, groups = split_source_groups(
            ["--from", "-weird.yaml", "-d", "sim"], {"-w": 0})
        self.assertEqual(groups, [["--from", "-weird.yaml", "-d", "sim"]])


class TestGlobalHoisting(unittest.TestCase):
    """Globals bind to the invocation wherever they are typed (option 3)."""

    def test_trailing_outdir_is_hoisted(self):
        g, specs = _parse(
            ["--from", "cat.yaml", "-d", "sim", "-o", "tools"], _globals())
        self.assertEqual(g, ["-o", "tools"])
        self.assertEqual(specs[0].src, "cat.yaml")
        self.assertEqual(specs[0].dep_sets, ["sim"])

    def test_hoisted_from_middle_group(self):
        g, specs = _parse([
            "--from", "a.yaml", "-d", "sim", "--ssh",
            "--from", "b.yaml", "-o", "tools", "-d", "common"], _globals())
        self.assertEqual(g, ["--ssh", "-o", "tools"])
        self.assertEqual([s.dep_sets for s in specs], [["sim"], ["common"]])

    def test_attached_and_clustered_forms(self):
        g, _ = _parse(
            ["--from", "a.yaml", "--on-collision=last-wins", "-o/opt/eda",
             "-vv"], _globals())
        self.assertEqual(g, ["--on-collision=last-wins", "-o/opt/eda", "-vv"])

    def test_per_source_options_are_not_hoisted(self):
        """The whole point of the grammar: -d must stay with its source."""
        g, specs = _parse([
            "-o", "tools", "--from", "a.yaml", "-d", "sim",
            "--from", "b.yaml", "-D", "REL=1.0", "--as", "corp"], _globals())
        self.assertEqual(g, ["-o", "tools"])
        self.assertEqual(specs[0].dep_sets, ["sim"])
        self.assertEqual(specs[1].definitions, {"REL": "1.0"})
        self.assertEqual(specs[1].alias, "corp")

    def test_unknown_option_still_errors(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            _parse(["--from", "a.yaml", "--depset", "sim"], _globals())
        self.assertIn("unknown option", str(ctx.exception))

    def test_global_and_per_source_names_are_disjoint(self):
        """A collision would make hoisting silently drop a dep-set."""
        from ivpm.install_spec import PER_SOURCE_OPTIONS
        for opt in PER_SOURCE_OPTIONS:
            self.assertNotIn(opt, _globals())

    def test_misplaced_global_reported_when_not_hoisted(self):
        """Fallback path: split ran without a table, so say 'move it'."""
        _, groups = split_source_groups(["--from", "a.yaml", "-o", "tools"])
        with self.assertRaises(SrcLoaderError) as ctx:
            parse_source_groups(groups, _globals())
        msg = str(ctx.exception)
        self.assertIn("before the first --from", msg)
        self.assertNotIn("unknown option", msg)


class TestParseSourceGroups(unittest.TestCase):

    def test_mistyped_option_reports_unknown_option(self):
        """The failure this grammar exists to prevent: a mistyped option must
        not be silently absorbed or mistaken for a source."""
        with self.assertRaises(SrcLoaderError) as ctx:
            _parse(["--from", "a.yaml", "--depset", "sim"])
        msg = str(ctx.exception)
        self.assertIn("unknown option", msg)
        self.assertIn("--depset", msg)
        self.assertNotIn("fetch", msg)

    def test_dep_set_comma_and_repeat(self):
        _, specs = _parse(["--from", "a.yaml", "-d", "a,b", "-d", "c"])
        self.assertEqual(specs[0].dep_sets, ["a", "b", "c"])

    def test_dep_set_deduped_and_ordered(self):
        _, specs = _parse(["--from", "a.yaml", "-d", "b", "-d", "a,b"])
        self.assertEqual(specs[0].dep_sets, ["b", "a"])

    def test_definitions_scoped_per_source(self):
        _, specs = _parse([
            "--from", "a.yaml", "-D", "REL=1.0",
            "--from", "b.yaml", "-D", "REL=2.0", "-D", "ARCH=x86"])
        self.assertEqual(specs[0].definitions, {"REL": "1.0"})
        self.assertEqual(specs[1].definitions, {"REL": "2.0", "ARCH": "x86"})

    def test_explicit_alias_recorded(self):
        _, specs = _parse(["--from", "a.yaml", "--as", "corp"])
        self.assertEqual(specs[0].alias, "corp")
        self.assertTrue(specs[0].alias_explicit)

    def test_duplicate_explicit_alias_errors(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            _parse(["--from", "a.yaml", "--as", "x",
                    "--from", "b.yaml", "--as", "x"])
        self.assertIn("'x'", str(ctx.exception))


class TestFlattenDepSets(unittest.TestCase):

    def test_none_when_unselected(self):
        self.assertIsNone(flatten_dep_sets(None))
        self.assertIsNone(flatten_dep_sets([]))

    def test_whitespace_stripped(self):
        self.assertEqual(flatten_dep_sets([" a , b "]), ["a", "b"])

    def test_empty_segments_dropped(self):
        self.assertEqual(flatten_dep_sets(["a,,b"]), ["a", "b"])


class TestAliasDefaulting(unittest.TestCase):

    def test_alias_defaulting_precedence(self):
        """--as > package.name > URL slug."""
        specs = [
            SourceSpec(src="https://x/a.yaml", alias="explicit",
                       alias_explicit=True),
            SourceSpec(src="https://x/b.yaml"),
            SourceSpec(src="https://edapack.github.io/tools.yaml"),
        ]
        assign_manifest_aliases(specs, ["ignored-name", "declared-name", None])
        self.assertEqual(specs[0].alias, "explicit")
        self.assertEqual(specs[1].alias, "declared-name")
        self.assertEqual(specs[2].alias, "edapack-github-io-tools")

    def test_duplicate_defaulted_alias_errors(self):
        """Caught before any fetch, so collision messages stay unambiguous."""
        specs = [SourceSpec(src="a.yaml"), SourceSpec(src="b.yaml")]
        with self.assertRaises(SrcLoaderError):
            assign_manifest_aliases(specs, ["same", "same"])

    def test_slugify(self):
        self.assertEqual(slugify_source("https://edapack.github.io/"),
                         "edapack-github-io")
        self.assertEqual(slugify_source("/opt/catalogs/corp.yaml"),
                         "opt-catalogs-corp")
        self.assertEqual(slugify_source("///"), "source")


if __name__ == "__main__":
    unittest.main()
