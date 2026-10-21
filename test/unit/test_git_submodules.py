"""Submodules of git dependencies (and of the root project), end to end.

Real repositories, real ``ivpm update`` / ``ivpm sync``.  The fixture is three
local repos nested two deep, so recursion is exercised: ``top`` has submodule
``ext/mid``, which has submodule ``leaf``.  Some tests add a second top-level
submodule ``ext/other``.

Submodule URLs are ``file://``, which git >= 2.38.1 refuses for submodules
unless ``protocol.file.allow=always``; the tests set that through the
environment so it reaches the git processes ivpm spawns.

Spec parsing, identity and other pure logic is in test_git_submodules_spec.py.
"""
import contextlib
import json
import os
import shutil
import subprocess
import types
from unittest.mock import patch

from .test_base import TestBase
from .test_sync_ops import _SyncArgs

from ivpm import git_submodules as gsm
from ivpm.git_submodules import parse_spec
from ivpm.pkg_sync import SyncOutcome
from ivpm.pkg_types.package_git import PackageGit
from ivpm.project_ops import ProjectOps

# Commit hashes depend on timestamps; pin them so the fixture is deterministic.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
}

# Allow file:// submodule transport for every git process, ivpm's included.
_ALLOW_FILE = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
}

# ...and speak protocol v0, whose upload-pack refuses a request for a commit
# no ref advertises -- the server behavior the shallow fallback exists for.
_PROTOCOL_V0 = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_1": "protocol.version",
    "GIT_CONFIG_VALUE_1": "0",
}


class _Base(TestBase):

    #: IMPLICIT_DEFAULT for the test; None leaves the shipped default.
    implicit = None

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, dict(_GIT_ENV, **_ALLOW_FILE))
        env.start()
        self.addCleanup(env.stop)
        # Only the cache tests opt into a shared cache.
        os.environ.pop("IVPM_CACHE", None)
        os.environ.pop("IVPM_GIT_URL_MAP", None)
        if self.implicit is not None:
            p = patch.object(gsm, "IMPLICIT_DEFAULT", self.implicit)
            p.start()
            self.addCleanup(p.stop)

        self.repos = os.path.join(self.testdir, "repos")
        self.leaf = self._repo("leaf", {"leaf.txt": "leaf\n"})
        self.mid = self._repo("mid", {"mid.txt": "mid\n"},
                              submodules={"leaf": self.leaf})
        self.top = self._repo("top", {"ivpm.yaml": "package:\n  name: top\n",
                                      "top.txt": "top\n"},
                              submodules={"ext/mid": self.mid})
        self.ws = os.path.join(self.testdir, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        # Cache entries are sealed unwritable.
        for root, dirs, _ in os.walk(self.testdir):
            for d in dirs:
                p = os.path.join(root, d)
                if not os.path.islink(p):
                    try:
                        os.chmod(p, 0o700)
                    except OSError:
                        pass
        return super().tearDown()

    # -- repositories ----------------------------------------------------- #

    @staticmethod
    def git(cwd, *args):
        subprocess.check_call(["git"] + list(args), cwd=cwd,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)

    @staticmethod
    def git_out(cwd, *args):
        return subprocess.check_output(["git"] + list(args), cwd=cwd,
                                       text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def _repo(self, name, files, submodules=None):
        path = os.path.join(self.repos, name)
        os.makedirs(path)
        self.git(path, "init", "-b", "main")
        for rel, content in files.items():
            os.makedirs(os.path.dirname(os.path.join(path, rel)) or path,
                        exist_ok=True)
            with open(os.path.join(path, rel), "w") as fp:
                fp.write(content)
        for sm_path, url in (submodules or {}).items():
            self.git(path, "submodule", "add", "file://%s" % url, sm_path)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", "init")
        return path

    def commit(self, repo, files, msg="change"):
        for rel, content in files.items():
            with open(os.path.join(repo, rel), "w") as fp:
                fp.write(content)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-m", msg)
        return self.git_out(repo, "rev-parse", "HEAD")

    def add_other(self, repo=None):
        """Give *repo* (default ``top``) a second top-level submodule."""
        other = os.path.join(self.repos, "other")
        if not os.path.isdir(other):
            self._repo("other", {"other.txt": "other\n"})
        repo = repo or self.top
        self.git(repo, "submodule", "add", "file://%s" % other, "ext/other")
        self.git(repo, "commit", "-m", "add other")
        return other

    def bad_top(self):
        """A superproject whose submodule URL leads nowhere."""
        bad = self._repo("badtop", {"ivpm.yaml": "package:\n  name: top\n",
                                    "top.txt": "top\n"},
                         submodules={"ext/mid": self.mid})
        self.git(bad, "config", "-f", ".gitmodules", "submodule.ext/mid.url",
                 "file://%s/does-not-exist" % self.repos)
        self.git(bad, "commit", "-am", "break the url")
        return bad

    # -- workspace -------------------------------------------------------- #

    def write_ws(self, attrs="", repo=None, extra=""):
        """Write a workspace ivpm.yaml depending on *repo* (default ``top``)
        with extra dep *attrs* (pre-indented yaml lines)."""
        with open(os.path.join(self.ws, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n"
                     "  name: ws\n"
                     "%s"
                     "  dep-sets:\n"
                     "    - name: default-dev\n"
                     "      deps:\n"
                     "        - name: top\n"
                     "          src: git\n"
                     "          url: file://%s\n"
                     "%s" % (extra, repo or self.top, attrs))

    def update(self, lock_file=None):
        class Args(object):
            anonymous_git = None
        ProjectOps(self.ws, Args()).update(dep_set="default-dev",
                                           skip_venv=True, args=Args(),
                                           lock_file=lock_file)
        return os.path.join(self.ws, "packages", "top")

    def use_cache(self):
        cache = os.path.join(self.testdir, "cache")
        os.makedirs(cache, exist_ok=True)
        os.environ["IVPM_CACHE"] = cache
        return cache

    def cache_versions(self, cache, name="top"):
        d = os.path.join(cache, name)
        if not os.path.isdir(d):
            return []
        return sorted(n for n in os.listdir(d)
                      if not n.startswith(".") and not n.endswith(".meta.json"))

    def lock(self):
        with open(os.path.join(self.ws, "packages", "package-lock.json")) as fp:
            return json.load(fp)

    def lock_entry(self, name="top"):
        return self.lock()["packages"][name]

    @contextlib.contextmanager
    def messages(self):
        """Collect (kind, text) of the notes and warnings ivpm emits."""
        msgs = []

        def rec(kind):
            return lambda m, *a, **k: msgs.append((kind, str(m)))
        with patch("ivpm.pkg_types.package_git.note", rec("note")), \
             patch("ivpm.utils.warning", rec("warning")), \
             patch("ivpm.utils.note", rec("note")), \
             patch("ivpm.project_ops.note", rec("note")):
            yield msgs

    @staticmethod
    def texts(msgs, kind=None):
        return "\n".join(m for k, m in msgs if kind is None or k == kind)

    # -- assertions ------------------------------------------------------- #

    def assertPopulated(self, pkg, nested=True):
        self.assertTrue(os.path.isfile(os.path.join(pkg, "top.txt")))
        self.assertTrue(os.path.isfile(os.path.join(pkg, "ext", "mid", "mid.txt")),
                        "first-level submodule was not checked out")
        leaf = os.path.isfile(os.path.join(pkg, "ext", "mid", "leaf", "leaf.txt"))
        if nested:
            self.assertTrue(leaf, "nested submodule was not checked out")
        else:
            self.assertFalse(leaf, "nested submodule was checked out")

    def assertNotPopulated(self, pkg):
        self.assertTrue(os.path.isfile(os.path.join(pkg, "top.txt")))
        self.assertFalse(os.path.isfile(os.path.join(pkg, "ext", "mid", "mid.txt")),
                         "submodule was checked out")

    def is_shallow(self, repo):
        return self.git_out(repo, "rev-parse", "--is-shallow-repository") == "true"


class _SpyGit(object):
    """Record every git command PackageGit runs through _run_git."""

    def __init__(self):
        self.seen = []
        self._orig = PackageGit._run_git

    def __enter__(self):
        orig = self._orig
        seen = self.seen

        def spy(s, cmd, *a, **kw):
            seen.append(list(cmd))
            return orig(s, cmd, *a, **kw)
        self._p = patch.object(PackageGit, "_run_git", spy)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()

    @property
    def submodule_cmds(self):
        return [c for c in self.seen if "submodule" in c]


# ===================================================================== #
# Defaults                                                               #
# ===================================================================== #

class TestLegacyDefault(_Base):
    """No ``submodules:`` key, IMPLICIT_DEFAULT = legacy: every clone path
    initializes everything, recursively, as earlier releases did -- plus a
    deprecation note, and a recorded result."""

    implicit = "legacy"

    def test_full_clone(self):
        self.write_ws()
        with self.messages() as msgs:
            pkg = self.update()
        self.assertPopulated(pkg)
        self.assertIn("no 'submodules:' setting", self.texts(msgs, "note"))

    def test_cache_false(self):
        self.write_ws("          cache: false\n")
        self.assertPopulated(self.update())

    def test_cache_miss_uses_the_bare_key(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n")
        pkg = self.update()
        self.assertTrue(os.path.islink(pkg), "expected a link into the cache")
        self.assertPopulated(pkg)
        sha = self.git_out(self.top, "rev-parse", "HEAD")
        # Legacy content is what every pre-existing entry holds: it shares
        # their key.
        self.assertEqual(self.cache_versions(cache), [sha])

    def test_patched_dependency(self):
        patch_path = os.path.join(self.ws, "fix.patch")
        with open(patch_path, "w") as fp:
            fp.write("--- a/top.txt\n+++ b/top.txt\n@@ -1 +1 @@\n-top\n+patched\n")
        self.write_ws("          patches:\n"
                      "            - fix.patch\n")
        pkg = self.update()
        with open(os.path.join(pkg, "top.txt")) as fp:
            self.assertEqual(fp.read(), "patched\n")
        self.assertPopulated(pkg)

    def test_full_depth(self):
        pkg = os.path.join(self.ws, "packages", "top")
        self.write_ws("          cache: false\n")
        self.update()
        self.assertFalse(self.is_shallow(os.path.join(pkg, "ext", "mid")))

    def test_lock_records_legacy_spec(self):
        self.write_ws()
        self.update()
        rec = self.lock_entry()["submodules"]
        self.assertTrue(rec["spec"]["enabled"])
        self.assertTrue(rec["spec"].get("legacy"))
        self.assertEqual([m["path"] for m in rec["modules"]],
                         ["ext/mid", "ext/mid/leaf"])
        self.assertTrue(all(m["status"] == "ok" for m in rec["modules"]))

    def test_failure_warns_and_continues(self):
        # The old code swallowed this silently; now it is reported, but the
        # implicit legacy spec still does not fail the update.
        self.write_ws(repo=self.bad_top())
        with self.messages() as msgs:
            pkg = self.update()
        self.assertTrue(os.path.isfile(os.path.join(pkg, "top.txt")))
        warn = self.texts(msgs, "warning")
        self.assertIn("Failed to initialize submodules of top", warn)
        self.assertIn("ext/mid", warn)

    def test_failure_is_never_cached(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n", repo=self.bad_top())
        pkg = self.update()
        self.assertFalse(os.path.islink(pkg),
                         "a tree with failed submodules was linked from the cache")
        self.assertTrue(os.path.isfile(os.path.join(pkg, "top.txt")))
        self.assertEqual(self.cache_versions(cache), [])

    def test_resident_tree_is_not_touched(self):
        # Legacy never re-initialized a loaded package; it still does not.
        self.write_ws()
        pkg = self.update()
        self.git(pkg, "submodule", "deinit", "-f", "ext/mid")
        with _SpyGit() as spy:
            self.update()
        self.assertEqual(spy.submodule_cmds, [])
        self.assertNotPopulated(pkg)


class TestOffDefault(_Base):
    """IMPLICIT_DEFAULT = off (git's own default, and where the default is
    headed): nothing is initialized unless asked for."""

    implicit = "off"

    def test_no_submodule_command_runs(self):
        self.write_ws()
        with _SpyGit() as spy, self.messages() as msgs:
            pkg = self.update()
        self.assertTrue(spy.seen, "spy saw no git commands")
        self.assertEqual(spy.submodule_cmds, [])
        self.assertNotPopulated(pkg)
        self.assertIn("top has 1 submodule (not initialized; add "
                      "'submodules: true' to fetch them)",
                      self.texts(msgs, "note"))

    def test_lock_lists_uninitialized_modules(self):
        self.write_ws()
        self.update()
        rec = self.lock_entry()["submodules"]
        self.assertEqual(rec["spec"], {"enabled": False})
        self.assertEqual(len(rec["modules"]), 1)     # nested ones are unseen
        m = rec["modules"][0]
        self.assertEqual(m["path"], "ext/mid")
        self.assertFalse(m["initialized"])
        self.assertEqual(m["status"], "not-requested")
        # The pin is known even uninitialized: it is the superproject gitlink.
        self.assertEqual(m["commit"], self.git_out(self.mid, "rev-parse", "HEAD"))

    def test_explicit_false_is_silent(self):
        self.write_ws("          submodules: false\n")
        with self.messages() as msgs:
            pkg = self.update()
        self.assertNotPopulated(pkg)
        self.assertNotIn("submodule", self.texts(msgs, "note"))

    def test_no_note_without_gitmodules(self):
        self.write_ws(repo=self.leaf)
        with self.messages() as msgs:
            self.update()
        self.assertNotIn("submodule", self.texts(msgs))

    def test_off_with_gitmodules_uses_the_off_cache_key(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n")
        pkg = self.update()
        self.assertTrue(os.path.islink(pkg))
        self.assertNotPopulated(pkg)
        sha = self.git_out(self.top, "rev-parse", "HEAD")
        # Not the bare key: a legacy consumer of that key would get a tree
        # without the submodules it expects.
        self.assertEqual(self.cache_versions(cache), [sha + "_sm_off"])

    def test_off_without_gitmodules_uses_the_bare_key(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n", repo=self.leaf)
        self.update()
        self.assertEqual(self.cache_versions(cache),
                         [self.git_out(self.leaf, "rev-parse", "HEAD")])

    def test_off_accepts_a_legacy_entry(self):
        # An entry stored under the bare key by legacy behavior holds a
        # superset of what "off" needs; it is served rather than re-fetched.
        cache = self.use_cache()
        self.write_ws("          cache: true\n")
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"):
            self.update()
        sha = self.git_out(self.top, "rev-parse", "HEAD")
        self.assertEqual(self.cache_versions(cache), [sha])
        shutil.rmtree(os.path.join(self.ws, "packages"))
        with _SpyGit() as spy:
            pkg = self.update()
        self.assertFalse([c for c in spy.seen if "clone" in c],
                         "expected a cache hit, not a clone")
        self.assertEqual(os.path.basename(os.path.realpath(pkg)), sha)


class TestNoGitmodules(_Base):

    def test_no_submodule_command_without_gitmodules(self):
        self.write_ws("          submodules: true\n", repo=self.leaf)
        with _SpyGit() as spy:
            pkg = self.update()
        self.assertTrue(os.path.isfile(os.path.join(pkg, "leaf.txt")))
        self.assertTrue(spy.seen, "spy saw no git commands")
        self.assertEqual(spy.submodule_cmds, [])
        self.assertEqual(self.lock_entry()["submodules"]["modules"], [])

    def test_paths_without_gitmodules_is_fatal(self):
        self.write_ws("          submodules:\n"
                      "            paths: [ext/mid]\n", repo=self.leaf)
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("has no submodules", str(cm.exception))


# ===================================================================== #
# Explicit specs                                                         #
# ===================================================================== #

class TestExplicitSpec(_Base):

    implicit = "off"

    def test_true_full_clone(self):
        self.write_ws("          submodules: true\n")
        with self.messages() as msgs:
            pkg = self.update()
        self.assertPopulated(pkg)
        self.assertNotIn("no 'submodules:' setting", self.texts(msgs))

    def test_true_cache_false(self):
        self.write_ws("          cache: false\n"
                      "          submodules: true\n")
        self.assertPopulated(self.update())

    def test_true_cache_miss_uses_its_own_key(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n"
                      "          submodules: true\n")
        pkg = self.update()
        self.assertTrue(os.path.islink(pkg))
        self.assertPopulated(pkg)
        sha = self.git_out(self.top, "rev-parse", "HEAD")
        suffix = parse_spec(True).cache_suffix(True)
        self.assertEqual(self.cache_versions(cache), [sha + suffix])

    def test_true_patched_dependency(self):
        with open(os.path.join(self.ws, "fix.patch"), "w") as fp:
            fp.write("--- a/top.txt\n+++ b/top.txt\n@@ -1 +1 @@\n-top\n+patched\n")
        self.write_ws("          submodules: true\n"
                      "          patches:\n"
                      "            - fix.patch\n")
        pkg = self.update()
        with open(os.path.join(pkg, "top.txt")) as fp:
            self.assertEqual(fp.read(), "patched\n")
        self.assertPopulated(pkg)

    def test_not_recursive(self):
        self.write_ws("          submodules:\n"
                      "            recursive: false\n")
        pkg = self.update()
        self.assertPopulated(pkg, nested=False)
        mods = dict((m["path"], m) for m in self.lock_entry()["submodules"]["modules"])
        self.assertEqual(mods["ext/mid"]["status"], "ok")
        self.assertEqual(mods["ext/mid/leaf"]["status"], "not-requested")

    def test_paths_subset(self):
        self.add_other()
        self.write_ws("          submodules:\n"
                      "            paths: [ext/other]\n")
        pkg = self.update()
        self.assertTrue(os.path.isfile(os.path.join(pkg, "ext", "other", "other.txt")))
        self.assertFalse(os.path.isfile(os.path.join(pkg, "ext", "mid", "mid.txt")))
        mods = dict((m["path"], m["status"])
                    for m in self.lock_entry()["submodules"]["modules"])
        self.assertEqual(mods, {"ext/mid": "not-requested", "ext/other": "ok"})

    def test_paths_selection_recurses(self):
        self.add_other()
        self.write_ws("          submodules:\n"
                      "            paths: [ext/mid]\n")
        pkg = self.update()
        self.assertPopulated(pkg)
        self.assertFalse(os.path.isfile(os.path.join(pkg, "ext", "other", "other.txt")))

    def test_unknown_path_is_fatal(self):
        self.write_ws("          submodules:\n"
                      "            paths: [ext/typo]\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        msg = str(cm.exception)
        self.assertIn("ext/typo", msg)
        self.assertIn("not a submodule", msg)
        self.assertIn("ext/mid", msg)        # lists what does exist

    def test_nested_path_is_explained(self):
        self.write_ws("          submodules:\n"
                      "            paths: [ext/mid/leaf]\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("nested submodule is not supported", str(cm.exception))

    def test_bad_value_is_fatal_at_load(self):
        self.write_ws("          submodules: {deep: true}\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("deep", str(cm.exception))

    def test_update_none_is_honored(self):
        self.add_other()
        self.git(self.top, "config", "-f", ".gitmodules",
                 "submodule.ext/other.update", "none")
        self.git(self.top, "commit", "-am", "other: update none")
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        self.assertPopulated(pkg)
        self.assertFalse(os.path.isfile(os.path.join(pkg, "ext", "other", "other.txt")))
        mods = dict((m["path"], m["status"])
                    for m in self.lock_entry()["submodules"]["modules"])
        self.assertEqual(mods["ext/other"], "update-none")


class TestDepDefaults(_Base):

    implicit = "off"

    def test_applies_to_deps_without_the_key(self):
        self.write_ws(extra="  dep-defaults:\n"
                            "    git:\n"
                            "      submodules: true\n")
        self.assertPopulated(self.update())
        self.assertTrue(self.lock_entry()["submodules"]["spec"]["enabled"])

    def test_dep_key_wins(self):
        self.write_ws("          submodules: false\n",
                      extra="  dep-defaults:\n"
                            "    git:\n"
                            "      submodules: true\n")
        self.assertNotPopulated(self.update())

    def test_unknown_default_key_is_fatal(self):
        self.write_ws(extra="  dep-defaults:\n"
                            "    git:\n"
                            "      branch: main\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("dep-defaults.git", str(cm.exception))

    def test_unknown_source_is_fatal(self):
        self.write_ws(extra="  dep-defaults:\n"
                            "    pypi:\n"
                            "      submodules: true\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("pypi", str(cm.exception))

    def test_cache_depth_rule_applies_to_defaults(self):
        self.write_ws("          cache: true\n",
                      extra="  dep-defaults:\n"
                            "    git:\n"
                            "      submodules: {depth: 4}\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("submodules.depth", str(cm.exception))


# ===================================================================== #
# Depth                                                                  #
# ===================================================================== #

class TestDepth(_Base):

    implicit = "off"

    def test_editable_default_is_full_history(self):
        self.write_ws("          cache: false\n"
                      "          submodules: true\n")
        pkg = self.update()
        self.assertFalse(self.is_shallow(os.path.join(pkg, "ext", "mid")))

    def test_explicit_depth_is_shallow_recursively(self):
        self.write_ws("          cache: false\n"
                      "          submodules: {depth: 1}\n")
        pkg = self.update()
        self.assertPopulated(pkg)
        self.assertTrue(self.is_shallow(os.path.join(pkg, "ext", "mid")))
        self.assertTrue(self.is_shallow(os.path.join(pkg, "ext", "mid", "leaf")))
        mods = self.lock_entry()["submodules"]["modules"]
        self.assertTrue(all(m["shallow"] for m in mods))
        self.assertEqual(self.lock_entry()["submodules"]["spec"]["depth"], 1)

    def test_cached_default_is_shallow(self):
        self.use_cache()
        self.write_ws("          cache: true\n"
                      "          submodules: true\n")
        pkg = self.update()
        self.assertTrue(self.is_shallow(os.path.join(os.path.realpath(pkg),
                                                     "ext", "mid")))

    def test_cached_deep_depth_is_rejected(self):
        self.write_ws("          cache: true\n"
                      "          submodules: {depth: 3}\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("has no effect with 'cache: true'", str(cm.exception))

    def _pinned_behind_tip(self):
        """A superproject whose gitlink is *behind* its submodule's branch
        tip, so a shallow fetch of the tip does not contain the pin."""
        sub = self._repo("sub", {"a.txt": "a\n"})
        self.commit(sub, {"b.txt": "b\n"}, "b")
        pinned = self.git_out(sub, "rev-parse", "HEAD")
        sup = self._repo("sup", {"ivpm.yaml": "package:\n  name: top\n",
                                 "top.txt": "top\n"},
                         submodules={"s": sub})
        self.commit(sub, {"c.txt": "c\n"}, "c")      # tip moves past the pin
        return sup, pinned

    def test_shallow_miss_falls_back_to_full_history(self):
        sup, pinned = self._pinned_behind_tip()
        os.environ.update(_PROTOCOL_V0)
        self.write_ws("          cache: false\n"
                      "          submodules: {depth: 1}\n", repo=sup)
        with self.messages() as msgs:
            pkg = self.update()
        s = os.path.join(pkg, "s")
        self.assertEqual(self.git_out(s, "rev-parse", "HEAD"), pinned)
        self.assertFalse(self.is_shallow(s))
        self.assertIn("would not serve the pinned commit shallowly",
                      self.texts(msgs, "note"))
        m = self.lock_entry()["submodules"]["modules"][0]
        self.assertEqual(m["status"], "ok")
        self.assertFalse(m["shallow"])

    def test_retry_never_deletes_a_deinitialized_repository(self):
        # A deinitialized submodule keeps its repository (and the user's
        # branches) under .git/modules. The shallow-miss retry must deepen
        # it, not delete and re-clone it.
        sup, pinned = self._pinned_behind_tip()
        self.write_ws("          cache: false\n"
                      "          submodules: true\n", repo=sup)
        pkg = self.update()
        s = os.path.join(pkg, "s")
        self.git(s, "checkout", "-q", "-b", "work")
        mine = self.commit(s, {"mine.txt": "x\n"}, "local work")
        self.git(pkg, "submodule", "deinit", "-f", "s")
        recs = gsm.walk(pkg, parse_spec(True))
        self.assertEqual(recs[0].state, "uninitialized")
        gd = gsm.module_gitdir(pkg, recs[0])
        self.assertIsNotNone(gd)
        # init_missing must classify it as not fresh...
        seen = []

        def run(cmd, cwd, progress=False):
            seen.append(cmd)
            return 1, "fatal: Fetched in submodule path 's', but it did not " \
                      "contain %s. Direct fetching of that commit failed." % pinned
        gsm.init_missing(pkg, parse_spec({"depth": 1}), False, run=run)
        self.assertFalse([c for c in seen if "deinit" in c])
        # ...and the repository with the user's branch survives.
        self.assertTrue(os.path.isdir(gd))
        self.assertEqual(self.git_out(pkg, "--git-dir=%s" % gd, "rev-parse",
                                      "work"), mine)

    def test_retry_of_a_deinitialized_submodule_deepens_its_own_repo(self):
        sup, pinned = self._pinned_behind_tip()
        self.write_ws("          cache: false\n"
                      "          submodules: {depth: 1}\n", repo=sup)
        pkg = self.update()
        self.git(pkg, "submodule", "deinit", "-f", "s")
        rec = gsm.walk(pkg, parse_spec(True))[0]
        gd = gsm.module_gitdir(pkg, rec)
        rc, err = gsm._retry_full(pkg, rec, parse_spec(True),
                                  gsm._default_runner, lambda c: c,
                                  fresh=False)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.git_out(pkg, "--git-dir=%s" % gd, "rev-parse",
                                      "--is-shallow-repository"), "false")
        # The superproject was not the repository deepened/fetched into.
        self.assertFalse(self.is_shallow(pkg))
        self.assertEqual(self.git_out(os.path.join(pkg, "s"), "rev-parse", "HEAD"),
                         pinned)

    def test_pinned_behind_tip_is_shallow_when_the_server_allows(self):
        # Protocol v2 (git's default) serves any reachable commit, so no
        # fallback is needed and the submodule stays shallow.
        sup, pinned = self._pinned_behind_tip()
        self.write_ws("          cache: false\n"
                      "          submodules: {depth: 1}\n", repo=sup)
        with self.messages() as msgs:
            pkg = self.update()
        s = os.path.join(pkg, "s")
        self.assertEqual(self.git_out(s, "rev-parse", "HEAD"), pinned)
        self.assertTrue(self.is_shallow(s))
        self.assertNotIn("shallowly", self.texts(msgs))


# ===================================================================== #
# Failure                                                                #
# ===================================================================== #

class TestFailure(_Base):

    implicit = "off"

    def test_unreachable_submodule_is_fatal(self):
        self.write_ws("          submodules: true\n", repo=self.bad_top())
        with self.assertRaises(Exception) as cm:
            self.update()
        msg = str(cm.exception)
        self.assertIn("Failed to initialize submodules of top", msg)
        self.assertIn("ext/mid -> file://", msg)
        self.assertIn("does-not-exist", msg)
        self.assertIn("git reported:", msg)

    def test_unreachable_submodule_is_never_cached(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n"
                      "          submodules: true\n", repo=self.bad_top())
        with self.assertRaises(Exception):
            self.update()
        self.assertEqual(self.cache_versions(cache), [])
        # ...and no staging tree is left behind.
        leftovers = []
        for root, dirs, files in os.walk(cache):
            leftovers += [d for d in dirs if ".staging." in d]
        self.assertEqual(leftovers, [])

    def test_optional_failure_warns_and_is_not_cached(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n"
                      "          submodules: {required: false}\n",
                      repo=self.bad_top())
        with self.messages() as msgs:
            pkg = self.update()
        self.assertIn("Failed to initialize submodules of top",
                      self.texts(msgs, "warning"))
        self.assertIn("they are optional", self.texts(msgs, "warning"))
        self.assertFalse(os.path.islink(pkg))
        self.assertTrue(os.path.isfile(os.path.join(pkg, "top.txt")))
        self.assertEqual(self.cache_versions(cache), [])
        m = self.lock_entry()["submodules"]["modules"][0]
        self.assertEqual(m["status"], "failed")

    def test_optional_failure_on_a_patched_miss_is_not_cached(self):
        cache = self.use_cache()
        with open(os.path.join(self.ws, "fix.patch"), "w") as fp:
            fp.write("--- a/top.txt\n+++ b/top.txt\n@@ -1 +1 @@\n-top\n+patched\n")
        self.write_ws("          cache: true\n"
                      "          submodules: {required: false}\n"
                      "          patches:\n"
                      "            - fix.patch\n", repo=self.bad_top())
        pkg = self.update()
        self.assertFalse(os.path.islink(pkg))
        with open(os.path.join(pkg, "top.txt")) as fp:
            self.assertEqual(fp.read(), "patched\n")
        self.assertEqual(self.cache_versions(cache), [])

    def test_optional_failure_on_a_patched_miss_never_touches_a_link(self):
        # pkg_dir still links to an earlier cached variant: the fallback must
        # replace the link, not reconcile the shared entry it points at.
        self.use_cache()
        with open(os.path.join(self.ws, "fix.patch"), "w") as fp:
            fp.write("--- a/top.txt\n+++ b/top.txt\n@@ -1 +1 @@\n-top\n+patched\n")
        shared = os.path.join(self.testdir, "shared-entry")
        os.makedirs(shared)
        with open(os.path.join(shared, "marker"), "w") as fp:
            fp.write("untouched\n")
        os.makedirs(os.path.join(self.ws, "packages"))
        os.symlink(shared, os.path.join(self.ws, "packages", "top"))
        self.write_ws("          cache: true\n"
                      "          submodules: {required: false}\n"
                      "          patches:\n"
                      "            - fix.patch\n", repo=self.bad_top())
        with self.messages():
            pkg = self.update()
        self.assertFalse(os.path.islink(pkg))
        self.assertEqual(os.listdir(shared), ["marker"])

    def test_legacy_private_copy_is_completed_later(self):
        # A failed (optional) fetch leaves an uncached private copy; the next
        # update retries its missing submodules in place.
        self.use_cache()
        bad = self.bad_top()
        self.write_ws("          cache: true\n", repo=bad)
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"), self.messages():
            pkg = self.update()
            self.assertFalse(os.path.islink(pkg))
            # Repair the URL in the workspace copy (as a fixed mirror would).
            self.git(pkg, "config", "submodule.ext/mid.url", "file://%s" % self.mid)
            self.git(pkg, "config", "-f", ".gitmodules", "submodule.ext/mid.url",
                     "file://%s" % self.mid)
            self.update()
        self.assertPopulated(pkg)

    def test_file_protocol_refusal_is_explained(self):
        # git's own default: file:// only when the user asked directly, which
        # a submodule fetch is not. The superproject clone still works.
        os.environ["GIT_CONFIG_VALUE_0"] = "user"
        self.write_ws("          submodules: true\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("protocol.file.allow", str(cm.exception))


# ===================================================================== #
# Lock file                                                              #
# ===================================================================== #

class TestLock(_Base):

    implicit = "off"

    def test_v3_fields(self):
        self.write_ws("          cache: false\n"
                      "          depth: 2\n"
                      "          submodules: true\n")
        self.update()
        lock = self.lock()
        self.assertEqual(lock["ivpm_lock_version"], 3)
        e = lock["packages"]["top"]
        self.assertEqual(e["depth"], 2)
        rec = e["submodules"]
        self.assertEqual(rec["spec_id"], parse_spec(True).spec_id(False))
        self.assertEqual(rec["spec"], {"enabled": True, "recursive": True,
                                       "paths": None, "depth": None,
                                       "required": True})
        mods = dict((m["path"], m) for m in rec["modules"])
        self.assertEqual(sorted(mods), ["ext/mid", "ext/mid/leaf"])
        self.assertEqual(mods["ext/mid"]["commit"],
                         self.git_out(self.mid, "rev-parse", "HEAD"))
        self.assertEqual(mods["ext/mid/leaf"]["commit"],
                         self.git_out(self.leaf, "rev-parse", "HEAD"))
        self.assertEqual(mods["ext/mid"]["url"], "file://%s" % self.mid)
        self.assertEqual(mods["ext/mid/leaf"]["name"], "leaf")

    def test_reused_tree_keeps_its_record(self):
        self.write_ws("          submodules: true\n")
        self.update()
        first = self.lock_entry()["submodules"]
        self.update()          # resident fast path: walks the tree again
        self.assertEqual(self.lock_entry()["submodules"], first)

    def _reproduce(self, mutate=None):
        self.write_ws("          submodules: true\n")
        self.update()
        lock = self.lock()
        if mutate is not None:
            mutate(lock)
        saved = os.path.join(self.testdir, "saved-lock.json")
        with open(saved, "w") as fp:
            json.dump(lock, fp)
        shutil.rmtree(os.path.join(self.ws, "packages"))
        os.unlink(os.path.join(self.ws, "ivpm.yaml"))
        self.write_ws()         # the manifest no longer asks for submodules
        return saved

    def test_lock_file_reproduces_the_submodules(self):
        saved = self._reproduce()
        pkg = self.update(lock_file=saved)
        self.assertPopulated(pkg)

    def test_lock_file_commit_mismatch_is_fatal(self):
        def tamper(lock):
            lock["packages"]["top"]["submodules"]["modules"][0]["commit"] = "f" * 40
            lock.pop("sha256", None)
        saved = self._reproduce(tamper)
        with self.assertRaises(Exception) as cm:
            self.update(lock_file=saved)
        self.assertIn("submodule commits differ from the lock file", str(cm.exception))

    def test_lock_file_url_change_is_a_note(self):
        def move(lock):
            lock["packages"]["top"]["submodules"]["modules"][0]["url"] = "file:///old"
            lock.pop("sha256", None)
        saved = self._reproduce(move)
        with self.messages() as msgs:
            self.update(lock_file=saved)
        self.assertIn("the lock recorded file:///old", self.texts(msgs, "note"))

    def test_v2_lock_reproduces_legacy_behavior(self):
        def to_v2(lock):
            lock["ivpm_lock_version"] = 2
            for e in lock["packages"].values():
                e.pop("submodules", None)
                e.pop("depth", None)
            lock.pop("sha256", None)
        saved = self._reproduce(to_v2)
        with patch.object(gsm, "IMPLICIT_DEFAULT", "off"):
            pkg = self.update(lock_file=saved)
        # Written before the setting existed: what that ivpm did.
        self.assertPopulated(pkg)

    def test_cached_spec_change_is_drift(self):
        cache = self.use_cache()
        self.write_ws("          cache: true\n"
                      "          submodules: true\n")
        pkg = self.update()
        self.assertPopulated(pkg)
        self.write_ws("          cache: true\n"
                      "          submodules: {recursive: false}\n")
        pkg = self.update()
        self.assertPopulated(pkg, nested=False)
        sha = self.git_out(self.top, "rev-parse", "HEAD")
        self.assertEqual(os.path.basename(os.path.realpath(pkg)),
                         sha + parse_spec({"recursive": False}).cache_suffix())
        self.assertEqual(len(self.cache_versions(cache)), 2)


# ===================================================================== #
# Resident (already-loaded) editable packages                           #
# ===================================================================== #

class TestResident(_Base):

    implicit = "off"

    def test_widened_spec_initializes_in_place(self):
        self.write_ws("          submodules: false\n")
        pkg = self.update()
        self.assertNotPopulated(pkg)
        marker = os.path.join(pkg, "local-work.txt")
        with open(marker, "w") as fp:
            fp.write("mine\n")
        self.write_ws("          submodules: true\n")
        with self.messages() as msgs:
            self.update()
        self.assertPopulated(pkg)
        self.assertTrue(os.path.isfile(marker), "the tree was re-cloned")
        self.assertIn("initializing 1 submodule: ext/mid", self.texts(msgs, "note"))
        self.assertEqual(self.lock_entry()["submodules"]["modules"][0]["status"],
                         "ok")

    def test_missing_init_is_repaired(self):
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        self.git(pkg, "submodule", "deinit", "-f", "ext/mid")
        self.update()
        self.assertPopulated(pkg)

    def test_moved_submodule_is_left_alone(self):
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        mid = os.path.join(pkg, "ext", "mid")
        mine = self.commit(mid, {"mine.txt": "x\n"}, "local work")
        with self.messages() as msgs, _SpyGit() as spy:
            self.update()
        self.assertEqual(self.git_out(mid, "rev-parse", "HEAD"), mine)
        self.assertEqual(spy.submodule_cmds, [])
        self.assertIn("ext/mid is checked out at", self.texts(msgs, "note"))

    def test_narrowed_spec_leaves_submodules_with_a_note(self):
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        self.write_ws("          submodules: false\n")
        with self.messages() as msgs:
            self.update()
        self.assertPopulated(pkg)
        self.assertIn("no longer requested; left in place",
                      self.texts(msgs, "note"))

    def test_unchanged_complete_tree_runs_no_submodule_command(self):
        self.write_ws("          submodules: true\n")
        self.update()
        with _SpyGit() as spy:
            self.update()
        self.assertEqual(spy.submodule_cmds, [])


# ===================================================================== #
# Sync                                                                   #
# ===================================================================== #

class TestSync(_Base):

    implicit = "off"

    def sync(self, dry_run=False):
        args = _SyncArgs()
        args.dry_run = dry_run
        results = ProjectOps(self.ws).sync(args=args)
        r = [r for r in results if r.name == "top"]
        self.assertEqual(len(r), 1)
        return r[0]

    def test_newly_added_upstream_submodule(self):
        plain = self._repo("plain", {"ivpm.yaml": "package:\n  name: top\n",
                                     "top.txt": "top\n"})
        self.write_ws("          branch: main\n"
                      "          submodules: true\n", repo=plain)
        pkg = self.update()
        self.assertFalse(os.path.exists(os.path.join(pkg, "ext")))
        self.git(plain, "submodule", "add", "file://%s" % self.mid, "ext/mid")
        self.git(plain, "commit", "-m", "add submodule")
        r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.SYNCED)
        self.assertPopulated(pkg)
        self.assertEqual(r.submodules, ["ext/mid"])

    def test_legacy_newly_added_upstream_submodule(self):
        plain = self._repo("plain", {"ivpm.yaml": "package:\n  name: top\n",
                                     "top.txt": "top\n"})
        self.write_ws("          branch: main\n", repo=plain)
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"):
            pkg = self.update()
            self.git(plain, "submodule", "add", "file://%s" % self.mid, "ext/mid")
            self.git(plain, "commit", "-m", "add submodule")
            self.assertEqual(self.sync().outcome, SyncOutcome.SYNCED)
        self.assertPopulated(pkg)

    def test_off_spec_does_no_submodule_work(self):
        plain = self._repo("plain", {"ivpm.yaml": "package:\n  name: top\n",
                                     "top.txt": "top\n"})
        self.write_ws("          branch: main\n"
                      "          submodules: false\n", repo=plain)
        pkg = self.update()
        self.git(plain, "submodule", "add", "file://%s" % self.mid, "ext/mid")
        self.git(plain, "commit", "-m", "add submodule")
        self.assertEqual(self.sync().outcome, SyncOutcome.SYNCED)
        self.assertNotPopulated(pkg)

    def test_up_to_date_repairs_a_missing_init(self):
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        self.git(pkg, "submodule", "deinit", "-f", "ext/mid")
        r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.SYNCED)
        self.assertEqual(r.detail, "initialized 1 submodule")
        self.assertPopulated(pkg)
        mods = self.lock_entry()["submodules"]["modules"]
        self.assertTrue(all(m["status"] == "ok" for m in mods))

    def test_dry_run_reports_without_touching(self):
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        self.git(pkg, "submodule", "deinit", "-f", "ext/mid")
        r = self.sync(dry_run=True)
        self.assertEqual(r.outcome, SyncOutcome.DRY_SUBMODULES)
        self.assertEqual(r.submodules, ["ext/mid"])
        self.assertNotPopulated(pkg)

    def _advance_mid_gitlink(self):
        """Upstream moves top's gitlink for ext/mid to a new mid commit."""
        new = self.commit(self.mid, {"mid.txt": "mid v2\n"}, "mid v2")
        sub = os.path.join(self.top, "ext", "mid")
        self.git(sub, "fetch", "origin")
        self.git(sub, "checkout", new)
        self.git(self.top, "add", "ext/mid")
        self.git(self.top, "commit", "-m", "bump mid")
        return new

    def test_changed_gitlink_updates_a_clean_submodule(self):
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        new = self._advance_mid_gitlink()
        r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.SYNCED)
        mid = os.path.join(pkg, "ext", "mid")
        self.assertEqual(self.git_out(mid, "rev-parse", "HEAD"), new)
        self.assertIn("updated 1", r.detail)
        rec = dict((m["path"], m) for m in self.lock_entry()["submodules"]["modules"])
        self.assertEqual(rec["ext/mid"]["commit"], new)

    def test_moved_submodule_blocks_the_merge(self):
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        mid = os.path.join(pkg, "ext", "mid")
        mine = self.commit(mid, {"mine.txt": "x\n"}, "local work")
        self._advance_mid_gitlink()
        r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.DIRTY)
        self.assertTrue(any("ext/mid" in f for f in r.dirty_files))
        self.assertEqual(self.git_out(mid, "rev-parse", "HEAD"), mine)

    def test_moved_submodule_is_left_when_up_to_date(self):
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        mid = os.path.join(pkg, "ext", "mid")
        mine = self.commit(mid, {"mine.txt": "x\n"}, "local work")
        r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.UP_TO_DATE)
        self.assertEqual(self.git_out(mid, "rev-parse", "HEAD"), mine)

    def test_local_work_at_a_changed_gitlink_is_a_conflict(self):
        # The merge itself is blocked by a dirty submodule (above), so this
        # drives the post-merge step directly: the submodule had a commit of
        # its own when the snapshot was taken, and the merge moved the gitlink.
        from ivpm.pkg_sync import PkgSyncResult
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg_dir = self.update()
        mid = os.path.join(pkg_dir, "ext", "mid")
        mine = self.commit(mid, {"mine.txt": "x\n"}, "local work")
        p = PackageGit("top")
        p.submodules_spec = parse_spec(True)
        pre = p._sync_submodule_snapshot(pkg_dir)
        self._advance_mid_gitlink()
        self.git(pkg_dir, "fetch", "origin")
        self.git(pkg_dir, "merge", "origin/main")
        res = PkgSyncResult(name="top", src_type="git", path=pkg_dir,
                            outcome=SyncOutcome.SYNCED)
        res = p._sync_submodules(res, pkg_dir, pre, True, False, None)
        self.assertEqual(res.outcome, SyncOutcome.SUBMODULE_CONFLICT)
        self.assertEqual(res.submodules, ["ext/mid"])
        self.assertEqual(self.git_out(mid, "rev-parse", "HEAD"), mine)
        self.assertTrue(res.next_steps[0].endswith("ext/mid && git status"))

    def test_failing_submodule_is_an_error(self):
        plain = self._repo("plain", {"ivpm.yaml": "package:\n  name: top\n",
                                     "top.txt": "top\n"})
        self.write_ws("          branch: main\n"
                      "          submodules: true\n", repo=plain)
        self.update()
        self.git(plain, "submodule", "add", "file://%s" % self.mid, "ext/mid")
        self.git(plain, "config", "-f", ".gitmodules", "submodule.ext/mid.url",
                 "file://%s/does-not-exist" % self.repos)
        self.git(plain, "commit", "-am", "add a broken submodule")
        r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.ERROR)
        self.assertIn("Failed to initialize submodules of top", r.error)
        # The merge happened: the lock must follow the superproject.
        self.assertEqual(self.lock_entry()["commit_resolved"],
                         self.git_out(plain, "rev-parse", "HEAD"))

    def test_optional_failure_keeps_the_outcome(self):
        plain = self._repo("plain", {"ivpm.yaml": "package:\n  name: top\n",
                                     "top.txt": "top\n"})
        self.write_ws("          branch: main\n"
                      "          submodules: {required: false}\n", repo=plain)
        self.update()
        self.git(plain, "submodule", "add", "file://%s" % self.mid, "ext/mid")
        self.git(plain, "config", "-f", ".gitmodules", "submodule.ext/mid.url",
                 "file://%s/does-not-exist" % self.repos)
        self.git(plain, "commit", "-am", "add a broken submodule")
        with self.messages():
            r = self.sync()
        self.assertEqual(r.outcome, SyncOutcome.SYNCED)
        self.assertEqual(r.detail, "submodules incomplete")


# ===================================================================== #
# Status and removal safety                                              #
# ===================================================================== #

class TestStatus(_Base):

    implicit = "off"

    def status(self):
        from ivpm.project_ops_info import ProjectStatusInfo
        p = PackageGit("top")
        p.submodules_spec = parse_spec(True)
        return p.status(ProjectStatusInfo(args=None,
                                          deps_dir=os.path.join(self.ws, "packages")))

    def by_path(self, st):
        return dict((m.path, m) for m in st.submodules)

    def test_clean(self):
        self.write_ws("          submodules: true\n")
        self.update()
        st = self.status()
        self.assertEqual(st.submodule_summary(), "submodules 2/2")
        self.assertFalse(st.is_dirty)
        self.assertEqual(st.submodules_spec, "recursive")

    def test_uninitialized(self):
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        self.git(pkg, "submodule", "deinit", "-f", "ext/mid")
        st = self.status()
        self.assertEqual(self.by_path(st)["ext/mid"].state, "uninitialized")
        self.assertIn("1 uninitialized: ext/mid", st.submodule_summary())

    def test_moved_is_dirty_but_not_a_modified_line(self):
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        self.commit(os.path.join(pkg, "ext", "mid"), {"mine.txt": "x\n"})
        st = self.status()
        self.assertEqual(self.by_path(st)["ext/mid"].state, "moved")
        self.assertTrue(st.is_dirty)
        self.assertFalse([ln for ln in st.modified if "ext/mid" in ln])

    def test_dirty_submodule(self):
        self.write_ws("          submodules: true\n")
        pkg = self.update()
        with open(os.path.join(pkg, "ext", "mid", "mid.txt"), "w") as fp:
            fp.write("edited\n")
        st = self.status()
        m = self.by_path(st)["ext/mid"]
        self.assertEqual(m.state, "ok")
        self.assertTrue(m.dirty)
        self.assertTrue(st.is_dirty)

    def test_not_requested(self):
        self.write_ws("          submodules: false\n")
        self.update()
        from ivpm.project_ops_info import ProjectStatusInfo
        p = PackageGit("top")
        p.submodules_spec = parse_spec(False)
        st = p.status(ProjectStatusInfo(args=None,
                                        deps_dir=os.path.join(self.ws, "packages")))
        self.assertEqual(self.by_path(st)["ext/mid"].state, "not-requested")
        self.assertEqual(st.submodule_problems, [])

    def test_failed_from_the_lock(self):
        self.use_cache()
        self.write_ws("          submodules: {required: false}\n",
                      repo=self.bad_top())
        with self.messages():
            self.update()
        _, results = ProjectOps(self.ws).status(args=types.SimpleNamespace(
            no_rich=True, verbose=0))
        st = [r for r in results if r.name == "top"][0]
        self.assertEqual(self.by_path(st)["ext/mid"].state, "failed")


class TestRemovalSafety(_Base):

    implicit = "off"

    def verdict(self, pkg):
        p = PackageGit("top")
        p.path = pkg
        return p.removal_safety(None)

    def reasons(self, v):
        return dict((r.kind, r) for r in v.reasons)

    def test_clean_submodules_are_safe(self):
        from ivpm.pkg_remove import SafetyLevel
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        v = self.verdict(self.update())
        self.assertEqual(v.level, SafetyLevel.SAFE, v.reasons)

    def test_shallow_submodules_are_safe(self):
        # A submodule fetched by commit is on no remote-tracking ref; that is
        # not unpushed work.
        from ivpm.pkg_remove import SafetyLevel
        self.write_ws("          branch: main\n"
                      "          cache: false\n"
                      "          submodules: {depth: 1}\n")
        v = self.verdict(self.update())
        self.assertEqual(v.level, SafetyLevel.SAFE, v.reasons)

    def test_unpushed_submodule_commit_blocks(self):
        from ivpm.pkg_remove import SafetyLevel
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        mid = os.path.join(pkg, "ext", "mid")
        self.commit(mid, {"mine.txt": "x\n"}, "local work")
        # Put the superproject back to a clean view of the submodule, so only
        # the submodule check can see the commit: the work lives on a branch.
        self.git(mid, "branch", "keep")
        self.git(mid, "checkout", "-q",
                 self.git_out(self.mid, "rev-parse", "HEAD"))
        v = self.verdict(pkg)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        r = self.reasons(v)["submodule"]
        self.assertEqual(r.data["paths"], ["ext/mid"])
        self.assertIn("ext/mid: 1 commit not on any remote", r.items)

    def test_submodule_stash_blocks(self):
        from ivpm.pkg_remove import SafetyLevel
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        leaf = os.path.join(pkg, "ext", "mid", "leaf")
        with open(os.path.join(leaf, "leaf.txt"), "w") as fp:
            fp.write("edited\n")
        self.git(leaf, "stash")
        v = self.verdict(pkg)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertIn("ext/mid/leaf: 1 stash entry",
                      self.reasons(v)["submodule"].items)

    def test_deinitialized_submodule_work_blocks(self):
        # deinit removes the checkout but keeps .git/modules/<name>, which is
        # deleted with the tree.
        from ivpm.pkg_remove import SafetyLevel
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        mid = os.path.join(pkg, "ext", "mid")
        self.git(mid, "checkout", "-q", "-b", "work")
        self.commit(mid, {"mine.txt": "x\n"}, "local work")
        self.git(pkg, "submodule", "deinit", "-f", "ext/mid")
        v = self.verdict(pkg)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertIn("ext/mid: 1 commit not on any remote (deinitialized)",
                      self.reasons(v)["submodule"].items)

    def test_dirty_submodule_blocks(self):
        from ivpm.pkg_remove import SafetyLevel
        self.write_ws("          branch: main\n"
                      "          submodules: true\n")
        pkg = self.update()
        with open(os.path.join(pkg, "ext", "mid", "mid.txt"), "w") as fp:
            fp.write("edited\n")
        v = self.verdict(pkg)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertIn("ext/mid: 1 modified file",
                      self.reasons(v)["submodule"].items)


# ===================================================================== #
# Patches                                                                #
# ===================================================================== #

class TestPatches(_Base):

    implicit = "off"

    def test_patch_into_a_submodule_is_rejected(self):
        with open(os.path.join(self.ws, "sub.patch"), "w") as fp:
            fp.write("--- a/ext/mid/mid.txt\n+++ b/ext/mid/mid.txt\n"
                     "@@ -1 +1 @@\n-mid\n+patched\n")
        self.write_ws("          submodules: true\n"
                      "          patches:\n"
                      "            - sub.patch\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("inside submodule ext/mid", str(cm.exception))

    def test_build_output_in_a_submodule_is_not_drift(self):
        # An editable patched dependency whose submodule holds untracked
        # output must still accept a changed patch set.
        p = os.path.join(self.ws, "fix.patch")
        with open(p, "w") as fp:
            fp.write("--- a/top.txt\n+++ b/top.txt\n@@ -1 +1 @@\n-top\n+one\n")
        self.write_ws("          cache: false\n"
                      "          submodules: true\n"
                      "          patches:\n"
                      "            - fix.patch\n")
        pkg = self.update()
        with open(os.path.join(pkg, "ext", "mid", "build.log"), "w") as fp:
            fp.write("output\n")
        with open(p, "w") as fp:
            fp.write("--- a/top.txt\n+++ b/top.txt\n@@ -1 +1 @@\n-top\n+two\n")
        self.update()
        with open(os.path.join(pkg, "top.txt")) as fp:
            self.assertEqual(fp.read(), "two\n")
        self.assertTrue(os.path.isfile(os.path.join(pkg, "ext", "mid", "build.log")))

    def test_rejected_even_when_submodules_are_off(self):
        # Declared in .gitmodules is enough: the path is not the
        # superproject's to patch.
        with open(os.path.join(self.ws, "sub.patch"), "w") as fp:
            fp.write("--- a/ext/mid/new.txt\n+++ b/ext/mid/new.txt\n"
                     "@@ -0,0 +1 @@\n+new\n")
        self.write_ws("          patches:\n"
                      "            - sub.patch\n")
        with self.assertRaises(Exception) as cm:
            self.update()
        self.assertIn("inside submodule ext/mid", str(cm.exception))


# ===================================================================== #
# URL mapping                                                            #
# ===================================================================== #

class TestUrlMapping(_Base):

    implicit = "off"

    def test_git_url_map_applies_to_submodule_urls(self):
        # The submodule is declared at a URL that does not exist; a
        # git-url-map rule redirects it to the real repository, just as it
        # would a dependency URL.
        sup = self._repo("sup", {"ivpm.yaml": "package:\n  name: top\n",
                                 "top.txt": "top\n"},
                         submodules={"ext/mid": self.mid})
        self.git(sup, "config", "-f", ".gitmodules", "submodule.ext/mid.url",
                 "https://git.invalid/org/mid.git")
        self.git(sup, "commit", "-am", "public url")
        os.environ["IVPM_GIT_URL_MAP"] = (
            "https://git.invalid/org/mid.git=file://%s" % self.mid)
        self.write_ws("          submodules: {recursive: false}\n", repo=sup)
        pkg = self.update()
        self.assertTrue(os.path.isfile(os.path.join(pkg, "ext", "mid", "mid.txt")))

    def test_insteadof_args(self):
        os.environ["IVPM_GIT_URL_MAP"] = "https://a.invalid/x.git=https://b.invalid/x.git"
        d = os.path.join(self.testdir, "gm")
        os.makedirs(d)
        with open(os.path.join(d, ".gitmodules"), "w") as fp:
            fp.write('[submodule "x"]\n\tpath = x\n\turl = https://a.invalid/x.git\n'
                     '[submodule "r"]\n\tpath = r\n\turl = ../r.git\n')
        p = PackageGit("top")
        p.url = "https://h.invalid/top.git"
        args = p._sm_config_args("https://h.invalid/top.git", None, d)
        self.assertEqual(args, ["-c", "url.https://b.invalid/x.git.insteadOf="
                                      "https://a.invalid/x.git"])

    def test_ssh_superproject_rewrites_same_host_https(self):
        d = os.path.join(self.testdir, "gm")
        os.makedirs(d)
        with open(os.path.join(d, ".gitmodules"), "w") as fp:
            fp.write('[submodule "x"]\n\tpath = x\n\turl = https://h.invalid/o/x.git\n'
                     '[submodule "y"]\n\tpath = y\n\turl = https://other.invalid/o/y.git\n')
        p = PackageGit("top")
        args = p._sm_config_args("git@h.invalid:o/top.git", None, d)
        self.assertIn("url.git@h.invalid:o/x.git.insteadOf=https://h.invalid/o/x.git",
                      args)
        # Another host is left as written: no SSH keys needed for it.
        self.assertFalse([a for a in args if "other.invalid" in a])


# ===================================================================== #
# Root project                                                           #
# ===================================================================== #

class TestRoot(_Base):

    implicit = "off"

    def make_root(self, submodules="true"):
        """A root project repo declaring its own submodules, cloned (without
        them) as the workspace."""
        self.add_other()
        yaml = ("package:\n"
                "  name: root\n"
                "  submodules: %s\n"
                "  dep-sets:\n"
                "    - name: default-dev\n"
                "      deps:\n"
                "        - name: leafdep\n"
                "          src: git\n"
                "          url: file://%s\n" % (submodules, self.leaf))
        self.commit(self.top, {"ivpm.yaml": yaml}, "root manifest")
        shutil.rmtree(self.ws)
        self.git(self.testdir, "clone", "file://%s" % self.top, self.ws)
        return self.ws

    def update_root(self, args=None):
        class Args(object):
            anonymous_git = None
        a = args or Args()
        ProjectOps(self.ws, a).update(dep_set="default-dev", skip_venv=True,
                                      args=a)

    def test_update_initializes_root_submodules(self):
        root = self.make_root()
        self.assertFalse(os.path.isfile(os.path.join(root, "ext", "mid", "mid.txt")))
        with self.messages() as msgs:
            self.update_root()
        self.assertTrue(os.path.isfile(os.path.join(root, "ext", "mid", "leaf",
                                                    "leaf.txt")))
        self.assertTrue(os.path.isfile(os.path.join(root, "ext", "other", "other.txt")))
        self.assertIn("initializing 2 root submodules", self.texts(msgs, "note"))

    def test_root_paths_subset(self):
        root = self.make_root("{paths: [ext/other]}")
        self.update_root()
        self.assertTrue(os.path.isfile(os.path.join(root, "ext", "other", "other.txt")))
        self.assertFalse(os.path.isfile(os.path.join(root, "ext", "mid", "mid.txt")))

    def test_root_moved_submodule_is_not_touched(self):
        root = self.make_root()
        self.update_root()
        mid = os.path.join(root, "ext", "mid")
        mine = self.commit(mid, {"mine.txt": "x\n"}, "root-side work")
        with self.messages() as msgs:
            self.update_root()
        self.assertEqual(self.git_out(mid, "rev-parse", "HEAD"), mine)
        self.assertIn("root submodule ext/mid is checked out at",
                      self.texts(msgs, "note"))

    def test_root_without_setting_is_untouched(self):
        root = self.make_root("false")
        self.update_root()
        self.assertFalse(os.path.isfile(os.path.join(root, "ext", "mid", "mid.txt")))

    def test_no_recurse_flag_suppresses_root_init(self):
        root = self.make_root()

        class Args(object):
            anonymous_git = None
            root_submodules = False
        self.update_root(Args())
        self.assertFalse(os.path.isfile(os.path.join(root, "ext", "mid", "mid.txt")))

    def test_bad_root_spec_is_fatal(self):
        self.make_root("{depth: zero}")
        with self.assertRaises(Exception) as cm:
            self.update_root()
        self.assertIn("submodules.depth", str(cm.exception))

    # -- ivpm clone flags (GitCloneProvider) ------------------------------ #

    def _clone_req(self, target, **flags):
        pa = types.SimpleNamespace(recurse_submodules=False,
                                   submodule_depth=None,
                                   no_recurse_submodules=False)
        for k, v in flags.items():
            setattr(pa, k, v)
        return types.SimpleNamespace(provider_args=pa,
                                     args=types.SimpleNamespace(),
                                     src="file://%s" % self.top,
                                     target_dir=target)

    def _provider_clone(self, **flags):
        from ivpm.clone.git_clone_provider import GitCloneProvider
        target = os.path.join(self.testdir, "cloned")
        self.git(self.testdir, "clone", "file://%s" % self.top, target)
        req = self._clone_req(target, **flags)
        prov = GitCloneProvider()
        msg = prov._clone_submodules(req, "file://%s" % self.top, target,
                                     None, False)
        return target, req, msg

    def test_clone_recurse_submodules(self):
        target, _, msg = self._provider_clone(recurse_submodules=True)
        self.assertIsNone(msg)
        self.assertTrue(os.path.isfile(os.path.join(target, "ext", "mid", "leaf",
                                                    "leaf.txt")))

    def test_clone_submodule_depth(self):
        target, _, msg = self._provider_clone(submodule_depth="1")
        self.assertIsNone(msg)
        self.assertTrue(self.is_shallow(os.path.join(target, "ext", "mid")))

    def test_clone_without_flags_does_nothing(self):
        target, _, msg = self._provider_clone()
        self.assertIsNone(msg)
        self.assertFalse(os.path.isfile(os.path.join(target, "ext", "mid", "mid.txt")))

    def test_clone_no_recurse_marks_args(self):
        _, req, msg = self._provider_clone(no_recurse_submodules=True)
        self.assertIsNone(msg)
        self.assertIs(req.args.root_submodules, False)

    def test_clone_conflicting_flags(self):
        _, _, msg = self._provider_clone(no_recurse_submodules=True,
                                         recurse_submodules=True)
        self.assertIn("conflicts", msg)

    def test_clone_bad_depth(self):
        _, _, msg = self._provider_clone(submodule_depth="none")
        self.assertIn("--submodule-depth", msg)

    def test_clone_failure_is_reported(self):
        from ivpm.clone.git_clone_provider import GitCloneProvider
        bad = self.bad_top()
        target = os.path.join(self.testdir, "cloned")
        self.git(self.testdir, "clone", "file://%s" % bad, target)
        req = self._clone_req(target, recurse_submodules=True)
        msg = GitCloneProvider()._clone_submodules(req, "file://%s" % bad,
                                                   target, None, False)
        self.assertIn("Failed to initialize submodules", msg)
