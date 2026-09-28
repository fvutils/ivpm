"""'ivpm skills': install agent skills a la carte (ivpm.agent_skills.manager).

Every test runs against a real, local venv (skills_fixtures) -- no network.
The uv-backed '--with' tests hand uv a locally written wheel with --offline
and are skipped when uv is not installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ivpm.agent_skills import install as inst
from ivpm.agent_skills import manager as mgr
from ivpm.agent_skills import state

from . import skills_fixtures as fx

CLEAN_ENV = {k: v for k, v in os.environ.items()
             if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "src")


class SkillsTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ivpm-skills-cmd-"))
        self.root = os.path.join(self.tmp, "proj")
        os.makedirs(self.root)
        self.venv = os.path.join(self.tmp, "venv")
        self.py, self.site = fx.make_venv(self.venv)
        files, eps = fx.skill_package("pss", ["pss", "pss-api", "pss-checkers"])
        fx.add_dist(self.site, "pss-parser", "3.1", files, eps)
        files, eps = fx.skill_package("other", ["other"])
        fx.add_dist(self.site, "other-dist", "0.5", files, eps)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def info(self, python=None):
        choice = mgr.EnvChoice(argv=[python or self.py], reason="--python", env=CLEAN_ENV)
        return mgr.query(choice)

    def install(self, *selectors, **kw):
        return mgr.install(self.root, self.info(), list(selectors), **kw)

    def names(self, agent="agents"):
        d = {"agents": ".agents", "claude": ".claude", "cursor": ".cursor"}[agent]
        path = os.path.join(self.root, d, "skills")
        return sorted(os.listdir(path)) if os.path.isdir(path) else []

    def link(self, name, agent=".agents"):
        return os.path.join(self.root, agent, "skills", name)

    def src(self, name):
        return os.path.join(self.site, "pss", "share", "skills", name)


class TestInstall(SkillsTestBase):

    def test_install_by_name_links_into_every_agent(self):
        report = self.install("pss-api")
        self.assertEqual("link", report.mode)
        for agent in ("agents", "claude", "cursor"):
            self.assertEqual(["pss-api"], self.names(agent))
        self.assertTrue(os.path.islink(self.link("pss-api")))
        self.assertEqual(os.path.realpath(self.src("pss-api")),
                         os.path.realpath(self.link("pss-api")))

        st = state.load(self.root)
        self.assertEqual([("ep:pss", "pss-api", "pss-parser")],
                         [(s.provider, s.skill, s.dist) for s in st.selections])
        self.assertEqual({"agents_skills", "claude_skills", "cursor_skills"}, set(st.installed))
        self.assertEqual({"pss-parser": "3.1"}, st.resolved["dists"])
        # Portable: selections carry no paths
        raw = json.load(open(state.state_path(self.root)))
        self.assertNotIn(self.tmp, json.dumps(raw["selections"]))

    def test_selectors(self):
        cases = [(["pss"], ["pss", "pss-api", "pss-checkers"]),   # entry point name
                 (["dist:pss_parser"], ["pss", "pss-api", "pss-checkers"]),
                 (["pss/pss-checkers"], ["pss-checkers"]),
                 (["pss-*"], ["pss-api", "pss-checkers"]),
                 (["other", "pss-api"], ["other", "pss-api"])]
        for sels, expected in cases:
            with self.subTest(sels=sels):
                self.tearDown()
                self.setUp()
                self.install(*sels)
                self.assertEqual(expected, self.names())

    def test_all_includes_ivpm_own_skill(self):
        self.install(all_=True)
        self.assertEqual(["ivpm", "other", "pss", "pss-api", "pss-checkers"], self.names())

    def test_unknown_selector_is_an_error(self):
        with self.assertRaisesRegex(mgr.SkillsError, "matches no available skill"):
            self.install("nope")

    def test_same_name_from_two_providers_is_ambiguous(self):
        files, eps = fx.skill_package("dup", ["pss-api"])
        fx.add_dist(self.site, "dup", "1.0", files, eps)
        with self.assertRaisesRegex(mgr.SkillsError, "ambiguous.*dup/pss-api"):
            self.install("pss-api")
        # Qualified works, and --as resolves the name clash
        self.install("pss/pss-api")
        self.install("dup/pss-api", as_name="dup-api")
        self.assertEqual(["dup-api", "pss-api"], self.names())

    def test_agent_selection_is_remembered(self):
        self.install("pss-api", agents=["claude"])
        self.assertEqual(["pss-api"], self.names("claude"))
        self.assertEqual(["pss-api"], self.names("agents"))    # always written
        self.assertEqual([], self.names("cursor"))
        self.install("other")
        self.assertEqual(["other", "pss-api"], self.names("claude"))
        self.assertEqual([], self.names("cursor"))
        self.assertEqual(["agents", "claude"], state.load(self.root).agents)

    def test_copy_mode_records_hash(self):
        self.install("pss", mode="copy")
        dest = self.link("pss")
        self.assertFalse(os.path.islink(dest))
        self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")))
        ent = state.load(self.root).installed["agents_skills"][0]
        self.assertEqual(("copy", inst.content_hash(self.src("pss"))), (ent.mode, ent.hash))

    def test_skills_in_uv_cache_are_copied(self):
        report = mgr.install(self.root, self.info(), ["pss"], cache_dir=self.venv)
        self.assertEqual("copy", report.mode)
        self.assertIn("uv's cache", report.copy_reason)
        self.assertFalse(os.path.islink(self.link("pss")))
        self.assertEqual("link", state.load(self.root).mode)   # preference kept

    def test_unmanaged_entry_is_never_overwritten(self):
        os.makedirs(self.link("pss-api", ".claude"))
        with self.assertRaisesRegex(mgr.SkillsError, "not created by IVPM.*--as"):
            self.install("pss-api")
        self.assertIsNone(state.load(self.root))
        self.install("pss-api", as_name="my-pss-api")
        self.assertEqual(["my-pss-api", "pss-api"], self.names("claude"))

    def test_name_owned_by_ivpm_update_is_refused(self):
        with open(os.path.join(self.root, "ivpm.yaml"), "w") as fh:
            fh.write("package:\n    name: proj\n")
        os.makedirs(os.path.join(self.root, "packages"))
        with open(os.path.join(self.root, "packages", "ivpm.json"), "w") as fh:
            json.dump({"handlers": {"agents": {"agents_skills": ["pss"]}}}, fh)
        os.makedirs(os.path.join(self.root, ".agents", "skills", "pss"))
        with self.assertRaisesRegex(mgr.SkillsError, "ivpm update.*entrypoints"):
            self.install("pss")


class TestUninstallSyncStatus(SkillsTestBase):

    def test_uninstall_removes_exactly_what_was_installed(self):
        os.makedirs(self.link("mine", ".claude"))
        self.install("pss")
        mgr.uninstall(self.root, ["pss-api"])
        self.assertEqual(["pss", "pss-checkers"], self.names())
        self.assertEqual(["mine", "pss", "pss-checkers"], self.names("claude"))
        mgr.uninstall(self.root, [], all_=True)
        self.assertEqual([], self.names())
        self.assertEqual(["mine"], self.names("claude"))
        self.assertEqual([], state.load(self.root).selections)

    def test_uninstall_unknown_is_an_error(self):
        self.install("pss-api")
        with self.assertRaisesRegex(mgr.SkillsError, "matches no installed skill"):
            mgr.uninstall(self.root, ["nope"])

    def test_sync_recreates_from_state_file_alone(self):
        self.install("pss-api", "other", mode="copy")
        before = {n: inst.content_hash(self.link(n)) for n in self.names()}
        saved = open(state.state_path(self.root)).read()
        for d in (".agents", ".claude", ".cursor"):
            shutil.rmtree(os.path.join(self.root, d))
        os.makedirs(os.path.join(self.root, ".agents"))
        with open(state.state_path(self.root), "w") as fh:
            fh.write(saved)
        mgr.sync(self.root, self.info())
        self.assertEqual(before, {n: inst.content_hash(self.link(n)) for n in self.names()})
        self.assertEqual(["other", "pss-api"], self.names("cursor"))

    def test_sync_after_upgrade_reports_version_change(self):
        self.install("pss-api")
        fx.remove_dist(self.site, "pss-parser", "3.1")
        files, eps = fx.skill_package("pss", ["pss", "pss-api", "pss-checkers"])
        fx.add_dist(self.site, "pss-parser", "3.2", files, eps)
        report = mgr.sync(self.root, self.info())
        self.assertEqual({"pss-parser": ("3.1", "3.2")}, report.version_changes)
        self.assertEqual({"pss-parser": "3.2"}, state.load(self.root).resolved["dists"])

    def test_provider_rename_is_followed_by_dist(self):
        """One entry point per skill -> one for all: selections still resolve."""
        self.install("pss-api")
        fx.remove_dist(self.site, "pss-parser", "3.1")
        files, eps = fx.skill_package("pss", ["pss", "pss-api"], ep_name="pss-all")
        fx.add_dist(self.site, "pss-parser", "3.2", files, eps)
        report = mgr.sync(self.root, self.info())
        self.assertEqual([], report.missing)
        self.assertEqual(["pss-api"], self.names())

    def test_missing_selection_is_left_in_place(self):
        self.install("pss-api", "other")
        fx.remove_dist(self.site, "other-dist", "0.5")
        report = mgr.sync(self.root, self.info())
        self.assertEqual(["other"], [s.skill for s in report.missing])
        self.assertEqual(["other", "pss-api"], self.names())
        _, problems = mgr.status(self.root, self.info())
        self.assertEqual([("missing", "other")], [(p.kind, p.name) for p in problems])

    def test_status_reports_stale_copy(self):
        self.install("pss-checkers", mode="copy")
        with open(os.path.join(self.src("pss-checkers"), "SKILL.md"), "a") as fh:
            fh.write("changed\n")
        _, problems = mgr.status(self.root, self.info())
        self.assertEqual({("stale", "pss-checkers")}, {(p.kind, p.name) for p in problems})
        mgr.sync(self.root, self.info())
        _, problems = mgr.status(self.root, self.info())
        self.assertEqual([], problems)

    def test_status_reports_dangling_link_and_absent_entry(self):
        self.install("pss-api", "pss-checkers")
        shutil.rmtree(self.src("pss-api"))
        os.unlink(self.link("pss-checkers", ".cursor"))
        _, problems = mgr.status(self.root, None)
        kinds = {(p.kind, p.name, p.target) for p in problems}
        self.assertIn(("dangling", "pss-api", "agents_skills"), kinds)
        self.assertIn(("absent", "pss-checkers", "cursor_skills"), kinds)

    def test_copy_mode_is_state_wide(self):
        self.install("pss-api")
        self.install("pss-checkers", mode="copy")
        self.assertFalse(os.path.islink(self.link("pss-api")))

    def test_ownership(self):
        self.install("pss-api")
        os.makedirs(self.link("handmade"))
        owners = mgr.ownership(self.root, state.load(self.root))["agents_skills"]
        self.assertEqual({"pss-api": "ivpm skills", "handmade": "unmanaged"}, owners)


class TestChooseEnv(SkillsTestBase):

    def choose(self, environ, **kw):
        environ = dict(environ)
        environ.setdefault("UV_CACHE_DIR", os.path.join(self.tmp, "uv-cache"))
        return mgr.choose_env(self.root, environ=environ, **kw)

    def test_order(self):
        other_venv = os.path.join(self.tmp, "other")
        other_py, _ = fx.make_venv(other_venv)
        dot_py, _ = fx.make_venv(os.path.join(self.root, ".venv"))

        self.assertEqual(".venv", self.choose({}).reason)
        c = self.choose({"VIRTUAL_ENV": other_venv})
        self.assertEqual(("VIRTUAL_ENV", other_py), (c.reason, c.argv[0]))
        c = self.choose({"VIRTUAL_ENV": other_venv}, python=self.py)
        self.assertEqual(("--python", self.py), (c.reason, c.argv[0]))

        with open(os.path.join(self.root, "ivpm.yaml"), "w") as fh:
            fh.write("package:\n    name: proj\n")
        proj_py, _ = fx.make_venv(os.path.join(self.root, "packages", "python"))
        c = self.choose({"VIRTUAL_ENV": other_venv})
        self.assertEqual(("IVPM project venv", proj_py), (c.reason, c.argv[0]))

        shutil.rmtree(os.path.join(self.root, ".venv"))
        os.unlink(os.path.join(self.root, "ivpm.yaml"))
        self.assertEqual((sys.executable, "interpreter running ivpm"),
                         (self.choose({}).argv[0], self.choose({}).reason))

    def test_uvx_environment_beats_activated_venv(self):
        other_venv = os.path.join(self.tmp, "other")
        fx.make_venv(other_venv)
        with patch.object(mgr, "is_under", return_value=True), \
                patch.object(mgr, "_own_env_offers_skills", return_value=True):
            c = self.choose({"VIRTUAL_ENV": other_venv})
        self.assertEqual(("uvx environment", sys.executable), (c.reason, c.argv[0]))

    def test_with_builds_uv_command_and_scrubs_environment(self):
        uv = os.path.join(self.tmp, "uv")
        with open(uv, "w") as fh:
            fh.write("")
        c = self.choose({"UV": uv, "PYTHONPATH": "/x", "VIRTUAL_ENV": "/y"},
                        with_specs=["pssparser", "other>=1"], offline=True)
        self.assertEqual([uv, "run", "--no-project", "--isolated", "--with", "pssparser",
                          "--with", "other>=1", "--offline", "python"], c.argv)
        self.assertNotIn("PYTHONPATH", c.env)
        self.assertNotIn("VIRTUAL_ENV", c.env)

    def test_with_without_uv_is_an_error(self):
        with patch.object(mgr, "find_uv", return_value=None):
            with self.assertRaisesRegex(mgr.SkillsError, "needs uv"):
                self.choose({}, with_specs=["pssparser"])

    def test_sync_reuses_recorded_with_specs(self):
        uv = os.path.join(self.tmp, "uv")
        open(uv, "w").close()
        state.save(self.root, state.SkillsState(with_specs=["pssparser"]))
        c = mgr.sync_choice(self.root, None, ["extra"],
                            environ={"UV": uv, "UV_CACHE_DIR": self.tmp})
        self.assertEqual(["pssparser", "extra"], c.with_specs)
        c = mgr.sync_choice(self.root, self.py, [], environ={"UV_CACHE_DIR": self.tmp})
        self.assertEqual("--python", c.reason)


_UV = mgr.find_uv(CLEAN_ENV)


@unittest.skipUnless(_UV, "uv not available")
class TestWithUv(SkillsTestBase):
    """'--with' end to end, offline, with a wheel written by the test."""

    def test_install_with_local_wheel(self):
        files, eps = fx.skill_package("wpkg", ["wskill-a", "wskill-b"])
        wheel = fx.make_wheel(os.path.join(self.tmp, "dist"), "wpkg", "2.0", files, eps)
        choice = mgr.choose_env(self.root, with_specs=[wheel], offline=True,
                                environ=CLEAN_ENV)
        info = mgr.query(choice)
        report = mgr.install(self.root, info, ["wpkg"], cache_dir=mgr.uv_cache_dir())
        self.assertEqual("copy", report.mode)
        self.assertEqual(["wskill-a", "wskill-b"], self.names("claude"))
        self.assertFalse(os.path.islink(self.link("wskill-a")))
        st = state.load(self.root)
        self.assertEqual([wheel], st.with_specs)
        self.assertEqual({"wpkg": "2.0"}, st.resolved["dists"])


class TestCli(SkillsTestBase):
    """A few runs through the real command line."""

    def ivpm(self, *args):
        env = dict(CLEAN_ENV, PYTHONPATH=SRC_DIR)
        return subprocess.run([sys.executable, "-m", "ivpm", "skills"] + list(args),
                              capture_output=True, text=True, env=env, cwd=self.root)

    def test_list_install_status_uninstall(self):
        r = self.ivpm("list", "--python", self.py, "--json")
        self.assertEqual(0, r.returncode, r.stderr)
        names = [s["name"] for s in json.loads(r.stdout)["skills"]]
        self.assertEqual(["ivpm", "other", "pss", "pss-api", "pss-checkers"], names)

        r = self.ivpm("add", "pss-api", "--python", self.py, "--agent", "claude")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("installed pss-api", r.stdout)
        self.assertEqual(["pss-api"], self.names("claude"))

        r = self.ivpm("list", "--python", self.py, "--no-rich")
        self.assertRegex(r.stdout, r"pss-api .*agents, claude \(ivpm skills\)")

        r = self.ivpm("status", "--python", self.py)
        self.assertIn("No problems found.", r.stdout)

        r = self.ivpm("remove", "--all")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.names("claude"))

    def test_errors_exit_nonzero_with_message(self):
        r = self.ivpm("install", "nope", "--python", self.py)
        self.assertEqual(1, r.returncode)
        self.assertIn("ivpm skills: error: 'nope' matches no available skill", r.stderr)
        r = self.ivpm("install", "--python", self.py)
        self.assertEqual(1, r.returncode)
        self.assertIn("--all", r.stderr)


if __name__ == "__main__":
    unittest.main()
