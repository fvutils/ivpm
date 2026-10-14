"""Init templates: rendering through copier (skipped when copier is missing)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

import yaml

from ivpm.init_template import InitTemplate
from ivpm.init_template import copier_runner
from ivpm.init_template.copier_runner import RunOptions, copier_available
from ivpm.init_template.errors import InitError

from .init_template_fixtures import mk_template, git_repo, copier_hidden

FIXTURE = """\
    _ivpm:
      name: fx
      description: test fixture
    _subdirectory: template
    project_name:
      type: str
      help: name
    project_version:
      type: str
      default: "0.0.1"
    """
FILES = {
    "template/out.txt.jinja":
        "n={{ project_name }} v={{ project_version }} iv={{ ivpm.version }}\n",
    "template/{% if extra %}extra.txt{% endif %}.jinja": "extra\n",
    "template/{{_copier_conf.answers_file}}.jinja": "{{ _copier_answers|to_nice_yaml }}",
}


@unittest.skipUnless(copier_available(), "copier is not installed")
class TestCopierRunner(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.src = mk_template(os.path.join(self.tmp, "tmpl"), FIXTURE, FILES)
        self.t = InitTemplate(name="fx", src=self.src, origin="env", trusted=False)
        self.dest = os.path.join(self.tmp, "out")

    def tearDown(self):
        self._tmp.cleanup()

    def run_t(self, data=None, user_defaults=None, t=None, **opts):
        opts.setdefault("defaults", True)
        opts.setdefault("quiet", True)
        copier_runner.run(t or self.t, self.dest, data or {"ivpm": {"version": "9.9"}},
                          user_defaults or {}, RunOptions(**opts))

    def read(self, rel):
        with open(os.path.join(self.dest, rel)) as fp:
            return fp.read()

    def test_render_and_answers(self):
        self.run_t({"project_name": "p", "extra": False, "ivpm": {"version": "9.9"}})
        self.assertEqual("n=p v=0.0.1 iv=9.9\n", self.read("out.txt"))
        self.assertFalse(os.path.exists(os.path.join(self.dest, "extra.txt")))
        answers = yaml.safe_load(self.read(".copier-answers.yml"))
        self.assertEqual({"project_name", "project_version"},
                         {k for k in answers if not k.startswith("_")})

    def test_conditional_file(self):
        self.run_t({"project_name": "p", "extra": True, "ivpm": {"version": "1"}})
        self.assertTrue(os.path.exists(os.path.join(self.dest, "extra.txt")))

    def test_user_defaults_under_defaults(self):
        self.run_t({"extra": False, "ivpm": {"version": "1"}},
                   {"project_name": "from-ud", "project_version": "7.7"})
        self.assertEqual("n=from-ud v=7.7 iv=1\n", self.read("out.txt"))

    def test_missing_required_answer(self):
        with self.assertRaises(InitError) as cm:
            self.run_t({"extra": False, "ivpm": {"version": "1"}})
        self.assertIn("project_name", str(cm.exception))
        self.assertIn("-D project_name=", str(cm.exception))

    def test_pretend_writes_nothing(self):
        self.run_t({"project_name": "p", "extra": False, "ivpm": {"version": "1"}},
                   pretend=True)
        self.assertFalse(os.path.exists(self.dest))

    def test_conflict_under_defaults(self):
        os.makedirs(self.dest)
        with open(os.path.join(self.dest, "out.txt"), "w") as fp:
            fp.write("mine\n")
        with self.assertRaises(InitError) as cm:
            self.run_t({"project_name": "p", "extra": False, "ivpm": {"version": "1"}})
        self.assertIn("--force", str(cm.exception))
        self.assertEqual("mine\n", self.read("out.txt"))
        self.run_t({"project_name": "p", "extra": False, "ivpm": {"version": "1"}},
                   overwrite=True)
        self.assertEqual("n=p v=0.0.1 iv=1\n", self.read("out.txt"))

    def _tasks_template(self):
        src = mk_template(os.path.join(self.tmp, "tasks"),
                          "_tasks:\n  - touch TASKRAN\nproject_name: x\n", {"a.txt": "a\n"})
        return InitTemplate(name="tasks", src=src, origin="env", trusted=False)

    def test_tasks_refused_when_untrusted(self):
        t = self._tasks_template()
        with self.assertRaises(InitError) as cm:
            self.run_t(t=t)
        self.assertIn("not trusted", str(cm.exception))
        self.assertIn("--trust", str(cm.exception))
        self.assertFalse(os.path.exists(self.dest))

    def test_tasks_run_when_trusted(self):
        self.run_t(t=self._tasks_template(), unsafe=True)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "TASKRAN")))

    def test_skip_tasks(self):
        self.run_t(t=self._tasks_template(), unsafe=True, skip_tasks=True)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.dest, "TASKRAN")))

    def test_git_template_with_ref(self):
        repo = mk_template(os.path.join(self.tmp, "repo"), "q: tagged\n", {"f.txt.jinja": "{{ q }}\n"})
        git_repo(repo, tag="v1")
        with open(os.path.join(repo, "copier.yml"), "w") as fp:
            fp.write("q: untagged\n")
        t = InitTemplate(name="r", src="git+file://" + repo, origin="cli", trusted=False)
        self.run_t(t=t, vcs_ref="v1")
        self.assertEqual("tagged\n", self.read("f.txt"))

    def test_unknown_ref(self):
        repo = mk_template(os.path.join(self.tmp, "repo"), "q: 1\n", {"f.jinja": "x"})
        git_repo(repo, tag="v1")
        t = InitTemplate(name="r", src="git+file://" + repo, origin="cli")
        with self.assertRaises(InitError) as cm:
            self.run_t(t=t, vcs_ref="nosuchref")
        self.assertIn("'r'", str(cm.exception))

    def test_missing_local_template(self):
        t = InitTemplate(name="gone", src=os.path.join(self.tmp, "gone"), origin="cli")
        with self.assertRaises(InitError):
            self.run_t(t=t)


class TestCopierMissing(unittest.TestCase):

    def test_install_hint(self):
        with copier_hidden():
            self.assertFalse(copier_available())
            with self.assertRaises(InitError) as cm:
                copier_runner.run(InitTemplate(name="x", src="/x"), "/tmp/none", {}, {},
                                  RunOptions())
        self.assertIn("ivpm[init]", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
