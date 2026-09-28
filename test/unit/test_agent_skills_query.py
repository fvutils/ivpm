"""Querying an environment for agent skills (ivpm.agent_skills.query).

The query script runs under the *target* interpreter, so these tests build a
real venv and install distributions into it (see skills_fixtures).
"""
import os
import shutil
import tempfile
import unittest

from ivpm.agent_skills import query
from ivpm.agent_skills import select

from . import skills_fixtures as fx


# The fixture venv must see only what the test installs in it
CLEAN_ENV = {k: v for k, v in os.environ.items()
             if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}


def run(py):
    return query.query_env(py, env=CLEAN_ENV)


class QueryTestBase(unittest.TestCase):

    python = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-skills-query-")
        self.py, self.site = fx.make_venv(os.path.join(self.tmp, "venv"), self.python)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestQuery(QueryTestBase):

    def test_multi_dir_entry_point(self):
        files, eps = fx.skill_package("pss", ["pss", "pss-api", "pss-checkers"])
        fx.add_dist(self.site, "pss-parser", "3.1", files, eps)
        r = run(self.py)
        self.assertEqual([], r.errors)
        (ep,) = r.skill_entries
        self.assertEqual(("pss", "pss-parser", "3.1"), (ep.name, ep.dist, ep.version))
        self.assertEqual(["pss", "pss-api", "pss-checkers"],
                         [os.path.basename(d) for d in ep.dirs])
        avail = select.available_from_query(r)
        self.assertEqual(["pss", "pss-api", "pss-checkers"], [a.name for a in avail])
        self.assertTrue(all(a.provider == "ep:pss" for a in avail))

    def test_misbehaving_entry_points_are_isolated(self):
        """A class, an exception, a bad item and stdout noise each affect only
        their own entry point."""
        good_files, good_eps = fx.skill_package("good", ["good"])
        fx.add_dist(self.site, "good", "1.0", good_files, good_eps)
        fx.add_dist(self.site, "bad", "1.0", files={
            "bad/__init__.py": "print('import-time noise')\n",
            "bad/eps.py": (
                "import sys\n"
                "print('noise <<IVPM_SKILLS_JSON>>')\n"
                "class ImageGenSkill: pass\n"
                "def raises(): raise RuntimeError('boom')\n"
                "def bad_item(): return ['/tmp', 42]\n"
                "def exits(): sys.exit(3)\n"),
        }, entry_points={"agent.skills": {
            "klass": "bad.eps:ImageGenSkill",
            "raises": "bad.eps:raises",
            "baditem": "bad.eps:bad_item",
            "exits": "bad.eps:exits",
            "missing": "bad.nosuchmod:fn",
        }})
        r = run(self.py)
        self.assertEqual(["good"], [e.name for e in r.skill_entries])
        errors = {e.name: e.error for e in r.errors}
        self.assertEqual({"klass", "raises", "baditem", "exits", "missing"}, set(errors))
        self.assertIn("ImageGenSkill", errors["klass"])
        self.assertIn("boom", errors["raises"])
        self.assertIn("int", errors["baditem"])
        self.assertTrue(all(e.dist == "bad" for e in r.errors))
        self.assertIn("klass (agent.skills, from bad)", str(r.errors[0]) + str(r.errors[1:]))

    def test_legacy_group_deduplicated(self):
        files, _ = fx.skill_package("dual", ["dual"])
        fx.add_dist(self.site, "dual", "1.0", files, {
            "agent.skills": {"dual": "dual.skills:get_skill_dirs"},
            "ivpm.skill": {"dual": "dual.skills:get_skill_dirs"}})
        r = run(self.py)
        self.assertEqual([("agent.skills", "dual")], [(e.group, e.name) for e in r.entries])

    def test_share_agent_skills_attributed_by_record(self):
        fx.add_dist(self.site, "agent-skill-polars", "1.38.0", data={
            "share/agent-skills/polars/SKILL.md": fx.skill_md("polars")})
        # A directory nobody's RECORD claims
        prefix = os.path.join(self.tmp, "venv")
        orphan = os.path.join(prefix, "share", "agent-skills", "orphan")
        os.makedirs(orphan)
        with open(os.path.join(orphan, "SKILL.md"), "w") as fh:
            fh.write(fx.skill_md("orphan"))
        r = run(self.py)
        by_name = {os.path.basename(s.dir): s for s in r.share}
        self.assertEqual(("agent-skill-polars", "1.38.0"),
                         (by_name["polars"].dist, by_name["polars"].version))
        self.assertIsNone(by_name["orphan"].dist)
        avail = {a.name: a for a in select.available_from_query(r)}
        self.assertEqual("share", avail["polars"].provider)

    def test_plugins_group(self):
        fx.add_dist(self.site, "plug", "1.0", files={
            "plug/__init__.py": "",
            "plug/p.py": "import os\ndef root():\n"
                         "    return os.path.join(os.path.dirname(__file__), 'plugin')\n"},
            entry_points={"agent.plugins": {"plug": "plug.p:root"}})
        r = run(self.py)
        self.assertEqual(["plug"], [e.name for e in r.plugin_entries])
        self.assertEqual([], r.skill_entries)

    def test_unrunnable_interpreter_raises(self):
        with self.assertRaises(query.QueryError):
            run(os.path.join(self.tmp, "no-such-python"))


class TestIvpmOwnEntryPoint(QueryTestBase):
    """IVPM's own 'agent.skills' entry point must load in an environment
    that has IVPM's source but none of its dependencies: loading
    ivpm.skills runs ivpm/__init__.py, which must stay import-light."""

    def test_ivpm_skills_imports_without_dependencies(self):
        import subprocess
        src = os.path.dirname(os.path.dirname(os.path.abspath(query.__file__)))
        src = os.path.dirname(src)
        env = dict(CLEAN_ENV, PYTHONPATH=src)
        r = subprocess.run(
            [self.py, "-c",
             "import sys, ivpm.skills; ivpm.skills.get_skill_dirs(); "
             "print(sorted(m for m in sys.modules if m.split('.')[0] in "
             "('ivpm', 'yaml', 'rich', 'httpx')))"],
            capture_output=True, text=True, env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual("['ivpm', 'ivpm.skills']", r.stdout.strip())


_PY39 = shutil.which("python3.9")


@unittest.skipUnless(_PY39, "python3.9 not available")
class TestQueryPython39(QueryTestBase):
    """3.9's entry_points() takes no arguments and EntryPoint has no .dist."""

    python = _PY39

    def test_multi_dir_entry_point_and_dist(self):
        files, eps = fx.skill_package("pss", ["pss", "pss-api"])
        fx.add_dist(self.site, "pss-parser", "3.1", files, eps)
        r = run(self.py)
        self.assertTrue(r.version.startswith("3.9."))
        (ep,) = r.skill_entries
        self.assertEqual(("pss-parser", "3.1"), (ep.dist, ep.version))
        self.assertEqual(2, len(ep.dirs))


if __name__ == "__main__":
    unittest.main()
