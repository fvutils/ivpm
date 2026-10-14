"""Init templates: descriptors, template/collection scanning, lenient metadata."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.init_template import InitTemplate
from ivpm.init_template import template_dir as td


def mk_template(path, copier_yml="", fn="copier.yml"):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, fn), "w") as fp:
        fp.write(textwrap.dedent(copier_yml))
    return path


class TestTemplateDir(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_template_yml(self):
        t = mk_template(os.path.join(self.tmp, "foo"), "q: 1\n")
        self.assertTrue(td.is_template_dir(t))
        res = td.scan(t, origin="env")
        self.assertEqual(["foo"], [x.name for x in res])
        self.assertEqual(os.path.abspath(t), res[0].src)
        self.assertEqual("env", res[0].origin)

    def test_single_template_yaml(self):
        t = mk_template(os.path.join(self.tmp, "bar"), "q: 1\n", fn="copier.yaml")
        self.assertEqual(["bar"], [x.name for x in td.scan(t)])

    def test_collection(self):
        mk_template(os.path.join(self.tmp, "b"))
        mk_template(os.path.join(self.tmp, "a"))
        os.makedirs(os.path.join(self.tmp, "not_a_template"))
        warnings = []
        res = td.scan(self.tmp, warnings=warnings)
        self.assertEqual(["a", "b"], [x.name for x in res])
        self.assertEqual([], warnings)

    def test_nested_collection_not_recursed(self):
        mk_template(os.path.join(self.tmp, "outer", "inner"))
        warnings = []
        self.assertEqual([], td.scan(self.tmp, warnings=warnings))
        self.assertEqual(1, len(warnings))
        self.assertIn("no copier templates", warnings[0])

    def test_missing_path_warns(self):
        warnings = []
        self.assertEqual([], td.scan(os.path.join(self.tmp, "nope"), warnings=warnings))
        self.assertIn("does not exist", warnings[0])

    def test_ivpm_name_overrides_basename(self):
        t = mk_template(os.path.join(self.tmp, "dir"), """\
            _ivpm:
              name: fancy
              description: A fancy one
              category: hdl
              next_steps: run it
              hidden: true
            """)
        res = td.scan(t)[0]
        self.assertEqual(("fancy", "A fancy one", "hdl", "run it", True),
                         (res.name, res.description, res.category,
                          res.next_steps, res.hidden))

    def test_ivpm_cannot_set_trust(self):
        t = mk_template(os.path.join(self.tmp, "dir"), "_ivpm:\n  trust: true\n")
        self.assertIsNone(td.scan(t)[0].trusted)

    def test_invalid_ivpm_name_falls_back(self):
        t = mk_template(os.path.join(self.tmp, "dir"), "_ivpm:\n  name: 'a b'\n")
        warnings = []
        self.assertEqual("dir", td.scan(t, warnings=warnings)[0].name)
        self.assertEqual(1, len(warnings))

    def test_include_tags_do_not_stop_metadata(self):
        t = mk_template(os.path.join(self.tmp, "inc"), """\
            _ivpm:
              description: with includes
            !include shared.yml: ~
            ---
            !include other.yml
            ---
            q: 1
            """)
        warnings = []
        res = td.scan(t, warnings=warnings)[0]
        self.assertEqual("with includes", res.description)
        self.assertEqual([], warnings)
        self.assertEqual(["q"], [q.name for q in td.read_questions(t)])

    def test_malformed_yaml_still_listed(self):
        t = mk_template(os.path.join(self.tmp, "bad"), "_ivpm: [unclosed\n")
        warnings = []
        res = td.scan(t, warnings=warnings)
        self.assertEqual(["bad"], [x.name for x in res])
        self.assertEqual("", res[0].description)
        self.assertEqual(1, len(warnings))
        self.assertIn("cannot be parsed", warnings[0])

    def test_ivpm_not_a_mapping(self):
        t = mk_template(os.path.join(self.tmp, "x"), "_ivpm: just a string\n")
        warnings = []
        self.assertEqual("x", td.scan(t, warnings=warnings)[0].name)
        self.assertIn("not a mapping", warnings[0])

    def test_read_questions_forms(self):
        t = mk_template(os.path.join(self.tmp, "q"), """\
            _subdirectory: template
            _ivpm: {name: q}
            simple: 3
            full:
              type: str
              default: abc
              help: A question
            """)
        qs = td.read_questions(t)
        self.assertEqual(["simple", "full"], [q.name for q in qs])
        self.assertEqual(3, qs[0].default)
        self.assertEqual(("str", "abc", "A question"),
                         (qs[1].type, qs[1].default, qs[1].help))


class TestFromMapping(unittest.TestCase):

    def test_full_entry(self):
        t = InitTemplate.from_mapping(
            {"name": "acme", "src": "https://git.acme.com/t.git", "ref": "v1",
             "description": "d", "category": "c", "hidden": True, "trust": True,
             "defaults": {"a": 1}, "next_steps": "n"},
            origin="user-config")
        self.assertEqual(
            ("acme", "https://git.acme.com/t.git", "v1", "d", "c", True, True,
             {"a": 1}, "n", "user-config"),
            (t.name, t.src, t.ref, t.description, t.category, t.hidden,
             t.trusted, t.defaults, t.next_steps, t.origin))
        self.assertTrue(t.is_remote)

    def test_trust_unset_is_none(self):
        t = InitTemplate.from_mapping({"name": "a", "src": "/x"})
        self.assertIsNone(t.trusted)
        self.assertFalse(t.hidden)

    def test_tilde_expansion(self):
        t = InitTemplate.from_mapping({"name": "a", "src": "~/tmpl"})
        self.assertEqual(os.path.join(os.path.expanduser("~"), "tmpl"), t.src)

    def test_relative_against_base_dir(self):
        t = InitTemplate.from_mapping({"name": "a", "src": "sub/t"}, base_dir="/etc/ivpm")
        self.assertEqual("/etc/ivpm/sub/t", t.src)

    def test_unknown_key_warns(self):
        warnings = []
        InitTemplate.from_mapping({"name": "a", "src": "/x", "colour": "red"},
                                  warnings=warnings)
        self.assertEqual(1, len(warnings))
        self.assertIn("colour", warnings[0])

    def test_wrong_types(self):
        for bad in ({"name": "a", "src": "/x", "hidden": "yes"},
                    {"name": "a", "src": "/x", "trust": 1},
                    {"name": "a", "src": "/x", "defaults": [1]},
                    {"name": "a b", "src": "/x"},
                    {"name": "a"},
                    {"src": "/x"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                InitTemplate.from_mapping(bad)

    def test_error_names_field(self):
        with self.assertRaises(ValueError) as cm:
            InitTemplate.from_mapping({"name": "a", "src": "/x", "hidden": "yes"})
        self.assertIn("hidden", str(cm.exception))


class TestNoCopierImport(unittest.TestCase):

    def test_package_import_is_light(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "src")
        code = ("import sys, ivpm.init_template, ivpm.init_template.template_dir;"
                "bad=[m for m in ('copier','ivpm.init_template.template_rgy') if m in sys.modules];"
                "print(','.join(bad))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=src), check=True)
        self.assertEqual("", out.stdout.strip())


if __name__ == "__main__":
    unittest.main()
