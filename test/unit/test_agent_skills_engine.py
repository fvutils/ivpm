"""Unit tests of the agent-skills engine pieces that need no environment."""
import json
import logging
import os
import shutil
import tempfile
import unittest

from ivpm.agent_skills import frontmatter, install, naming, select, state
from ivpm.agent_skills.model import SkillEntry

from .skills_fixtures import skill_md


class EngineTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ivpm-skills-engine-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def skill(self, rel, name, description=None):
        path = os.path.join(self.tmp, rel)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "SKILL.md"), "w") as fh:
            fh.write(skill_md(name, description))
        return path


class TestFrontmatter(EngineTestBase):

    def test_quoted_values_unquoted(self):
        path = os.path.join(self.tmp, "SKILL.md")
        with open(path, "w") as fh:
            fh.write("---\nname: \"quoted\"\ndescription: 'single'\n---\n")
        self.assertEqual({"name": "quoted", "description": "single"},
                         frontmatter.parse(path))

    def test_name_problems(self):
        self.assertEqual([], frontmatter.name_problems("pssparser-api"))
        self.assertEqual([], frontmatter.name_problems("a1"))
        for bad in ("Upper", "a--b", "-a", "a-", "a_b", "a b"):
            self.assertTrue(frontmatter.name_problems(bad), bad)
        self.assertTrue(frontmatter.name_problems("a" * 65))
        self.assertEqual([], frontmatter.name_problems("a" * 64))

    def test_safe_dir_name(self):
        for bad in ("", ".", "..", "a/b", "a\\b"):
            self.assertFalse(frontmatter.is_safe_dir_name(bad))
        self.assertTrue(frontmatter.is_safe_dir_name("Upper Case"))


class TestNaming(EngineTestBase):

    def test_frontmatter_name_first_for_every_route(self):
        dep = self.skill("pkg/skills/dir-a", "alpha")
        ep = self.skill("site/x", "beta")
        entries = [SkillEntry("dependency", "pkg", os.path.join(self.tmp, "pkg"), dep),
                   SkillEntry("dependency", "ep", ep, ep, source="entrypoint"),
                   SkillEntry("project", "proj", self.tmp,
                              self.skill("skills/gamma", "gamma"))]
        with self.assertLogs("ivpm.agent_skills.naming", logging.WARNING):
            names = [n for n, _ in naming.assign_dest_names(entries)]
        self.assertEqual(["alpha", "beta", "gamma"], names)

    def test_collision_escalates_to_owner_prefix(self):
        a = self.skill("a/review", "review")
        b = self.skill("b/review", "review")
        entries = [SkillEntry("dependency", "a", a, a, source="entrypoint"),
                   SkillEntry("dependency", "b", b, b, source="entrypoint")]
        with self.assertLogs("ivpm.agent_skills.naming", logging.WARNING) as cm:
            names = sorted(n for n, _ in naming.assign_dest_names(entries))
        self.assertEqual(["a-review", "b-review"], names)
        self.assertIn("2 skills are named 'review'", cm.output[0])

    def test_reserved_names_are_skipped(self):
        a = self.skill("a/review", "review")
        entries = [SkillEntry("dependency", "a", a, a, source="entrypoint")]
        self.assertEqual(["a-review"],
                         [n for n, _ in naming.assign_dest_names(entries, reserved={"review"})])

    def test_tree_root_skill_is_not_held_to_dir_name_rule(self):
        root = self.skill("my_repo", "my-skill")
        entry = SkillEntry("dependency", "my_repo", root, root)
        logger = logging.getLogger("ivpm.agent_skills.naming")
        with self.assertNoLogs(logger, logging.WARNING):
            self.assertEqual("my-skill", naming.name_candidates(entry)[0])

    def test_unsafe_frontmatter_name_falls_back_to_route(self):
        d = self.skill("pkg/skills/alpha", "../evil")
        entry = SkillEntry("dependency", "pkg", os.path.join(self.tmp, "pkg"), d)
        self.assertEqual(["pkg-alpha", "pkg-skills-alpha"], naming.name_candidates(entry))

    def test_plugin_skills_keep_plugin_prefix(self):
        d = self.skill("plug/skills/one", "one")
        entry = SkillEntry("plugin-dependency", "pkg", os.path.join(self.tmp, "plug"), d,
                           plugin_name="plug")
        self.assertEqual("plug-one", naming.name_candidates(entry)[0])


class TestSelect(unittest.TestCase):

    def av(self, name, ep="pss", dist="pssparser"):
        return select.Available(name=name, ep_name=ep, dist=dist, version="1", path="/x/" + name)

    def test_matches(self):
        a = self.av("pss-api")
        self.assertTrue(select.matches("pss-api", a))
        self.assertTrue(select.matches("pss", a))            # entry-point name
        self.assertTrue(select.matches("dist:PSSParser", a))  # normalized
        self.assertTrue(select.matches("dist:pss*", a))
        self.assertTrue(select.matches("pss/pss-api", a))
        self.assertTrue(select.matches("*/pss-*", a))
        self.assertFalse(select.matches("other/pss-api", a))
        self.assertTrue(select.matches("mine", a, dest_name="mine"))
        share = select.Available(name="polars", ep_name=None, dist=None, version=None, path="/p")
        self.assertTrue(select.matches("share/polars", share))
        self.assertFalse(select.matches("dist:polars", share))

    def test_resolve_errors(self):
        avail = [self.av("pss-api"), self.av("pss-api", ep="dup", dist="dup")]
        with self.assertRaisesRegex(select.SelectionError, "ambiguous"):
            select.resolve(["pss-api"], avail)
        with self.assertRaisesRegex(select.SelectionError, "matches no"):
            select.resolve(["zzz"], avail)
        # A glob or an entry-point name may pick several, deliberately
        self.assertEqual(2, len(select.resolve(["pss-*"], avail)))
        self.assertEqual(1, len(select.resolve(["dup"], avail)))


class TestState(EngineTestBase):

    def test_round_trip(self):
        st = state.SkillsState(
            agents=["agents", "claude"], mode="copy", with_specs=["pssparser"],
            selections=[state.Selection("ep:pss", "pss-api", "pssparser", as_name="api")],
            installed={"claude_skills": [state.InstalledEntry("api", "copy", "abc")]},
            resolved={"python": "/p", "dists": {"pssparser": "3.1.7"}})
        state.save(self.tmp, st)
        self.assertFalse(os.path.exists(state.state_path(self.tmp) + ".tmp"))
        back = state.load(self.tmp)
        self.assertEqual(st.to_json(), back.to_json())
        self.assertEqual("api", back.selections[0].dest_name)

    def test_bare_string_installed_entries_accepted(self):
        path = state.state_path(self.tmp)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            json.dump({"version": 1, "installed": {"agents_skills": ["x"]}}, fh)
        self.assertEqual({"x"}, state.load(self.tmp).installed_names())

    def test_newer_version_rejected_and_quiet_load(self):
        path = state.state_path(self.tmp)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            json.dump({"version": 99}, fh)
        with self.assertRaises(ValueError):
            state.load(self.tmp)
        self.assertIsNone(state.load_quiet(self.tmp))

    def test_handler_installed(self):
        deps = os.path.join(self.tmp, "packages")
        os.makedirs(deps)
        with open(os.path.join(deps, "ivpm.json"), "w") as fh:
            json.dump({"handlers": {"agents": {"claude_skills": ["a"]}}}, fh)
        self.assertEqual({"claude_skills": ["a"]}, state.handler_installed(deps))
        self.assertEqual({}, state.handler_installed(os.path.join(self.tmp, "none")))


class TestInstallPrimitives(EngineTestBase):

    def test_copy_whole_dir_and_hash(self):
        src = self.skill("src/alpha", "alpha")
        for rel in ("templates/x.in", "deep/er/y.txt", "__pycache__/z.pyc", "m.pyc"):
            os.makedirs(os.path.dirname(os.path.join(src, rel)), exist_ok=True)
            with open(os.path.join(src, rel), "w") as fh:
                fh.write(rel)
        dest = os.path.join(self.tmp, "dest", "alpha")
        install.copy_skill_dir(src, dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, "deep", "er", "y.txt")))
        self.assertFalse(os.path.exists(os.path.join(dest, "__pycache__")))
        self.assertFalse(os.path.exists(os.path.join(dest, "m.pyc")))
        self.assertEqual(install.content_hash(src), install.content_hash(dest))
        with open(os.path.join(src, "SKILL.md"), "a") as fh:
            fh.write("x")
        self.assertNotEqual(install.content_hash(src), install.content_hash(dest))

    def test_build_targets(self):
        t = install.build_targets(self.tmp, ["cursor"])
        self.assertEqual(["agents", "cursor"], [x.agent for x in t])
        self.assertEqual(["agents_skills", "cursor_skills"], [x.state_key for x in t])
        t = install.build_targets(self.tmp, install.AGENTS, plugin_install=False)
        self.assertEqual({"unbundle"}, {x.strategy for x in t})

    def test_remove_managed_honors_keep(self):
        d = os.path.join(self.tmp, ".claude", "skills")
        for n in ("a", "b"):
            os.makedirs(os.path.join(d, n))
        install.remove_managed(self.tmp, {"claude_skills": ["a", {"name": "b"}]},
                               keep={"claude_skills": ["b"]})
        self.assertEqual(["b"], os.listdir(d))


if __name__ == "__main__":
    unittest.main()
