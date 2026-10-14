"""`ivpm show init-templates`: listing, detail, JSON, and warnings."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.init_template.template_rgy import ENV_TEMPLATE_PATH

from .init_template_fixtures import mk_template, copier_hidden
from .test_init_cmd import InitCmdBase

COLL = {
    "alpha": "_ivpm: {description: First, category: hdl}\nproject_name: {type: str, help: Name}\n",
    "beta": "_ivpm: {description: Second, category: python}\nlevel: 3\n",
    "secret": "_ivpm: {description: Hidden one, hidden: true}\n",
    # Shadows the built-in 'default'.
    "default": "_ivpm: {description: Site default}\n",
}

INFO_KEYS = {"name", "description", "params", "notes", "origin", "provider", "version",
             "category", "src", "ref", "trusted", "hidden", "next_steps", "shadowed"}


class TestShowInitTemplates(InitCmdBase):

    def setUp(self):
        super().setUp()
        self.coll = os.path.join(self.tmp, "coll")
        for name, yml in COLL.items():
            mk_template(os.path.join(self.coll, name), yml)
        os.environ[ENV_TEMPLATE_PATH] = os.pathsep.join(
            [self.coll, os.path.join(self.tmp, "stale")])

    def test_list_plain(self):
        code, out, err = self.ivpm("show", "init-templates", "--no-rich")
        self.assertEqual(0, code, err)
        lines = out.splitlines()
        self.assertIn("hdl:", lines)
        self.assertIn("python:", lines)
        self.assertLess(lines.index("hdl:"), lines.index("python:"))
        default = [ln for ln in lines if ln.strip().startswith("default ")][0]
        self.assertIn("Site default", default)
        self.assertIn("(shadows 1)", default)
        self.assertNotIn("secret", out)
        self.assertIn("Warnings:", out)
        self.assertIn("stale", out)

    def test_all_includes_hidden(self):
        _, out, _ = self.ivpm("show", "init-templates", "--no-rich", "--all")
        self.assertIn("secret", out)
        _, out, _ = self.ivpm("init", "--list", "--all")
        self.assertIn("secret", out)

    def test_rich_list(self):
        code, out, err = self.ivpm("show", "init-templates")
        self.assertEqual(0, code, err)
        self.assertIn("alpha", out)
        self.assertIn("shadows 1", out)

    def test_detail(self):
        with copier_hidden():
            code, out, err = self.ivpm("show", "init-templates", "default", "--no-rich")
        self.assertEqual(0, code, err)
        self.assertIn("Trusted:          no", out)
        self.assertIn("Shadows:", out)
        self.assertIn("built-in:", out)
        code, out, _ = self.ivpm("show", "init-templates", "alpha", "--no-rich")
        self.assertIn("project_name [str]  Name", out)

    def test_detail_rich(self):
        code, out, err = self.ivpm("show", "init-templates", "alpha")
        self.assertEqual(0, code, err)
        self.assertIn("project_name", out)

    def test_json(self):
        code, out, err = self.ivpm("show", "init-templates", "--json")
        self.assertEqual(0, code, err)
        data = json.loads(out)
        self.assertEqual(["alpha", "beta", "default"], sorted(d["name"] for d in data))
        for d in data:
            self.assertEqual(INFO_KEYS, set(d))
        dflt = [d for d in data if d["name"] == "default"][0]
        self.assertEqual(["built-in"], [s["origin"] for s in dflt["shadowed"]])
        self.assertIn("stale", err)
        code, out, _ = self.ivpm("show", "init-templates", "beta", "--json")
        detail = json.loads(out)
        self.assertEqual([{"name": "level", "description": "", "required": False,
                           "default": "3", "type_hint": ""}], detail["params"])

    def test_unknown(self):
        code, _, err = self.ivpm("show", "init-templates", "alpah")
        self.assertEqual(1, code)
        self.assertIn("did you mean: alpha", err)

    def test_bare_show_includes_section(self):
        code, out, err = self.ivpm("show", "--no-rich")
        self.assertEqual(0, code, err)
        self.assertIn("=== Init Templates ===", out)
        code, out, _ = self.ivpm("show", "--json")
        self.assertIn("init_templates", json.loads(out))

    def test_init_list_matches_show(self):
        _, a, _ = self.ivpm("init", "--list")
        _, b, _ = self.ivpm("show", "init-templates", "--no-rich")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
