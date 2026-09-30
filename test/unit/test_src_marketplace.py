"""End-to-end tests for ``src: marketplace``.

Every marketplace and plugin here is local: a directory, or a git repository
created in the test and fetched over file://. Nothing touches the network.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from .test_base import TestBase

# Fixed dates make commits a pure function of their content (see the note in
# test_patch_reconcile.py).
GENV = dict(os.environ,
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
            GIT_AUTHOR_DATE="2020-01-01T00:00:00 +0000",
            GIT_COMMITTER_DATE="2020-01-01T00:00:00 +0000")


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content if isinstance(content, str) else json.dumps(content, indent=2))


def skill(root, name):
    write(os.path.join(root, "skills", name, "SKILL.md"),
          "---\nname: %s\ndescription: Test skill %s.\n---\n\nBody.\n" % (name, name))


def claude_plugin(root, name, **extra):
    doc = {"name": name, "description": "Plugin %s." % name}
    doc.update(extra)
    write(os.path.join(root, ".claude-plugin", "plugin.json"), doc)


def git_repo(path, tag=None):
    """Commit everything under ``path``; return the commit hash."""
    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=path, env=GENV, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    if tag:
        git("tag", tag)
    return git("rev-parse", "HEAD")


class MarketplaceTestBase(TestBase):

    def setUp(self):
        super().setUp()
        self.src = tempfile.mkdtemp(prefix="ivpm-mkt-src-")

    def tearDown(self):
        shutil.rmtree(self.src, ignore_errors=True)
        super().tearDown()

    def project(self, dep_block):
        self.mkFile("ivpm.yaml", """
package:
    name: test_marketplace
    dep-sets:
        - name: default-dev
          deps:
%s
""" % "\n".join("            " + line for line in dep_block.strip("\n").splitlines()))

    def listing(self, *parts):
        d = os.path.join(self.testdir, *parts)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def lock(self):
        with open(os.path.join(self.testdir, "packages", "package-lock.json")) as fh:
            return json.load(fh)

    def local_marketplace(self):
        """A marketplace whose plugins all live inside it."""
        root = os.path.join(self.src, "mkt")
        claude_plugin(os.path.join(root, "plugins", "fmt"), "fmt")
        skill(os.path.join(root, "plugins", "fmt"), "format")
        write(os.path.join(root, "plugins", "lint", "plugin.json"), {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "lint"})
        skill(os.path.join(root, "plugins", "lint"), "check")
        claude_plugin(os.path.join(root, "plugins", "other"), "other")
        skill(os.path.join(root, "plugins", "other"), "unwanted")
        # No manifest at all: the marketplace entry is its manifest
        skill(os.path.join(root, "plugins", "bare"), "plain")
        write(os.path.join(root, ".claude-plugin", "marketplace.json"), {
            "name": "local-mkt", "owner": {"name": "t"},
            "metadata": {"pluginRoot": "./plugins"},
            "plugins": [
                {"name": "fmt", "source": "fmt", "description": "Formatter"},
                {"name": "lint", "source": "./plugins/lint"},
                {"name": "other", "source": "other"},
                {"name": "bare", "source": "bare", "strict": False,
                 "description": "No manifest of its own", "category": "misc"},
            ]})
        return root


class TestDirectoryMarketplace(MarketplaceTestBase):

    def test_selected_plugins_only(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: file://%s
  plugins: [fmt, lint]
""" % root)
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.listing(".agents", "plugins"), ["fmt", "lint"])
        self.assertEqual(self.listing(".agents", "skills"), ["fmt-format", "lint-check"])
        self.assertEqual(self.listing(".claude", "skills"), ["fmt", "lint"])

    def test_glob_selection(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: ["*"]
""" % root)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.listing(".agents", "plugins"), ["bare", "fmt", "lint", "other"])

    def test_catalog_file_url(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: [fmt]
""" % os.path.join(root, ".claude-plugin", "marketplace.json"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.listing(".agents", "plugins"), ["fmt"])

    def test_non_strict_entry_is_the_manifest(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: [bare]
""" % root)
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.listing(".agents", "skills"), ["bare-plain"])
        manifest = json.load(open(os.path.join(
            self.testdir, ".claude", "skills", "bare", ".claude-plugin", "plugin.json")))
        self.assertEqual(manifest, {"name": "bare", "description": "No manifest of its own"})

    def test_lock_records_agents_config(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: [fmt]
""" % root)
        self.ivpm_update(skip_venv=True)

        agents = self.lock()["packages"]["mkt"]["agents"]
        self.assertEqual(agents["plugins"], ["plugins/fmt"])
        self.assertEqual(agents["plugin_manifests"]["plugins/fmt"]["fields"]["description"],
                         "Formatter")

    def test_no_match_is_fatal(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: [fmtt]
""" % root)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)
        self.assertIn("did you mean 'fmt'", str(ctx.exception))

    def test_plugins_required(self):
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
""" % root)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)
        self.assertIn("requires 'plugins:'", str(ctx.exception))

    def test_command_source_is_fatal(self):
        root = os.path.join(self.src, "cmd-mkt")
        write(os.path.join(root, "marketplace.json"), {
            "name": "cmd", "owner": {"name": "t"},
            "plugins": [{"name": "gen", "source": {"source": "command", "command": "make"}}]})
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: [gen]
""" % root)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)
        self.assertIn("does not run marketplace commands", str(ctx.exception))

    def test_show_plugins_uses_locked_selection(self):
        """'show plugins' must list the selected plugins, not every plugin the
        marketplace directory happens to contain."""
        import contextlib, io
        from ivpm.show.show_plugins import ShowPlugins
        root = self.local_marketplace()
        self.project("""
- name: mkt
  src: marketplace
  url: %s
  plugins: [fmt]
""" % root)
        self.ivpm_update(skip_venv=True)

        class Args(object):
            name = None; check = None; mcp = False; json = True
            no_rich = True; project_dir = self.testdir; dep_set = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ShowPlugins()(Args())
        names = [p["name"] for p in json.loads(out.getvalue())["plugins"]]
        self.assertEqual(names, ["fmt"])


class TestGitMarketplace(MarketplaceTestBase):

    def remote_plugins(self):
        """Two plugin repositories: one plugin at a root, one in a monorepo."""
        solo = os.path.join(self.src, "solo")
        claude_plugin(solo, "solo")
        skill(solo, "one")
        solo_sha = git_repo(solo, tag="v1.0")

        mono = os.path.join(self.src, "mono")
        claude_plugin(os.path.join(mono, "tools", "deep"), "deep")
        skill(os.path.join(mono, "tools", "deep"), "two")
        claude_plugin(os.path.join(mono, "tools", "shallow"), "shallow")
        skill(os.path.join(mono, "tools", "shallow"), "three")
        git_repo(mono)
        return solo, solo_sha, mono

    def git_marketplace(self, entries):
        root = os.path.join(self.src, "gmkt")
        claude_plugin(os.path.join(root, "plugins", "inside"), "inside")
        skill(os.path.join(root, "plugins", "inside"), "local")
        write(os.path.join(root, ".claude-plugin", "marketplace.json"), {
            "name": "git-mkt", "owner": {"name": "t"},
            "plugins": [{"name": "inside", "source": "./plugins/inside"}] + entries})
        git_repo(root)
        return root

    def test_remote_sources_become_dependencies(self):
        solo, solo_sha, mono = self.remote_plugins()
        root = self.git_marketplace([
            {"name": "solo", "source": {"source": "url", "url": "file://" + solo,
                                        "sha": solo_sha}},
            {"name": "deep", "source": {"source": "git-subdir", "url": "file://" + mono,
                                        "path": "tools/deep"}},
        ])
        self.project("""
- name: gmkt
  src: marketplace
  url: file://%s/.git
  plugins: [inside, solo, deep]
""" % root)
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.listing(".agents", "plugins"), ["deep", "inside", "solo"])
        self.assertEqual(self.listing(".agents", "skills"),
                         ["deep-two", "inside-local", "solo-one"])

        packages = self.lock()["packages"]
        self.assertEqual(packages["solo"]["commit_resolved"], solo_sha)
        self.assertTrue(packages["solo"]["from_marketplace"].endswith("#git-mkt"))
        self.assertEqual(packages["deep"]["agents"]["plugins"], ["tools/deep"])
        self.assertEqual(packages["gmkt"]["src"], "git")

    def test_git_subdir_takes_only_its_plugin(self):
        _, _, mono = self.remote_plugins()
        root = self.git_marketplace([
            {"name": "deep", "source": {"source": "git-subdir", "url": "file://" + mono,
                                        "path": "tools/deep"}}])
        self.project("""
- name: gmkt
  src: marketplace
  url: file://%s/.git
  plugins: [deep]
""" % root)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self.listing(".agents", "plugins"), ["deep"])

    def test_ref_classified_as_tag(self):
        solo, solo_sha, _ = self.remote_plugins()
        root = self.git_marketplace([
            {"name": "solo", "source": {"source": "url", "url": "file://" + solo,
                                        "ref": "v1.0"}}])
        self.project("""
- name: gmkt
  src: marketplace
  url: file://%s/.git
  plugins: [solo]
""" % root)
        self.ivpm_update(skip_venv=True)

        entry = self.lock()["packages"]["solo"]
        self.assertEqual(entry["tag"], "v1.0")
        self.assertEqual(entry["commit_resolved"], solo_sha)

    def test_project_dependency_overrides_marketplace_plugin(self):
        """A dependency the project declares itself wins over a same-named
        plugin from a marketplace -- that is how one plugin is pinned."""
        solo, solo_sha, _ = self.remote_plugins()
        root = self.git_marketplace([
            {"name": "solo", "source": {"source": "url", "url": "file://" + solo}}])
        pinned = os.path.join(self.src, "solo-fork")
        claude_plugin(pinned, "solo", description="The fork.")
        skill(pinned, "forked")
        self.project("""
- name: solo
  src: dir
  url: file://%s
- name: gmkt
  src: marketplace
  url: file://%s/.git
  plugins: [solo]
""" % (pinned, root))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self.listing(".agents", "skills"), ["solo-forked"])
        self.assertEqual(self.lock()["packages"]["solo"]["src"], "dir")

    def test_lock_reproduction(self):
        """A lock alone reproduces the selection, with no marketplace logic."""
        solo, solo_sha, _ = self.remote_plugins()
        root = self.git_marketplace([
            {"name": "solo", "source": {"source": "url", "url": "file://" + solo}}])
        self.project("""
- name: gmkt
  src: marketplace
  url: file://%s/.git
  plugins: [inside, solo]
""" % root)
        self.ivpm_update(skip_venv=True)

        lock_copy = os.path.join(self.src, "package-lock.json")
        shutil.copy(os.path.join(self.testdir, "packages", "package-lock.json"), lock_copy)
        for d in ("packages", ".agents", ".claude", ".cursor"):
            shutil.rmtree(os.path.join(self.testdir, d), ignore_errors=True)

        from ivpm.project_ops import ProjectOps

        class Args(object):
            anonymous_git = None
        ProjectOps(self.testdir, Args()).update(
            dep_set="default-dev", skip_venv=True, args=Args(), lock_file=lock_copy)

        self.assertEqual(self.listing(".agents", "plugins"), ["inside", "solo"])


class TestShowFrom(MarketplaceTestBase):

    def show(self, src, **kw):
        import contextlib, io
        from ivpm.show.show_plugins import ShowPlugins

        class Args(object):
            name = None; check = None; mcp = False; json = False; no_rich = True
            project_dir = None; dep_set = None; marketplace_file = None
        args = Args()
        args.from_marketplace = src
        for k, v in kw.items():
            setattr(args, k, v)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ShowPlugins()(args)
        return out.getvalue()

    def test_directory(self):
        root = self.local_marketplace()
        out = self.show(root)
        self.assertIn("Marketplace: local-mkt", out)
        self.assertIn("./plugins/fmt", out)
        self.assertIn("Formatter", out)

    def test_git_json_flags_uninstallable(self):
        root = os.path.join(self.src, "gm")
        write(os.path.join(root, ".claude-plugin", "marketplace.json"), {
            "name": "gm", "owner": {"name": "t"},
            "plugins": [
                {"name": "a", "source": "./a"},
                {"name": "n", "source": {"source": "npm", "package": "@x/n"}}]})
        git_repo(root)
        data = json.loads(self.show("file://%s/.git" % root, json=True))
        self.assertEqual(data["marketplace"], "gm")
        self.assertEqual([(p["name"], p["installable"]) for p in data["plugins"]],
                         [("a", True), ("n", False)])

    def test_nothing_written_to_workspace(self):
        root = self.local_marketplace()
        before = sorted(os.listdir(self.testdir))
        self.show(root)
        self.assertEqual(sorted(os.listdir(self.testdir)), before)

    def test_missing_catalog_exits_nonzero(self):
        empty = os.path.join(self.src, "empty")
        os.makedirs(empty)
        with self.assertRaises(SystemExit) as ctx:
            self.show(empty)
        self.assertEqual(ctx.exception.code, 1)


class TestUrlClassification(unittest.TestCase):

    def test_classify(self):
        from ivpm.pkg_types.package_marketplace import _classify_url
        self.assertEqual(_classify_url("acme/tools", None),
                         ("git", "https://github.com/acme/tools.git", None))
        self.assertEqual(_classify_url("https://example.com/m/marketplace.json", None),
                         ("url", "https://example.com/m/marketplace.json", None))
        self.assertEqual(_classify_url("https://gitlab.com/acme/tools", None)[0], "git")
        self.assertEqual(_classify_url("git@github.com:acme/tools.git", None)[0], "git")


if __name__ == "__main__":
    unittest.main()
