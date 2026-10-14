"""Init templates: the variable contract (explicit answers, defaults, context)."""
import argparse
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm import site_config
from ivpm.site_config import DefaultSiteConfig
from ivpm.init_template import InitTemplate
from ivpm.init_template import template_vars as tv
from ivpm.init_template.errors import InitError


def _args(name=None, version=None, definitions=()):
    return argparse.Namespace(name=name, version=version, definitions=list(definitions))


class TestDefinitions(unittest.TestCase):

    def test_scalars(self):
        self.assertEqual(
            {"b": True, "i": 3, "s": "abc", "e": "", "q": "3", "f": False},
            tv.parse_definitions(["b=true", "i=3", "s=abc", "e=", "q='3'", "f=no"]))

    def test_value_with_equals(self):
        self.assertEqual({"k": "a=b"}, tv.parse_definitions(["k=a=b"]))

    def test_invalid_yaml_kept_as_string(self):
        self.assertEqual({"k": "[unclosed"}, tv.parse_definitions(["k=[unclosed"]))

    def test_repeated_key_last_wins(self):
        self.assertEqual({"k": 2}, tv.parse_definitions(["k=1", "k=2"]))

    def test_missing_equals(self):
        with self.assertRaises(InitError) as cm:
            tv.parse_definitions(["ok=1", "oops"])
        self.assertIn("oops", str(cm.exception))

    def test_empty_key(self):
        with self.assertRaises(InitError):
            tv.parse_definitions(["=1"])


class TestExplicit(unittest.TestCase):

    def test_name_and_version(self):
        self.assertEqual({"project_name": "p", "project_version": "1.0"},
                         tv.explicit_answers(_args("p", "1.0")))

    def test_omitted(self):
        self.assertEqual({}, tv.explicit_answers(_args()))

    def test_d_overrides_name(self):
        self.assertEqual({"project_name": "x"},
                         tv.explicit_answers(_args("p", definitions=["project_name=x"])))

    def test_ivpm_reserved(self):
        t = InitTemplate(name="t", src="/t")
        with self.assertRaises(InitError) as cm:
            tv.build_copier_inputs(t, _args(definitions=["ivpm=1"]), "/tmp")
        self.assertIn("reserved", str(cm.exception))


class _Cfg(DefaultSiteConfig):
    defaults = {}

    def get_init_defaults(self):
        return self.defaults


class TestDefaults(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.dest = os.path.join(self.tmp, "my-proj")
        os.makedirs(self.dest)
        self._env = mock.patch.dict(os.environ, {
            "IVPM_USER_CONFIG": os.path.join(self.tmp, "user.yaml"),
            "IVPM_SITE_CONFIG": os.path.join(self.tmp, "site.yaml"),
        })
        self._env.start()
        site_config._config_files = None
        self.cfg = _Cfg()
        self._sc = mock.patch.object(site_config, "get_site_config", lambda: self.cfg)
        self._sc.start()
        self._git = mock.patch.object(tv, "_git_config", lambda key, cwd=None: None)
        self._git.start()

    def tearDown(self):
        self._git.stop()
        self._sc.stop()
        self._env.stop()
        site_config._config_files = None
        self._tmp.cleanup()

    def _write(self, which, text):
        with open(os.path.join(self.tmp, which + ".yaml"), "w") as fp:
            fp.write(text)
        site_config._config_files = None

    def test_derived(self):
        d = tv.derived_defaults(self.dest)
        self.assertEqual({"project_name": "my-proj", "project_version": "0.0.1",
                          "year": datetime.date.today().year}, d)

    def test_derived_git(self):
        vals = {"user.name": "Jane Doe", "user.email": "jane@example.com"}
        with mock.patch.object(tv, "_git_config", lambda key, cwd=None: vals.get(key)):
            d = tv.derived_defaults(self.dest)
        self.assertEqual(("Jane Doe", "jane@example.com"),
                         (d["author_name"], d["author_email"]))

    def test_no_git_binary(self):
        self._git.stop()
        try:
            with mock.patch.dict(os.environ, {"PATH": self.tmp}):
                self.assertIsNone(tv._git_config("user.name"))
        finally:
            self._git.start()

    def test_layering(self):
        t = InitTemplate(name="t", src="/t",
                         defaults={"l2": "tmpl", "all": "tmpl", "project_version": "9"})
        self.cfg.defaults = {"l3": "sitecfg", "all": "sitecfg"}
        self._write("site", "init-defaults: {l4: sitefile, all: sitefile}\n")
        self._write("user", "init-defaults: {l5: userfile, all: userfile}\n")
        ud = tv.layered_user_defaults(t, self.dest)
        self.assertEqual("my-proj", ud["project_name"])     # derived
        self.assertEqual("9", ud["project_version"])        # template beats derived
        self.assertEqual(("tmpl", "sitecfg", "sitefile", "userfile", "userfile"),
                         (ud["l2"], ud["l3"], ud["l4"], ud["l5"], ud["all"]))

    def test_bad_init_defaults_ignored(self):
        self._write("user", "init-defaults: [a, b]\n")
        ud = tv.layered_user_defaults(InitTemplate(name="t", src="/t"), self.dest)
        self.assertEqual("my-proj", ud["project_name"])

    def test_channels_kept_separate(self):
        self._write("user", "init-defaults: {author_name: Cfg}\n")
        t = InitTemplate(name="t", src="/t", origin="built-in")
        data, ud = tv.build_copier_inputs(
            t, _args("given", definitions=["extra=1"]), self.dest)
        self.assertEqual({"project_name", "extra", "ivpm"}, set(data))
        self.assertEqual("given", data["project_name"])
        self.assertEqual("my-proj", ud["project_name"])
        self.assertNotIn("extra", ud)
        self.assertNotIn("author_name", data)
        self.assertNotIn("ivpm", ud)

    def test_context(self):
        t = InitTemplate(name="t", src="/t", origin="acme")
        ctx = tv.ivpm_context(t)
        for key in ("version", "python", "python_version", "platform", "template",
                    "origin"):
            self.assertIn(key, ctx)
        self.assertEqual(("t", "acme"), (ctx["template"], ctx["origin"]))
        json.dumps(ctx)


if __name__ == "__main__":
    unittest.main()
