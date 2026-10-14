"""`ivpm init`: the CLI, template classification, and legacy compatibility."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm import site_config
from ivpm.cmds.cmd_init import classify_template_arg
from ivpm.init_template import InitTemplate
from ivpm.init_template.copier_runner import copier_available
from ivpm.init_template.errors import InitError
from ivpm.init_template.template_rgy import (
    InitTemplateRgy, ENV_TEMPLATE_PATH, ENV_DEFAULT_TEMPLATE)
from ivpm.internal_error import EXIT_USER_ERROR

from .init_template_fixtures import golden, mk_template, copier_hidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "src")

FIXTURE = """\
    _ivpm:
      name: fx
      description: test fixture
      next_steps: fixture next steps
    _subdirectory: template
    project_name: {type: str}
    flavor: {type: str, default: plain}
    """
FIXTURE_FILES = {"template/out.txt.jinja": "{{ project_name }}:{{ flavor }}\n"}


class InitCmdBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = os.path.realpath(self._tmp.name)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self._env = mock.patch.dict(os.environ, {
            "IVPM_USER_CONFIG": os.path.join(self.tmp, "user.yaml"),
            "IVPM_SITE_CONFIG": os.path.join(self.tmp, "site.yaml"),
        })
        self._env.start()
        for k in (ENV_TEMPLATE_PATH, ENV_DEFAULT_TEMPLATE):
            os.environ.pop(k, None)
        site_config._config_files = None
        InitTemplateRgy._reset()
        self._cwd = os.getcwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self._cwd)
        self._env.stop()
        site_config._config_files = None
        InitTemplateRgy._reset()
        self._tmp.cleanup()

    def ivpm(self, *argv):
        """Run `ivpm <argv>` in-process; return (exit code, stdout, stderr)."""
        from ivpm.__main__ import main
        InitTemplateRgy._reset()
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["ivpm"] + list(argv)), \
                mock.patch.object(sys, "stdin", io.StringIO("")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        return code, out.getvalue(), err.getvalue()

    def read(self, *rel):
        with open(os.path.join(self.work, *rel)) as fp:
            return fp.read()

    def fixture(self):
        return mk_template(os.path.join(self.tmp, "fx"), FIXTURE, FIXTURE_FILES)


class TestLegacy(InitCmdBase):

    def _legacy(self):
        code, out, err = self.ivpm("init", "my-project")
        self.assertEqual(0, code, err)
        self.assertEqual(golden("my-project"), self.read("ivpm.yaml"))
        self.assertEqual(["ivpm.yaml"], os.listdir(self.work))

        code, _, err = self.ivpm("init", "my-project", "-v", "1.0.0")
        self.assertEqual(EXIT_USER_ERROR, code)
        self.assertIn("exists and --force not specified", err)
        self.assertNotIn("Traceback", err)

        code, _, err = self.ivpm("init", "other", "-v", "1.0.0", "-f")
        self.assertEqual(0, code, err)
        self.assertEqual(golden("other"), self.read("ivpm.yaml"))
        return out

    @unittest.skipUnless(copier_available(), "copier is not installed")
    def test_legacy_with_copier(self):
        out = self._legacy()
        self.assertIn("ivpm update", out)

    def test_legacy_without_copier(self):
        with copier_hidden():
            out = self._legacy()
        self.assertIn("ivpm update", out)

    def test_name_defaults_to_dir(self):
        with copier_hidden():
            code, _, err = self.ivpm("init")
        self.assertEqual(0, code, err)
        self.assertEqual(golden("work"), self.read("ivpm.yaml"))

    def test_project_dir_created(self):
        with copier_hidden():
            code, _, err = self.ivpm("init", "-p", "newdir", "n")
        self.assertEqual(0, code, err)
        self.assertEqual(golden("n"), self.read("newdir", "ivpm.yaml"))

    def test_pretend_creates_nothing(self):
        for hide in (True, False):
            if not hide and not copier_available():
                continue
            ctx = copier_hidden() if hide else contextlib.nullcontext()
            with ctx:
                code, _, err = self.ivpm("init", "-p", "newdir", "n", "--pretend")
            self.assertEqual(0, code, err)
            self.assertFalse(os.path.exists(os.path.join(self.work, "newdir")))

    def test_never_prompts_from_a_subprocess(self):
        # stdin is /dev/null: the legacy form must complete without a terminal.
        env = dict(os.environ, PYTHONPATH=SRC)
        r = subprocess.run([sys.executable, "-m", "ivpm", "init", "sub-proj"],
                           cwd=self.work, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(golden("sub-proj"), self.read("ivpm.yaml"))


class TestTemplates(InitCmdBase):

    def test_other_template_needs_copier(self):
        os.environ[ENV_TEMPLATE_PATH] = self.fixture()
        with copier_hidden():
            code, _, err = self.ivpm("init", "-t", "fx", "p")
        self.assertEqual(EXIT_USER_ERROR, code)
        self.assertIn("needs copier", err)
        self.assertIn("ivpm[init]", err)
        self.assertNotIn("Traceback", err)

    @unittest.skipUnless(copier_available(), "copier is not installed")
    def test_named_template_and_d_before_and_after(self):
        os.environ[ENV_TEMPLATE_PATH] = self.fixture()
        code, out, err = self.ivpm("-Dflavor=spicy", "init", "-t", "fx", "p")
        self.assertEqual(0, code, err)
        self.assertEqual("p:spicy\n", self.read("out.txt"))
        self.assertIn("fixture next steps", out)
        code, _, err = self.ivpm("init", "-t", "fx", "q", "-D", "flavor=mild", "-f")
        self.assertEqual(0, code, err)
        self.assertEqual("q:mild\n", self.read("out.txt"))

    @unittest.skipUnless(copier_available(), "copier is not installed")
    def test_env_default_template(self):
        os.environ[ENV_TEMPLATE_PATH] = self.fixture()
        os.environ[ENV_DEFAULT_TEMPLATE] = "fx"
        code, _, err = self.ivpm("init", "p")
        self.assertEqual(0, code, err)
        self.assertEqual("p:plain\n", self.read("out.txt"))

    @unittest.skipUnless(copier_available(), "copier is not installed")
    def test_path_template_untrusted_unless_trust(self):
        src = mk_template(os.path.join(self.tmp, "tasks"),
                          "_tasks:\n  - touch TASKRAN\nproject_name: x\n", {"a.txt": "a\n"})
        code, _, err = self.ivpm("init", "-t", src, "p")
        self.assertEqual(EXIT_USER_ERROR, code)
        self.assertIn("not trusted", err)
        self.assertFalse(os.path.exists(os.path.join(self.work, "a.txt")))
        code, _, err = self.ivpm("init", "-t", src, "p", "--trust")
        self.assertEqual(0, code, err)
        self.assertTrue(os.path.exists(os.path.join(self.work, "TASKRAN")))

    def test_unknown_template(self):
        code, _, err = self.ivpm("init", "-t", "defualt")
        self.assertEqual(EXIT_USER_ERROR, code)
        self.assertIn("Unknown template 'defualt'", err)
        self.assertIn("did you mean: default", err)
        self.assertIn("ivpm init --list", err)

    def test_bad_definition(self):
        code, _, err = self.ivpm("init", "p", "-D", "oops")
        self.assertEqual(EXIT_USER_ERROR, code)
        self.assertIn("-D oops", err)

    def test_list(self):
        code, out, err = self.ivpm("init", "--list")
        self.assertEqual(0, code, err)
        self.assertIn("default", out)
        code2, out2, _ = self.ivpm("show", "init-templates", "--no-rich")
        self.assertEqual(out, out2)

    def test_help_lists_templates_and_survives_broken_ep(self):
        code, out, _ = self.ivpm("init", "--help")
        self.assertEqual(0, code)
        self.assertIn("Available templates:", out)
        self.assertIn("default", out)

        class _BadEP(object):
            name, value, dist = "bad", "bad:x", None

            def load(self):
                raise ImportError("broken pack")
        import ivpm._compat as compat
        real = compat.entry_points

        def fake(group=None):
            if group == "ivpm.init_templates":
                return [_BadEP()]
            return real(group=group)
        with mock.patch.object(compat, "entry_points", fake):
            code, out, _ = self.ivpm("init", "--help")
        self.assertEqual(0, code)
        self.assertIn("default", out)


class TestClassify(InitCmdBase):

    def setUp(self):
        super().setUp()
        self.rgy = InitTemplateRgy()
        self.rgy.register(InitTemplate(name="default", src="/builtin/default",
                                       origin="built-in", trusted=True))
        self.rgy.register(InitTemplate(name="python-lib", src="/x/python-lib",
                                       origin="acme", trusted=True))

    def test_explicit_paths(self):
        src = mk_template(os.path.join(self.work, "local"), "_ivpm: {name: loc}\n")
        for arg in ("./local", src):
            t = classify_template_arg(arg, self.rgy)
            self.assertEqual(("loc", src, "cli", False), (t.name, t.src, t.origin, t.trusted))

    def test_tilde_path(self):
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            mk_template(os.path.join(self.tmp, "ht"))
            self.assertEqual(os.path.join(self.tmp, "ht"),
                             classify_template_arg("~/ht", self.rgy).src)

    def test_missing_path(self):
        with self.assertRaises(InitError):
            classify_template_arg("./nope", self.rgy)
        os.makedirs(os.path.join(self.work, "plain"))
        with self.assertRaises(InitError) as cm:
            classify_template_arg("./plain", self.rgy)
        self.assertIn("not a copier template", str(cm.exception))

    def test_bare_template_dir_wins_over_registry_name(self):
        mk_template(os.path.join(self.work, "default"))
        t = classify_template_arg("default", self.rgy)
        self.assertEqual("cli", t.origin)

    def test_bare_plain_dir_does_not_win(self):
        os.makedirs(os.path.join(self.work, "default"))
        self.assertEqual("built-in", classify_template_arg("default", self.rgy).origin)

    def test_urls(self):
        for arg in ("gh:org/repo", "gl:org/repo", "https://x.com/t", "git@host:x.git",
                    "foo.git", "git+file:///srv/t"):
            t = classify_template_arg(arg, self.rgy)
            self.assertEqual((arg, "cli", False), (t.src, t.origin, t.trusted), arg)

    def test_registry_name(self):
        self.assertEqual("acme", classify_template_arg("python-lib", self.rgy).origin)

    def test_unknown_suggests(self):
        with self.assertRaises(InitError) as cm:
            classify_template_arg("python-lb", self.rgy)
        self.assertIn("python-lib", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
