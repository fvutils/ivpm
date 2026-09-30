"""Tests for ivpm.agent_plugins.marketplace -- catalog parsing and mapping.

Pure: nothing is fetched. The ``marketplace`` package source is tested in
test_src_marketplace.py.
"""
import json
import os
import shutil
import tempfile
import unittest

from ivpm.agent_plugins import marketplace as mk

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA256 = "ab" * 32


def catalog(*plugins, **kw):
    doc = {"name": "acme", "owner": {"name": "Acme"}, "plugins": list(plugins)}
    doc.update(kw)
    return doc


class MarketplaceTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-mkt-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def codes(self, diags):
        return [d.code for d in diags]

    def parse(self, doc):
        return mk.parse(doc, "marketplace.json", self.tmp)

    def only(self, doc):
        m = self.parse(doc)
        self.assertEqual(len(m.entries), 1, m.diagnostics)
        return m.entries[0], m


class TestLocate(MarketplaceTestBase):

    def write(self, rel, doc):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc, fh)
        return path

    def test_lookup_order(self):
        self.write("marketplace.json", catalog(name="plain"))
        self.write(".agents/plugins/marketplace.json", catalog(name="agents"))
        self.assertTrue(mk.find_catalog(self.tmp).endswith(
            os.path.join(".agents", "plugins", "marketplace.json")))
        self.write(".claude-plugin/marketplace.json", catalog(name="claude"))
        self.assertTrue(mk.find_catalog(self.tmp).endswith(
            os.path.join(".claude-plugin", "marketplace.json")))

    def test_override(self):
        self.write("catalogs/team.json", catalog())
        self.assertIsNotNone(mk.find_catalog(self.tmp, "catalogs/team.json"))
        self.assertIsNone(mk.find_catalog(self.tmp, "catalogs/missing.json"))

    def test_none(self):
        self.assertIsNone(mk.find_catalog(self.tmp))

    def test_root_for_catalog(self):
        root = os.path.abspath(self.tmp)
        self.assertEqual(mk.root_for_catalog(
            self.write(".claude-plugin/marketplace.json", catalog())), root)
        self.assertEqual(mk.root_for_catalog(
            self.write(".agents/plugins/marketplace.json", catalog())), root)
        self.assertEqual(mk.root_for_catalog(
            self.write("marketplace.json", catalog())), root)

    def test_load(self):
        path = self.write(".claude-plugin/marketplace.json",
                          catalog({"name": "a", "source": "./a"}, description="Tools"))
        m = mk.load(path, self.tmp)
        self.assertEqual((m.name, m.description), ("acme", "Tools"))
        self.assertEqual([e.name for e in m.entries], ["a"])

    def test_load_unreadable(self):
        path = os.path.join(self.tmp, "marketplace.json")
        with open(path, "w") as fh:
            fh.write("{not json")
        m = mk.load(path, self.tmp)
        self.assertEqual(self.codes(m.diagnostics), ["marketplace.invalid"])


class TestParse(MarketplaceTestBase):

    def test_missing_name_or_plugins(self):
        self.assertEqual(self.codes(self.parse({"plugins": []}).diagnostics),
                         ["marketplace.invalid"])
        self.assertEqual(self.codes(self.parse({"name": "x"}).diagnostics),
                         ["marketplace.invalid"])
        self.assertEqual(self.codes(self.parse([]).diagnostics), ["marketplace.invalid"])

    def test_entry_fields(self):
        e, _ = self.only(catalog({"name": "fmt", "source": "./fmt", "description": "d",
                                  "version": "1.0", "category": "style",
                                  "strict": False, "keywords": ["x"]}))
        self.assertEqual((e.description, e.version, e.category), ("d", "1.0", "style"))
        self.assertFalse(e.strict)
        self.assertEqual(e.fields["keywords"], ["x"])

    def test_invalid_entry_name_skipped(self):
        m = self.parse(catalog({"name": "Bad Name", "source": "./x"},
                               {"name": "good", "source": "./y"}))
        self.assertEqual([e.name for e in m.entries], ["good"])
        self.assertIn("marketplace.invalid-entry", self.codes(m.diagnostics))

    def test_duplicate_entry(self):
        m = self.parse(catalog({"name": "a", "source": "./one"},
                               {"name": "a", "source": "./two"}))
        self.assertEqual([e.source["path"] for e in m.entries], ["one"])
        self.assertIn("marketplace.duplicate-entry", self.codes(m.diagnostics))


class TestSources(MarketplaceTestBase):

    def test_relative(self):
        e, _ = self.only(catalog({"name": "a", "source": "./plugins/a"}))
        self.assertEqual((e.source_kind, e.source), ("relative", {"path": "plugins/a"}))
        self.assertTrue(e.supported)

    def test_plugin_root_prefixes_bare_names(self):
        e, _ = self.only(catalog({"name": "a", "source": "a"},
                                 metadata={"pluginRoot": "./plugins"}))
        self.assertEqual(e.source["path"], "plugins/a")

    def test_plugin_root_leaves_dot_paths(self):
        e, _ = self.only(catalog({"name": "a", "source": "./elsewhere/a"},
                                 metadata={"pluginRoot": "./plugins"}))
        self.assertEqual(e.source["path"], "elsewhere/a")

    def test_relative_escape_rejected(self):
        e, m = self.only(catalog({"name": "a", "source": "./../outside"}))
        self.assertEqual(e.source_kind, "unknown")
        self.assertIn("marketplace.invalid-source", self.codes(m.diagnostics))

    def test_codex_local(self):
        e, _ = self.only(catalog({"name": "a", "source": {"source": "local", "path": "./a"}}))
        self.assertEqual((e.source_kind, e.source["path"]), ("relative", "a"))

    def test_github(self):
        e, _ = self.only(catalog({"name": "a", "source": {
            "source": "github", "repo": "acme/a", "ref": "v1", "sha": SHA}}))
        self.assertEqual(e.source_kind, "github")
        self.assertEqual(e.source, {"url": "https://github.com/acme/a.git",
                                    "ref": "v1", "sha": SHA})

    def test_github_bad_repo(self):
        e, _ = self.only(catalog({"name": "a", "source": {"source": "github", "repo": "a"}}))
        self.assertEqual(e.source_kind, "unknown")

    def test_short_sha_dropped(self):
        e, m = self.only(catalog({"name": "a", "source": {
            "source": "url", "url": "https://x/a.git", "sha": "abc123"}}))
        self.assertNotIn("sha", e.source)
        self.assertIn("marketplace.invalid-field", self.codes(m.diagnostics))

    def test_url(self):
        e, _ = self.only(catalog({"name": "a", "source": {
            "source": "url", "url": "https://gitlab.com/t/a.git"}}))
        self.assertEqual((e.source_kind, e.source["url"]), ("git", "https://gitlab.com/t/a.git"))

    def test_git_subdir(self):
        e, _ = self.only(catalog({"name": "a", "source": {
            "source": "git-subdir", "url": "https://x/mono.git", "path": "tools/a/"}}))
        self.assertEqual(e.source_kind, "git-subdir")
        self.assertEqual(e.source["path"], "tools/a")

    def test_git_subdir_escape(self):
        e, _ = self.only(catalog({"name": "a", "source": {
            "source": "git-subdir", "url": "https://x/mono.git", "path": "../a"}}))
        self.assertEqual(e.source_kind, "unknown")

    def test_archive(self):
        e, m = self.only(catalog({"name": "a", "source": {
            "source": "archive", "url": "https://x/a.zip", "sha256": SHA256.upper()}}))
        self.assertEqual(e.source, {"url": "https://x/a.zip", "sha256": SHA256})
        self.assertEqual(m.diagnostics, ())

    def test_archive_without_checksum_warns(self):
        _, m = self.only(catalog({"name": "a", "source": {
            "source": "archive", "url": "https://x/a.zip"}}))
        self.assertIn("marketplace.no-checksum", self.codes(m.diagnostics))

    def test_archive_requires_https(self):
        e, _ = self.only(catalog({"name": "a", "source": {
            "source": "archive", "url": "http://x/a.zip"}}))
        self.assertEqual(e.source_kind, "unknown")

    def test_npm_command_unknown_unsupported(self):
        m = self.parse(catalog(
            {"name": "n", "source": {"source": "npm", "package": "@a/n"}},
            {"name": "c", "source": {"source": "command", "command": "make"}},
            {"name": "u", "source": {"source": "future-thing"}}))
        self.assertEqual([(e.source_kind, e.supported) for e in m.entries],
                         [("npm", False), ("command", False), ("unknown", False)])


class TestSelect(MarketplaceTestBase):

    def setUp(self):
        super().setUp()
        self.m = self.parse(catalog(*({"name": n, "source": "./" + n}
                                      for n in ("lint", "lsp-py", "lsp-rs", "format"))))

    def test_names_and_globs_in_catalog_order(self):
        chosen, diags = mk.select(self.m, ["format", "lsp-*"])
        self.assertEqual([e.name for e in chosen], ["lsp-py", "lsp-rs", "format"])
        self.assertEqual(diags, [])

    def test_no_match_suggests(self):
        _, diags = mk.select(self.m, ["formatt"])
        self.assertEqual(self.codes(diags), ["marketplace.no-match"])
        self.assertIn("did you mean 'format'", diags[0].message)


class TestToDep(MarketplaceTestBase):

    def dep(self, source, **kw):
        e, _ = self.only(catalog({"name": "a", "source": source}))
        return mk.to_dep(e, **kw)

    def test_relative_is_not_a_dep(self):
        self.assertEqual(self.dep("./a"), (None, []))

    def test_github_with_sha(self):
        opts, _ = self.dep({"source": "github", "repo": "acme/a", "ref": "main", "sha": SHA})
        self.assertEqual(opts, {"name": "a", "src": "git",
                                "url": "https://github.com/acme/a.git",
                                "commit": SHA, "agents": {"plugins": ["."]}})

    def test_ref_defaults_to_branch(self):
        opts, _ = self.dep({"source": "url", "url": "https://x/a.git", "ref": "dev"})
        self.assertEqual(opts["branch"], "dev")

    def test_ref_classified(self):
        opts, _ = self.dep({"source": "url", "url": "https://x/a.git", "ref": "v1"},
                           classify_ref=lambda url, ref: "tag")
        self.assertEqual(opts["tag"], "v1")
        self.assertNotIn("branch", opts)

    def test_git_subdir_names_the_plugin(self):
        opts, _ = self.dep({"source": "git-subdir", "url": "https://x/mono.git",
                            "path": "tools/a"})
        self.assertEqual(opts["agents"], {"plugins": ["tools/a"]})

    def test_archive(self):
        opts, _ = self.dep({"source": "archive", "url": "https://x/a.zip", "sha256": SHA256})
        self.assertEqual((opts["src"], opts["sha256"]), ("http", SHA256))

    def test_command_rejected(self):
        opts, diags = self.dep({"source": "command", "command": "make"})
        self.assertIsNone(opts)
        self.assertEqual(self.codes(diags), ["marketplace.command-source"])

    def test_npm_unsupported(self):
        opts, diags = self.dep({"source": "npm", "package": "@a/n"})
        self.assertIsNone(opts)
        self.assertEqual(self.codes(diags), ["marketplace.unsupported-source"])


if __name__ == "__main__":
    unittest.main()
