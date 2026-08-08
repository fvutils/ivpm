"""Handler-level tests for Agent Plugins discovery and projection.

`test_agents.py` remains the regression baseline for the plain-skills path;
everything here is about plugins.

The projection differs per tool, and the distinction is the point:

* ``.agents/`` and ``.cursor/`` **unbundle** — each of a plugin's skills is
  linked individually, because those consumers understand skills, not plugins.
  (Codex needs no mirror at all: it scans ``.agents/skills`` from the working
  directory up to the repository root.)
* ``.claude/`` **installs** — the plugin is materialized whole, with a
  ``.claude-plugin/plugin.json`` manifest, and Claude Code namespaces its
  skills itself.
"""
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from .test_base import TestBase

from ivpm.handlers.package_handler_agents import PackageHandlerAgents


def dep_yaml(name, project="test_plugins", extra_deps="", with_block=""):
    return """
package:
    name: %s%s
    dep-sets:
        - name: default-dev
          deps:
            - name: %s
              url: file://${DATA_DIR}/%s
              src: dir%s
""" % (project, with_block, name, name, extra_deps)


class PluginTestBase(TestBase):

    @contextmanager
    def entrypoint_plugins(self, *pairs):
        """Inject (ep_name, plugin_root) pairs as the python handler would.

        The real producer runs pip/uv and interrogates the venv's
        ``agent.plugins`` entry-points; these tests use skip_venv=True, so push
        the same pairs onto update_info just before the agents handler consumes
        them.
        """
        orig = PackageHandlerAgents.on_root_post_load

        def wrapper(handler, update_info):
            update_info.pending_plugin_dirs.extend(
                (name, os.path.normpath(d() if callable(d) else d)) for name, d in pairs)
            return orig(handler, update_info)

        with patch.object(PackageHandlerAgents, "on_root_post_load", wrapper):
            yield

    # -- workspace inspection ------------------------------------------- #

    def listing(self, *parts):
        d = os.path.join(self.testdir, *parts)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def agents_skills(self):
        return self.listing(".agents", "skills")

    def agents_plugins(self):
        return self.listing(".agents", "plugins")

    def claude_skills(self):
        return self.listing(".claude", "skills")

    def cursor_skills(self):
        return self.listing(".cursor", "skills")

    def path(self, *parts):
        return os.path.join(self.testdir, *parts)


class TestDiscovery(PluginTestBase):

    def test_plugin_at_package_root(self):
        """A dep whose root is a plugin: linked whole, and its skills expanded."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])
        self.assertEqual(self.agents_skills(), ["demo-plugin-alpha", "demo-plugin-beta"])

    def test_plugin_link_is_relative(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        link = self.path(".agents", "plugins", "demo-plugin")
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.isabs(os.readlink(link)))

    def test_plugin_link_resolves_to_manifest(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        manifest = self.path(".agents", "plugins", "demo-plugin", "plugin.json")
        self.assertTrue(os.path.isfile(manifest))
        self.assertEqual(json.load(open(manifest))["name"], "demo-plugin")

    def test_plugins_subdir_autoprobe(self):
        """plugins/*/plugin.json is probed when nothing is configured."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_nested_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["nested-one", "nested-two"])
        self.assertEqual(self.agents_skills(), ["nested-one-uno", "nested-two-duo"])

    def test_dep_entry_plugin_patterns(self):
        """A consumer can name the manifests explicitly in the dep entry."""
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_nested_dep",
            extra_deps="\n              agents:\n                plugins:\n"
                       "                  - plugins/one/plugin.json"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["nested-one"])

    def test_dep_entry_pattern_may_name_a_directory(self):
        """The dual spelling: a pattern may match the plugin root instead."""
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_nested_dep",
            extra_deps="\n              agents:\n                plugins:\n"
                       "                  - plugins/two"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["nested-two"])

    def test_dep_entry_pattern_escaping_package_rejected(self):
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_dep",
            extra_deps="\n              agents:\n                plugins:\n"
                       "                  - ../../../plugin.json"))
        with self.assertLogs("ivpm.handlers.package_handler_agents", level="WARNING"):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), [])

    def test_foreign_plugin_json_ignored_silently(self):
        """An unrelated plugin.json yields no plugin, no warning, and no effect
        on the package's ordinary skill discovery."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_foreign_dep"))

        logger = "ivpm.handlers.package_handler_agents"
        with self.assertLogs(logger, level="DEBUG") as captured:
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), [])
        self.assertEqual(self.agents_skills(), ["plugin_foreign_dep"])
        warnings = [r for r in captured.records if r.levelname in ("WARNING", "ERROR")]
        self.assertEqual(warnings, [], "a foreign plugin.json must not warn")

    def test_unsupported_version_warns_and_skips(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_future_dep"))
        with self.assertLogs("ivpm.handlers.package_handler_agents",
                             level="WARNING") as captured:
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), [])
        self.assertIn("2.0.0", " ".join(r.getMessage() for r in captured.records))

    def test_project_plugin_discovered(self):
        """The project itself may ship a plugin."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_project_plugin
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("plugins/mine/plugin.json", json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "mine"}))
        self.mkFile("plugins/mine/skills/thing/SKILL.md",
                    "---\nname: thing\ndescription: A thing.\n---\n\nbody\n")
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["mine"])
        self.assertEqual(self.agents_skills(), ["mine-thing"])


class TestProjection(PluginTestBase):

    def test_claude_installs_plugin_whole(self):
        """Claude Code gets the plugin itself, with a manifest it recognizes."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.claude_skills(), ["demo-plugin"])
        manifest = self.path(".claude", "skills", "demo-plugin",
                             ".claude-plugin", "plugin.json")
        self.assertTrue(os.path.isfile(manifest))
        self.assertEqual(json.load(open(manifest))["name"], "demo-plugin")

    def test_installed_manifest_is_the_agent_plugins_manifest(self):
        """No rewriting: the field names coincide and unrecognized top-level
        fields are ignored at load time."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        src = json.load(open(self.path(
            "packages", "plugin_dep", "plugin.json")))
        installed = json.load(open(self.path(
            ".claude", "skills", "demo-plugin", ".claude-plugin", "plugin.json")))
        self.assertEqual(src, installed)

    def test_installed_plugin_exposes_skills_dir(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        skills = self.path(".claude", "skills", "demo-plugin", "skills")
        self.assertTrue(os.path.isdir(skills))
        self.assertEqual(sorted(os.listdir(skills)), ["alpha", "beta"])
        self.assertTrue(os.path.isfile(os.path.join(skills, "alpha", "SKILL.md")))

    def test_installed_plugin_carries_other_top_level_entries(self):
        """bin/ and friends are linked through so ${CLAUDE_PLUGIN_ROOT} works."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isfile(
            self.path(".claude", "skills", "demo-plugin", "bin", "srv")))

    def test_install_and_unbundle_are_exclusive(self):
        """The correctness property: a plugin's skills must not appear twice in
        a tool that received the whole plugin."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.claude_skills(), ["demo-plugin"])
        self.assertNotIn("demo-plugin-alpha", self.claude_skills())
        self.assertNotIn("demo-plugin-beta", self.claude_skills())

    def test_cursor_unbundles(self):
        """Cursor has no plugin mechanism, so it gets individual skills."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.cursor_skills(),
                         ["demo-plugin-alpha", "demo-plugin-beta"])

    def test_no_tool_plugin_directories_created(self):
        """No client reads a project-local plugin-root directory; creating one
        would mean creating something nothing consumes."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.exists(self.path(".claude", "plugins")))
        self.assertFalse(os.path.exists(self.path(".cursor", "plugins")))

    def test_plugin_install_false_unbundles_everywhere(self):
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_dep",
            with_block="\n    with:\n        agents:\n            plugin_install: false"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.claude_skills(),
                         ["demo-plugin-alpha", "demo-plugin-beta"])
        self.assertEqual(self.agents_plugins(), ["demo-plugin"])

    def test_expand_skills_false(self):
        """Whole-plugin views remain; per-skill links do not."""
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_dep",
            with_block="\n    with:\n        agents:\n            expand_skills: false"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])
        self.assertEqual(self.agents_skills(), [])
        self.assertEqual(self.claude_skills(), ["demo-plugin"])

    def test_claude_false_skips_plugin_install(self):
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_dep",
            with_block="\n    with:\n        agents:\n            claude: false"))
        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.exists(self.path(".claude", "skills")))
        self.assertEqual(self.agents_plugins(), ["demo-plugin"])


class TestNamingAndDedup(PluginTestBase):

    def test_plugin_name_comes_from_the_manifest(self):
        """Not from the directory: the package is 'plugin_dep', the plugin
        'demo-plugin'."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])

    def test_duplicate_plugin_names_disambiguated_by_package(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_plugin_dup
            dep-sets:
                - name: default-dev
                  deps:
                    - name: plugin_dup_a
                      url: file://${DATA_DIR}/plugin_dup_a
                      src: dir
                    - name: plugin_dup_b
                      url: file://${DATA_DIR}/plugin_dup_b
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(),
                         ["plugin_dup_a-twin", "plugin_dup_b-twin"])
        # The skills keep the plugin's own name; they do not collide.
        self.assertEqual(self.agents_skills(), ["twin-one", "twin-two"])

    def test_plugin_skill_not_double_linked_as_loose_skill(self):
        """plugin_dep has skills/ at its root, so the ordinary auto-probe finds
        the same directories. They must be linked once, under the plugin."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_skills(),
                         ["demo-plugin-alpha", "demo-plugin-beta"])

    def test_entrypoint_plugin_linked(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_ep_plugin
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        root = os.path.join(self.data_dir, "plugin_dep")
        with self.entrypoint_plugins(("ep-pkg", root)):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])

    def test_entrypoint_plugin_projected_to_every_tool(self):
        """An entry-point plugin is projected exactly like a path-discovered one:
        unbundled for the skills-only consumers (.agents, and therefore Codex,
        plus Cursor) and installed whole for Claude Code."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_ep_projection
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        root = os.path.join(self.data_dir, "plugin_dep")
        with self.entrypoint_plugins(("ep-pkg", root)):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])
        self.assertEqual(self.agents_skills(),
                         ["demo-plugin-alpha", "demo-plugin-beta"])
        self.assertEqual(self.cursor_skills(),
                         ["demo-plugin-alpha", "demo-plugin-beta"])
        self.assertEqual(self.claude_skills(), ["demo-plugin"])
        self.assertTrue(os.path.isfile(self.path(
            ".claude", "skills", "demo-plugin", ".claude-plugin", "plugin.json")))

    def test_entrypoint_plugin_outside_project_links_absolutely(self):
        """A plugin resolved from the venv (or anywhere outside the project
        tree) is linked by absolute path; a relative link would not resolve."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_ep_abs
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        root = os.path.join(self.data_dir, "plugin_dep")
        with self.entrypoint_plugins(("ep-pkg", root)):
            self.ivpm_update(skip_venv=True)

        link = self.path(".agents", "plugins", "demo-plugin")
        self.assertTrue(os.path.isabs(os.readlink(link)))
        self.assertTrue(os.path.isfile(os.path.join(link, "plugin.json")))

        skill = self.path(".cursor", "skills", "demo-plugin-alpha")
        self.assertTrue(os.path.isfile(os.path.join(skill, "SKILL.md")))

    def test_entrypoint_plugin_may_name_the_manifest(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_ep_manifest
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        manifest = os.path.join(self.data_dir, "plugin_dep", "plugin.json")
        with self.entrypoint_plugins(("ep-pkg", manifest)):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])

    def test_entrypoint_and_dep_plugin_deduped(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        dep_root = self.path("packages", "plugin_dep")
        with self.entrypoint_plugins(("ep-pkg", lambda: dep_root)):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), ["demo-plugin"])

    def test_entrypoint_invalid_plugin_warns_and_skips(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_ep_bad
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        bogus = os.path.join(self.data_dir, "agents_leaf1")
        with self.entrypoint_plugins(("ep-pkg", bogus)):
            with self.assertLogs("ivpm.handlers.package_handler_agents",
                                 level="WARNING"):
                self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), [])


class TestMcp(PluginTestBase):

    def test_mcp_not_emitted_by_default(self):
        """Opt-in: a dependency's MCP servers name executables, so nothing is
        wired up without the project asking."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_mcp_dep"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.claude_skills(), ["mcp-plugin"])
        self.assertFalse(os.path.exists(
            self.path(".claude", "skills", "mcp-plugin", ".mcp.json")))

    def test_mcp_emitted_when_enabled(self):
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_mcp_dep",
            with_block="\n    with:\n        agents:\n            mcp: true"))
        self.ivpm_update(skip_venv=True)

        path = self.path(".claude", "skills", "mcp-plugin", ".mcp.json")
        self.assertTrue(os.path.isfile(path))
        servers = json.load(open(path))["mcpServers"]
        self.assertEqual(sorted(servers), ["local", "remote"])

    def test_mcp_placeholders_translated(self):
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_mcp_dep",
            with_block="\n    with:\n        agents:\n            mcp: true"))
        self.ivpm_update(skip_venv=True)

        servers = json.load(open(self.path(
            ".claude", "skills", "mcp-plugin", ".mcp.json")))["mcpServers"]
        local = servers["local"]

        # ${PLUGIN_ROOT} and './' both become the tool's own root placeholder
        self.assertEqual(local["command"], "${CLAUDE_PLUGIN_ROOT}/bin/srv")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", local["args"])
        self.assertNotIn("${PLUGIN_ROOT}", json.dumps(servers))

        # ${PLUGIN_DATA} has no equivalent, so it resolves to a real directory
        data_arg = local["args"][local["args"].index("--data") + 1]
        self.assertTrue(os.path.isdir(data_arg))
        self.assertTrue(data_arg.endswith(os.path.join(".agents", "data", "mcp-plugin")))

    def test_mcp_http_server_passthrough(self):
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_mcp_dep",
            with_block="\n    with:\n        agents:\n            mcp: true"))
        self.ivpm_update(skip_venv=True)

        servers = json.load(open(self.path(
            ".claude", "skills", "mcp-plugin", ".mcp.json")))["mcpServers"]
        self.assertEqual(servers["remote"],
                         {"type": "streamable-http", "url": "https://example.com/mcp"})

    def test_plugin_data_survives_a_second_update(self):
        """The specification requires plugin data to outlive updates."""
        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_mcp_dep",
            with_block="\n    with:\n        agents:\n            mcp: true"))
        self.ivpm_update(skip_venv=True)

        marker = self.path(".agents", "data", "mcp-plugin", "state.db")
        with open(marker, "w") as fh:
            fh.write("important")

        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(marker), "plugin data must survive an update")


class TestCleanup(PluginTestBase):

    def test_stale_plugin_removed_when_dep_drops_it(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.agents_plugins(), ["demo-plugin"])

        self.mkFile("ivpm.yaml", dep_yaml("agents_leaf1"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.agents_plugins(), [])
        self.assertEqual(self.claude_skills(), ["agents_leaf1"])

    def test_installed_plugin_removed_on_disable(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.claude_skills(), ["demo-plugin"])

        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_dep",
            with_block="\n    with:\n        agents:\n            claude: false"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.claude_skills(), [])

    def test_switching_strategy_leaves_no_duplicates(self):
        """install -> unbundle must not leave the installed plugin behind."""
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.claude_skills(), ["demo-plugin"])

        self.mkFile("ivpm.yaml", dep_yaml(
            "plugin_dep",
            with_block="\n    with:\n        agents:\n            plugin_install: false"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.claude_skills(),
                         ["demo-plugin-alpha", "demo-plugin-beta"])

    def test_agents_data_not_removed_by_cleanup(self):
        self.mkFile("ivpm.yaml", dep_yaml("plugin_dep"))
        self.ivpm_update(skip_venv=True)

        data = self.path(".agents", "data", "some-plugin")
        os.makedirs(data, exist_ok=True)
        with open(os.path.join(data, "keep"), "w") as fh:
            fh.write("x")

        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isfile(os.path.join(data, "keep")))

    def test_no_plugins_no_plugins_dir(self):
        self.mkFile("ivpm.yaml", dep_yaml("agents_leaf1"))
        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.exists(self.path(".agents", "plugins")))


if __name__ == "__main__":
    unittest.main()
