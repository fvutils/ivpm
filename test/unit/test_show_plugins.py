"""Tests for 'ivpm show plugins'.

Renders against a temp project rather than going through the CLI, so the
assertions are about content rather than argparse wiring (``test_cli_help``
covers the parser).
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.show.show_plugins import ShowPlugins

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

SECRET = "SUPERSECRETTOKENVALUE"


class Args(object):
    def __init__(self, **kw):
        self.name = None
        self.check = None
        self.mcp = False
        self.json = False
        self.no_rich = True
        self.project_dir = None
        self.dep_set = None
        for k, v in kw.items():
            setattr(self, k, v)


class TestShowPluginsBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-show-plugins-")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        self.write("ivpm.yaml", """\
package:
  name: demo-proj
  dep-sets:
    - name: default-dev
      deps: []
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, relpath, content):
        path = os.path.join(self.proj, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def mkplugin(self, dirname="plugins/hello", name="hello", mcp=False,
                 skills=("greet",), manifest=None):
        doc = manifest if manifest is not None else {
            "$schema": PLUGIN_SCHEMA, "name": name,
            "version": "0.1.0", "description": "A demo plugin",
        }
        self.write(os.path.join(dirname, "plugin.json"), json.dumps(doc, indent=2))
        for skill in skills:
            self.write(os.path.join(dirname, "skills", skill, "SKILL.md"),
                       "---\nname: %s\ndescription: Does %s.\n---\n\nbody\n" % (skill, skill))
        if mcp:
            self.write(os.path.join(dirname, "mcp.json"), json.dumps({
                "$schema": MCP_SCHEMA,
                "mcpServers": {
                    "local": {"type": "stdio", "command": "./bin/srv",
                              "args": ["--x"], "env": {"TOKEN": SECRET}},
                    "remote": {"type": "streamable-http",
                               "url": "https://example.com/mcp",
                               "headers": {"Authorization": SECRET}},
                },
            }, indent=2))
        return os.path.join(self.proj, dirname)

    def run_show(self, **kw):
        """Run ShowPlugins, returning captured stdout."""
        kw.setdefault("project_dir", self.proj)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ShowPlugins()(Args(**kw))
        return out.getvalue()

    def run_show_exit(self, **kw):
        """Run ShowPlugins expecting SystemExit; return (exit_code, stdout)."""
        kw.setdefault("project_dir", self.proj)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                ShowPlugins()(Args(**kw))
        code = ctx.exception.code
        return (0 if code is None else code, out.getvalue())


class TestList(TestShowPluginsBase):

    def test_lists_project_plugin(self):
        self.mkplugin()
        out = self.run_show()
        self.assertIn("hello", out)
        self.assertIn("skills=1", out)

    def test_no_plugins_message(self):
        out = self.run_show()
        self.assertIn("No Agent Plugins found", out)

    def test_foreign_plugin_json_not_listed_and_silent(self):
        """An unrelated plugin.json must neither appear nor produce noise."""
        self.write("plugins/acme/plugin.json", json.dumps({
            "$schema": "https://example.com/schemas/plugin.json",
            "id": "acme-datasource"}))
        out = self.run_show()
        self.assertIn("No Agent Plugins found", out)
        self.assertNotIn("warning", out)
        self.assertNotIn("acme", out)

    def test_detail_view(self):
        self.mkplugin()
        out = self.run_show(name="hello")
        self.assertIn("Plugin:", out)
        self.assertIn("greet", out)
        self.assertIn("demo-proj", out)

    def test_unknown_name_exits_nonzero(self):
        self.mkplugin()
        code, _ = self.run_show_exit(name="nope")
        self.assertEqual(code, 1)

    def test_json_output_shape(self):
        self.mkplugin(mcp=True)
        data = json.loads(self.run_show(json=True))
        self.assertIn("plugins", data)
        self.assertIn("diagnostics", data)
        plugin = data["plugins"][0]
        for key in ("name", "owner", "kind", "spec_version", "root_dir",
                    "skills", "mcp_servers"):
            self.assertIn(key, plugin)
        self.assertEqual(plugin["name"], "hello")
        self.assertEqual(plugin["skills"], ["greet"])

    def test_unsupported_version_reported(self):
        self.mkplugin(manifest={
            "$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json",
            "name": "future"})
        out = self.run_show()
        self.assertIn("No Agent Plugins found", out)
        # Auto-probe: an unreadable *Agent Plugins* manifest is worth reporting,
        # unlike a foreign one.
        self.assertIn("unsupported-version", out)


class TestMcpView(TestShowPluginsBase):

    def test_mcp_lists_servers_qualified(self):
        self.mkplugin(mcp=True)
        out = self.run_show(mcp=True)
        self.assertIn("hello.local", out)
        self.assertIn("hello.remote", out)
        self.assertIn("stdio", out)

    def test_mcp_redacts_env_values(self):
        """Env keys are shown; values never are. This output must be safe to paste."""
        self.mkplugin(mcp=True)
        out = self.run_show(mcp=True)
        self.assertIn("TOKEN", out)
        self.assertNotIn(SECRET, out)

    def test_mcp_redacts_header_values(self):
        self.mkplugin(mcp=True)
        out = self.run_show(mcp=True)
        self.assertIn("Authorization", out)
        self.assertNotIn(SECRET, out)

    def test_mcp_json_redacts_values(self):
        self.mkplugin(mcp=True)
        raw = self.run_show(mcp=True, json=True)
        self.assertNotIn(SECRET, raw)
        data = json.loads(raw)
        server = [s for s in data["servers"] if s["name"] == "local"][0]
        self.assertEqual(server["env_keys"], ["TOKEN"])
        self.assertEqual(server["qualified_name"], "hello.local")
        self.assertNotIn("env", server)

    def test_mcp_states_opt_in(self):
        self.mkplugin(mcp=True)
        out = self.run_show(mcp=True)
        self.assertIn("opt-in", out)

    def test_mcp_empty_message(self):
        self.mkplugin()
        out = self.run_show(mcp=True)
        self.assertIn("No MCP servers", out)


class TestCheck(TestShowPluginsBase):

    def test_check_valid_exit_zero(self):
        root = self.mkplugin()
        code, out = self.run_show_exit(check=root)
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        self.assertIn("Agent Plugins 1.0.0", out)

    def test_check_accepts_manifest_path(self):
        root = self.mkplugin()
        code, out = self.run_show_exit(check=os.path.join(root, "plugin.json"))
        self.assertEqual(code, 0)

    def test_check_invalid_name_exit_nonzero(self):
        root = self.mkplugin(manifest={"$schema": PLUGIN_SCHEMA, "name": "Bad--Name"})
        code, out = self.run_show_exit(check=root)
        self.assertEqual(code, 1)
        self.assertIn("name.charset", out)

    def test_check_message_is_actionable(self):
        """The report must state the naming rule, not echo the raw regex."""
        root = self.mkplugin(manifest={"$schema": PLUGIN_SCHEMA, "name": "Bad--Name"})
        _, out = self.run_show_exit(check=root)
        self.assertIn("Bad--Name", out)
        self.assertIn("lowercase", out)
        self.assertNotIn("[a-z0-9.-]", out)

    def test_check_foreign_manifest_exit_nonzero(self):
        """--check is explicit, so 'not a plugin' is a failure, not silence."""
        self.write("acme/plugin.json", json.dumps({
            "$schema": "https://example.com/x.json", "id": "acme"}))
        code, out = self.run_show_exit(check=os.path.join(self.proj, "acme"))
        self.assertEqual(code, 1)
        self.assertIn("manifest.not-a-plugin", out)
        self.assertIn("warning", out)

    def test_check_missing_path_exit_nonzero(self):
        code, out = self.run_show_exit(check=os.path.join(self.proj, "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("not-a-plugin-path", out)

    def test_check_warning_fails_the_check(self):
        """A loadable plugin with a component problem still fails --check."""
        root = self.mkplugin()
        os.makedirs(os.path.join(root, "skills", "broken"))
        code, out = self.run_show_exit(check=root)
        self.assertEqual(code, 1)
        self.assertIn("skills.no-skill-md", out)

    def test_check_json(self):
        root = self.mkplugin()
        code, out = self.run_show_exit(check=root, json=True)
        data = json.loads(out)
        self.assertTrue(data["valid"])
        self.assertEqual(data["plugin"]["name"], "hello")
        self.assertEqual(data["diagnostics"], [])

    def test_check_json_invalid(self):
        root = self.mkplugin(manifest={"$schema": PLUGIN_SCHEMA, "name": "Bad--Name"})
        code, out = self.run_show_exit(check=root, json=True)
        data = json.loads(out)
        self.assertFalse(data["valid"])
        self.assertIsNone(data["plugin"])
        self.assertTrue(any(d["code"] == "name.charset" for d in data["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
