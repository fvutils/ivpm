"""Unit tests for Agent Plugins manifest loading and validation.

These tests never run a project update -- they exercise
``ivpm.agent_plugins`` directly against manifests written into a temp dir.
Manifests are built inline rather than checked in as fixtures so that each
assertion sits next to the document it is about; the on-disk fixtures under
``data/`` are for the handler-level tests, where a real dependency package is
needed.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.agent_plugins import (
    Classification, PluginManifest, classify, iter_skill_dirs, load_plugin,
    normalize_plugin_path, within,
)
from ivpm.agent_plugins import mcp as mcp_mod
from ivpm.agent_plugins import validate as v

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def minimal(name="my-plugin", **kw):
    doc = {"$schema": PLUGIN_SCHEMA, "name": name}
    doc.update(kw)
    return doc


class PluginTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-plugin-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mkplugin(self, doc, subdir="p", raw=None):
        """Write a plugin.json (dict via ``doc``, or literal text via ``raw``)."""
        root = os.path.join(self.tmp, subdir)
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "plugin.json"), "w") as fh:
            fh.write(raw if raw is not None else json.dumps(doc, indent=2))
        return root

    def mkskill(self, root, name, body="skill body"):
        d = os.path.join(root, "skills", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w") as fh:
            fh.write("---\nname: %s\ndescription: A test skill.\n---\n\n%s\n"
                     % (name, body))
        return d

    def codes(self, diags):
        return [d.code for d in diags]

    def severities(self, diags):
        return {d.severity for d in diags}


# ---------------------------------------------------------------------------
# Classification -- "is this even an Agent Plugins manifest?"
# ---------------------------------------------------------------------------

class TestClassify(PluginTestBase):

    def classify_root(self, root):
        return classify(os.path.join(root, "plugin.json"))

    def test_classify_valid(self):
        kind, ver = self.classify_root(self.mkplugin(minimal()))
        self.assertIs(kind, Classification.PLUGIN)
        self.assertEqual(ver, "1.0.0")

    def test_classify_missing_file(self):
        kind, ver = classify(os.path.join(self.tmp, "nope", "plugin.json"))
        self.assertIs(kind, Classification.NOT_A_PLUGIN)
        self.assertIsNone(ver)

    def test_classify_invalid_json(self):
        root = self.mkplugin(None, raw="{ this is not json ")
        self.assertIs(self.classify_root(root)[0], Classification.NOT_A_PLUGIN)

    def test_classify_json_array_toplevel(self):
        root = self.mkplugin(None, raw='["not", "an", "object"]')
        self.assertIs(self.classify_root(root)[0], Classification.NOT_A_PLUGIN)

    def test_classify_no_schema_key(self):
        root = self.mkplugin(None, raw=json.dumps({"name": "x"}))
        self.assertIs(self.classify_root(root)[0], Classification.NOT_A_PLUGIN)

    def test_classify_schema_non_string(self):
        root = self.mkplugin(None, raw=json.dumps({"$schema": 42, "name": "x"}))
        self.assertIs(self.classify_root(root)[0], Classification.NOT_A_PLUGIN)

    def test_classify_foreign_schema(self):
        """A Grafana/Backstage-style plugin.json must not be claimed as ours.

        This is the central false-positive guard: 'plugin.json' is a heavily
        overloaded filename, so only $schema may decide.
        """
        root = self.mkplugin(None, raw=json.dumps({
            "$schema": "https://example.com/schemas/plugin.json",
            "id": "acme-datasource",
            "type": "datasource",
        }))
        kind, ver = self.classify_root(root)
        self.assertIs(kind, Classification.NOT_A_PLUGIN)
        self.assertIsNone(ver)

    def test_classify_schema_wrong_host(self):
        root = self.mkplugin(None, raw=json.dumps({
            "$schema": "https://evil.example/schemas/1.0.0/plugin.schema.json",
            "name": "x",
        }))
        self.assertIs(self.classify_root(root)[0], Classification.NOT_A_PLUGIN)

    def test_classify_future_version(self):
        root = self.mkplugin(None, raw=json.dumps({
            "$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json",
            "name": "x",
        }))
        kind, ver = self.classify_root(root)
        self.assertIs(kind, Classification.UNSUPPORTED_VERSION)
        self.assertEqual(ver, "2.0.0")

    def test_classify_wrong_filename(self):
        """classify() only ever considers a file literally named plugin.json."""
        root = self.mkplugin(minimal())
        other = os.path.join(root, "manifest.json")
        shutil.copy(os.path.join(root, "plugin.json"), other)
        self.assertIs(classify(other)[0], Classification.NOT_A_PLUGIN)


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

class TestName(PluginTestBase):

    VALID = ["a", "9", "my-plugin", "my.plugin", "a1.b2-c3", "a" * 64,
             "a-b.c-d", "x0"]
    INVALID = ["", "a" * 65, "Upper", "-lead", "trail-", ".lead", "trail.",
               "a--b", "a..b", "under_score", "sp ace", "sla/sh", "uniçode"]

    def test_valid_names_accepted(self):
        for name in self.VALID:
            with self.subTest(name=name):
                manifest, diags = load_plugin(self.mkplugin(minimal(name), subdir=os.urandom(6).hex()))
                self.assertIsNotNone(manifest, "%r should be valid: %s"
                                     % (name, [str(d) for d in diags]))
                self.assertEqual(manifest.name, name)

    def test_invalid_names_rejected(self):
        for name in self.INVALID:
            with self.subTest(name=name):
                manifest, diags = load_plugin(self.mkplugin(minimal(name), subdir=os.urandom(6).hex()))
                self.assertIsNone(manifest, "%r should be rejected" % name)
                self.assertTrue(any(d.code.startswith("name.") for d in diags),
                                "expected a name.* diagnostic, got %s" % self.codes(diags))
                self.assertIn("error", self.severities(diags))

    def test_name_charset_code(self):
        _, diags = load_plugin(self.mkplugin(minimal("Bad--Name")))
        self.assertIn("name.charset", self.codes(diags))

    def test_name_length_code(self):
        _, diags = load_plugin(self.mkplugin(minimal("a" * 65)))
        self.assertIn("name.length", self.codes(diags))

    def test_name_missing(self):
        root = self.mkplugin(None, raw=json.dumps({"$schema": PLUGIN_SCHEMA}))
        manifest, diags = load_plugin(root)
        self.assertIsNone(manifest)
        self.assertIn("name.missing", self.codes(diags))

    def test_name_wrong_type(self):
        root = self.mkplugin(None, raw=json.dumps({"$schema": PLUGIN_SCHEMA, "name": 7}))
        manifest, diags = load_plugin(root)
        self.assertIsNone(manifest)
        self.assertIn("name.type", self.codes(diags))

    def test_error_message_names_the_offending_value(self):
        _, diags = load_plugin(self.mkplugin(minimal("Bad--Name")))
        msg = " ".join(d.message for d in diags)
        self.assertIn("Bad--Name", msg)


# ---------------------------------------------------------------------------
# Loading and failure isolation
# ---------------------------------------------------------------------------

class TestLoad(PluginTestBase):

    def test_load_minimal(self):
        manifest, diags = load_plugin(self.mkplugin(minimal()))
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.name, "my-plugin")
        self.assertEqual(manifest.spec_version, "1.0.0")
        self.assertEqual(diags, [])

    def test_load_full_metadata(self):
        root = self.mkplugin(minimal(
            version="1.2.3",
            description="Does things.",
            author={"name": "A Dev", "email": "dev@example.com"},
            homepage="https://example.com",
            repository="https://github.com/example/x",
            license="Apache-2.0",
            keywords=["a", "b"],
            extensions={"com.example.client": {"k": 1}},
        ))
        manifest, diags = load_plugin(root)
        self.assertEqual(diags, [])
        self.assertEqual(manifest.version, "1.2.3")
        self.assertEqual(manifest.keywords, ("a", "b"))
        self.assertEqual(manifest.author["name"], "A Dev")
        self.assertEqual(manifest.extensions["com.example.client"], {"k": 1})

    def test_not_a_plugin_is_info_not_error(self):
        """An unrelated plugin.json must not produce a warning or an error."""
        root = self.mkplugin(None, raw=json.dumps({"$schema": "https://x/y.json"}))
        manifest, diags = load_plugin(root)
        self.assertIsNone(manifest)
        self.assertEqual(self.severities(diags), {"info"})
        self.assertIn("manifest.not-a-plugin", self.codes(diags))

    def test_unsupported_version_warns(self):
        root = self.mkplugin(None, raw=json.dumps({
            "$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json",
            "name": "x"}))
        manifest, diags = load_plugin(root)
        self.assertIsNone(manifest)
        self.assertIn("manifest.unsupported-version", self.codes(diags))
        self.assertIn("warning", self.severities(diags))
        self.assertIn("2.0.0", " ".join(d.message for d in diags))

    def test_unknown_toplevel_key_ignored(self):
        """Spec: report and ignore unknown fields; do not reject the plugin."""
        root = self.mkplugin(minimal(mystery={"a": 1}, another="x"))
        manifest, diags = load_plugin(root)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.unknown_keys, ("another", "mystery"))
        self.assertEqual(self.severities(diags), {"info"})
        self.assertNotIn("error", self.severities(diags))

    def test_extensions_non_object_ignored(self):
        root = self.mkplugin(minimal(extensions="nope"))
        manifest, diags = load_plugin(root)
        self.assertIsNotNone(manifest, "a bad 'extensions' must not reject the plugin")
        self.assertEqual(dict(manifest.extensions), {})
        self.assertIn("warning", self.severities(diags))

    def test_extensions_unknown_namespace_not_inspected(self):
        """Clients must ignore namespaces they do not implement, uninspected."""
        root = self.mkplugin(minimal(extensions={
            "com.example.other": {"deeply": {"nested": ["garbage", 1, None]}}}))
        manifest, diags = load_plugin(root)
        self.assertIsNotNone(manifest)
        self.assertEqual(diags, [])
        self.assertIn("com.example.other", manifest.extensions)

    def test_optional_field_wrong_type_dropped(self):
        root = self.mkplugin(minimal(keywords="a", version="1.0"))
        manifest, diags = load_plugin(root)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.keywords, ())
        self.assertEqual(manifest.version, "1.0", "sibling fields must survive")
        self.assertIn("field.invalid", self.codes(diags))

    def test_author_unknown_subkey_drops_author_only(self):
        root = self.mkplugin(minimal(author={"name": "A", "twitter": "@a"},
                                     description="kept"))
        manifest, diags = load_plugin(root)
        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.author)
        self.assertEqual(manifest.description, "kept")

    def test_diagnostics_are_returned_not_printed(self):
        """The module must not write to stdout/stderr; callers choose the sink."""
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            load_plugin(self.mkplugin(minimal("Bad--Name")))
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


# ---------------------------------------------------------------------------
# Path normalization -- the extension-point contract
# ---------------------------------------------------------------------------

class TestNormalize(PluginTestBase):

    def test_normalize_manifest_path(self):
        root = self.mkplugin(minimal())
        self.assertEqual(normalize_plugin_path(os.path.join(root, "plugin.json")), root)

    def test_normalize_root_dir(self):
        root = self.mkplugin(minimal())
        self.assertEqual(normalize_plugin_path(root), root)

    def test_normalize_dir_without_manifest(self):
        d = os.path.join(self.tmp, "bare")
        os.makedirs(d)
        self.assertIsNone(normalize_plugin_path(d))

    def test_normalize_manifest_path_not_a_file(self):
        self.assertIsNone(normalize_plugin_path(
            os.path.join(self.tmp, "nowhere", "plugin.json")))

    def test_normalize_directory_named_plugin_json(self):
        """A *directory* called plugin.json is not a manifest."""
        d = os.path.join(self.tmp, "odd", "plugin.json")
        os.makedirs(d)
        self.assertIsNone(normalize_plugin_path(d))

    def test_normalize_empty(self):
        self.assertIsNone(normalize_plugin_path(""))

    def test_normalize_relative_path(self):
        root = self.mkplugin(minimal())
        cwd = os.getcwd()
        try:
            os.chdir(self.tmp)
            self.assertEqual(normalize_plugin_path(os.path.join("p", "plugin.json")), root)
        finally:
            os.chdir(cwd)


class TestWithin(PluginTestBase):

    def test_within_self_and_child(self):
        child = os.path.join(self.tmp, "a", "b")
        os.makedirs(child)
        self.assertTrue(within(self.tmp, self.tmp))
        self.assertTrue(within(self.tmp, child))

    def test_within_rejects_sibling_and_parent(self):
        a = os.path.join(self.tmp, "a")
        b = os.path.join(self.tmp, "b")
        os.makedirs(a)
        os.makedirs(b)
        self.assertFalse(within(a, b))
        self.assertFalse(within(a, self.tmp))

    def test_within_rejects_prefix_lookalike(self):
        """'/x/root-evil' must not count as inside '/x/root'."""
        root = os.path.join(self.tmp, "root")
        evil = os.path.join(self.tmp, "root-evil")
        os.makedirs(root)
        os.makedirs(evil)
        self.assertFalse(within(root, evil))

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlinks")
    def test_within_follows_symlinks(self):
        root = os.path.join(self.tmp, "root")
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(root)
        os.makedirs(outside)
        link = os.path.join(root, "link")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported here")
        self.assertFalse(within(root, link))


# ---------------------------------------------------------------------------
# Skill discovery within a plugin
# ---------------------------------------------------------------------------

class TestSkillDirs(PluginTestBase):

    def load(self, root):
        manifest, _ = load_plugin(root)
        self.assertIsNotNone(manifest)
        return iter_skill_dirs(manifest)

    def test_no_skills_dir_is_not_an_error(self):
        dirs, diags = self.load(self.mkplugin(minimal()))
        self.assertEqual(dirs, [])
        self.assertEqual(diags, [])

    def test_immediate_subdirs_found(self):
        root = self.mkplugin(minimal())
        self.mkskill(root, "beta")
        self.mkskill(root, "alpha")
        dirs, diags = self.load(root)
        self.assertEqual([os.path.basename(d) for d in dirs], ["alpha", "beta"])
        self.assertEqual(diags, [])

    def test_nested_skill_not_discovered(self):
        """Per spec, only immediate subdirectories of skills/ are skills."""
        root = self.mkplugin(minimal())
        deep = os.path.join(root, "skills", "outer", "inner")
        os.makedirs(deep)
        with open(os.path.join(deep, "SKILL.md"), "w") as fh:
            fh.write("---\nname: inner\ndescription: d\n---\n")
        dirs, diags = self.load(root)
        self.assertEqual(dirs, [])
        self.assertIn("skills.no-skill-md", [d.code for d in diags])

    def test_skills_is_a_file(self):
        root = self.mkplugin(minimal())
        with open(os.path.join(root, "skills"), "w") as fh:
            fh.write("oops")
        dirs, diags = self.load(root)
        self.assertEqual(dirs, [])
        self.assertIn("skills.not-a-directory", [d.code for d in diags])

    def test_subdir_missing_skill_md_skipped_siblings_kept(self):
        root = self.mkplugin(minimal())
        self.mkskill(root, "good")
        os.makedirs(os.path.join(root, "skills", "empty"))
        dirs, diags = self.load(root)
        self.assertEqual([os.path.basename(d) for d in dirs], ["good"])
        self.assertIn("skills.no-skill-md", [d.code for d in diags])

    def test_skill_md_must_be_a_regular_file(self):
        root = self.mkplugin(minimal())
        os.makedirs(os.path.join(root, "skills", "weird", "SKILL.md"))
        dirs, diags = self.load(root)
        self.assertEqual(dirs, [])

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlinks")
    def test_skill_symlink_inside_root_allowed(self):
        root = self.mkplugin(minimal())
        real = os.path.join(root, "elsewhere")
        os.makedirs(real)
        with open(os.path.join(real, "SKILL.md"), "w") as fh:
            fh.write("---\nname: s\ndescription: d\n---\n")
        os.makedirs(os.path.join(root, "skills"))
        try:
            os.symlink(real, os.path.join(root, "skills", "linked"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported here")
        dirs, diags = self.load(root)
        self.assertEqual([os.path.basename(d) for d in dirs], ["linked"])
        self.assertEqual(diags, [])

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlinks")
    def test_skill_symlink_escaping_root_rejected(self):
        root = self.mkplugin(minimal())
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "SKILL.md"), "w") as fh:
            fh.write("---\nname: s\ndescription: d\n---\n")
        os.makedirs(os.path.join(root, "skills"))
        try:
            os.symlink(outside, os.path.join(root, "skills", "evil"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported here")
        dirs, diags = self.load(root)
        self.assertEqual(dirs, [])
        self.assertIn("skills.escapes-root", [d.code for d in diags])


# ---------------------------------------------------------------------------
# mcp.json
# ---------------------------------------------------------------------------

class TestMcp(PluginTestBase):

    def mkmcp(self, servers, subdir="p", schema=MCP_SCHEMA, plugin_doc=None):
        root = self.mkplugin(plugin_doc or minimal(), subdir=subdir)
        doc = {"$schema": schema, "mcpServers": servers}
        with open(os.path.join(root, "mcp.json"), "w") as fh:
            fh.write(json.dumps(doc, indent=2))
        manifest, _ = load_plugin(root)
        self.assertIsNotNone(manifest)
        return mcp_mod.load_mcp(manifest)

    def test_absent_mcp_is_not_an_error(self):
        manifest, _ = load_plugin(self.mkplugin(minimal()))
        cfg, diags = mcp_mod.load_mcp(manifest)
        self.assertIsNone(cfg)
        self.assertEqual(diags, [])

    def test_stdio_server(self):
        cfg, diags = self.mkmcp({"srv": {
            "type": "stdio", "command": "my-server",
            "args": ["--port", "1234"], "env": {"TOKEN": "shhh"}}})
        self.assertEqual(diags, [])
        self.assertEqual(len(cfg.servers), 1)
        srv = cfg.servers[0]
        self.assertEqual(srv.command, "my-server")
        self.assertEqual(srv.args, ("--port", "1234"))
        self.assertEqual(srv.env["TOKEN"], "shhh")

    def test_streamable_http_server(self):
        cfg, diags = self.mkmcp({"api": {
            "type": "streamable-http", "url": "https://example.com/mcp",
            "headers": {"X-A": "b"}}})
        self.assertEqual(diags, [])
        self.assertEqual(cfg.servers[0].type, "streamable-http")
        self.assertFalse(cfg.servers[0].legacy)

    def test_sse_flagged_legacy(self):
        cfg, _ = self.mkmcp({"old": {"type": "sse", "url": "https://example.com/sse"}})
        self.assertTrue(cfg.servers[0].legacy)

    def test_version_skew_invalidates_mcp_not_plugin(self):
        root = self.mkplugin(minimal())
        with open(os.path.join(root, "mcp.json"), "w") as fh:
            fh.write(json.dumps({
                "$schema": "https://agent-plugins.org/schemas/2.0.0/mcp.schema.json",
                "mcpServers": {}}))
        manifest, pdiags = load_plugin(root)
        self.assertIsNotNone(manifest, "plugin must remain valid")
        self.assertEqual(pdiags, [])
        cfg, diags = mcp_mod.load_mcp(manifest)
        self.assertIsNone(cfg)
        self.assertIn("mcp.version-skew", [d.code for d in diags])

    def test_shell_string_command_rejected(self):
        cfg, diags = self.mkmcp({"srv": {"type": "stdio", "command": "sh -c foo"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.command-not-a-token", [d.code for d in diags])

    def test_relative_command_accepted(self):
        cfg, diags = self.mkmcp({"srv": {"type": "stdio", "command": "./bin/srv"}})
        self.assertEqual(diags, [])
        self.assertEqual(cfg.servers[0].command, "./bin/srv")

    def test_command_escaping_root_rejected(self):
        cfg, diags = self.mkmcp({"srv": {"type": "stdio", "command": "./../../evil"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.command-escapes-root", [d.code for d in diags])

    def test_absolute_command_rejected(self):
        cfg, diags = self.mkmcp({"srv": {"type": "stdio", "command": "/usr/bin/evil"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.command-path", [d.code for d in diags])

    def test_cwd_must_be_relative_or_placeholder(self):
        cfg, diags = self.mkmcp({"srv": {
            "type": "stdio", "command": "srv", "cwd": "/tmp"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.invalid", [d.code for d in diags])

    def test_cwd_placeholder_accepted(self):
        cfg, diags = self.mkmcp({"srv": {
            "type": "stdio", "command": "srv", "cwd": "${PLUGIN_DATA}/work"}})
        self.assertEqual(diags, [])
        self.assertEqual(cfg.servers[0].cwd, "${PLUGIN_DATA}/work")

    def test_cwd_escaping_root_rejected(self):
        cfg, diags = self.mkmcp({"srv": {
            "type": "stdio", "command": "srv", "cwd": "./../../elsewhere"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.cwd-escapes-root", [d.code for d in diags])

    def test_reserved_env_keys_rejected(self):
        for key in ("PLUGIN_ROOT", "PLUGIN_DATA"):
            with self.subTest(key=key):
                cfg, diags = self.mkmcp(
                    {"srv": {"type": "stdio", "command": "srv", "env": {key: "x"}}},
                    subdir=os.urandom(6).hex())
                self.assertEqual(cfg.servers, ())
                self.assertIn("server.invalid", [d.code for d in diags])

    def test_http_requires_https_for_non_loopback(self):
        cfg, diags = self.mkmcp({"api": {
            "type": "streamable-http", "url": "http://example.com/mcp"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.url-insecure", [d.code for d in diags])

    def test_http_allowed_for_loopback(self):
        for url in ("http://localhost:8080/mcp", "http://127.0.0.1:8080/mcp",
                    "http://127.0.0.5:8080/mcp"):
            with self.subTest(url=url):
                cfg, diags = self.mkmcp(
                    {"api": {"type": "streamable-http", "url": url}},
                    subdir=os.urandom(6).hex())
                self.assertEqual(diags, [])
                self.assertEqual(len(cfg.servers), 1)

    def test_non_absolute_url_rejected(self):
        cfg, diags = self.mkmcp({"api": {"type": "streamable-http", "url": "/mcp"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.url-invalid", [d.code for d in diags])

    def test_unknown_transport_skipped_siblings_kept(self):
        cfg, diags = self.mkmcp({
            "good": {"type": "stdio", "command": "srv"},
            "weird": {"type": "carrier-pigeon", "url": "https://x/y"},
        })
        self.assertEqual([s.name for s in cfg.servers], ["good"])
        self.assertIn("server.unsupported-transport", [d.code for d in diags])

    def test_invalid_server_skipped_siblings_kept(self):
        cfg, diags = self.mkmcp({
            "good": {"type": "stdio", "command": "srv"},
            "bad": {"type": "stdio"},               # missing 'command'
        })
        self.assertEqual([s.name for s in cfg.servers], ["good"])
        self.assertIn("server.invalid", [d.code for d in diags])

    def test_server_missing_type(self):
        cfg, diags = self.mkmcp({"srv": {"command": "srv"}})
        self.assertEqual(cfg.servers, ())
        self.assertIn("server.no-type", [d.code for d in diags])

    def test_bad_mcp_schema_id(self):
        cfg, diags = self.mkmcp({}, schema="https://example.com/mcp.json")
        self.assertIsNone(cfg)
        self.assertIn("mcp.bad-schema", [d.code for d in diags])


# ---------------------------------------------------------------------------
# Cross-check the bundled validator against the real jsonschema package
# ---------------------------------------------------------------------------

class TestValidatorAgreesWithJsonschema(unittest.TestCase):
    """The bundled validator must accept/reject exactly what jsonschema does.

    IVPM does not use jsonschema at runtime -- diagnostics must not depend on
    what happens to be installed -- so this test is the guard against the two
    drifting apart.
    """

    PLUGIN_DOCS = [
        minimal(),
        minimal("a" * 64),
        minimal("a" * 65),
        minimal(""),
        minimal("Bad-Name"),
        minimal("a--b"),
        minimal("a..b"),
        minimal("-lead"),
        minimal("trail."),
        minimal(version="1.0", description="d", license="MIT"),
        minimal(keywords=["a", "b"]),
        minimal(keywords="a"),
        minimal(keywords=[1]),
        minimal(author={"name": "A", "email": "e", "url": "u"}),
        minimal(author={"name": "A", "twitter": "@a"}),
        minimal(author="A"),
        minimal(extensions={"com.a.b": {}}),
        minimal(extensions={"com.a.b": "no"}),
        minimal(extensions="no"),
        minimal(surprise=1),
        {"name": "x"},
        {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"},
        {"$schema": "https://example.com/other.json", "name": "x"},
    ]

    MCP_DOCS = [
        {"$schema": MCP_SCHEMA, "mcpServers": {}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {"type": "stdio", "command": "x"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {"type": "stdio"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {"type": "stdio", "command": ""}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "env": {"PLUGIN_ROOT": "y"}}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "env": {"OK": "y"}}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "env": {"OK": 1}}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "cwd": "/abs"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "cwd": "./rel"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "cwd": "${PLUGIN_ROOT}"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "args": ["a"], "extra": 1}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "streamable-http", "url": "https://x/y"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "streamable-http", "url": "https://x/y", "headers": {"A": "b"}}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {"type": "sse", "url": "https://x/y"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {"type": "nope", "url": "https://x/y"}}},
        {"$schema": MCP_SCHEMA, "mcpServers": {"a": {
            "type": "stdio", "command": "x", "url": "https://x/y"}}},
        {"$schema": MCP_SCHEMA},
        {"mcpServers": {}},
    ]

    def _agree(self, docs, kind):
        jsonschema = self._jsonschema()
        schema = v.load_schema("1.0.0", kind)
        validator = jsonschema.Draft202012Validator(schema)
        for doc in docs:
            with self.subTest(doc=json.dumps(doc)[:100]):
                ours = not v.validate(doc, schema)
                theirs = validator.is_valid(doc)
                self.assertEqual(
                    ours, theirs,
                    "bundled validator says %s, jsonschema says %s for %s"
                    % (ours, theirs, json.dumps(doc)))

    def _jsonschema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed; cross-check skipped")
        return jsonschema

    def test_plugin_schema_agreement(self):
        self._agree(self.PLUGIN_DOCS, "plugin")

    def test_mcp_schema_agreement(self):
        self._agree(self.MCP_DOCS, "mcp")


class TestVendoredSchemas(unittest.TestCase):

    def test_schema_ids_match_supported_versions(self):
        from ivpm.agent_plugins.manifest import SPEC_VERSIONS
        for version in SPEC_VERSIONS:
            for kind in ("plugin", "mcp"):
                schema = v.load_schema(version, kind)
                self.assertEqual(
                    schema["$id"],
                    "https://agent-plugins.org/schemas/%s/%s.schema.json" % (version, kind))

    def test_missing_schema_raises(self):
        with self.assertRaises(v.SchemaError):
            v.load_schema("9.9.9", "plugin")


if __name__ == "__main__":
    unittest.main()
