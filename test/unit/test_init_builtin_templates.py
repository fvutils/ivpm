"""Init templates: the templates IVPM ships, and that they ship."""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.init_template import InitTemplate
from ivpm.init_template import template_dir, fallback_runner, copier_runner
from ivpm.init_template.copier_runner import RunOptions, copier_available
from ivpm.init_template.template_rgy import builtin_templates_dir

from .init_template_fixtures import golden

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBuiltinTemplates(unittest.TestCase):

    def test_every_dir_is_a_described_template(self):
        root = builtin_templates_dir()
        names = sorted(os.listdir(root))
        self.assertIn("default", names)
        for name in names:
            path = os.path.join(root, name)
            self.assertTrue(template_dir.is_template_dir(path), name)
            meta, warnings = template_dir.read_template_meta(path)
            self.assertEqual([], warnings, name)
            self.assertEqual(name, meta.get("name"), name)
            self.assertTrue(meta.get("description"), name)

    def test_default_stays_within_fallback_subset(self):
        root = os.path.join(builtin_templates_dir(), "default", "template")
        files = []
        for dirpath, _dirs, fns in os.walk(root):
            files.extend(os.path.relpath(os.path.join(dirpath, f), root) for f in fns)
        self.assertEqual(["ivpm.yaml.jinja"], files)
        with open(os.path.join(root, "ivpm.yaml.jinja")) as fp:
            text = fp.read()
        for expr in re.findall(r"\{\{(.*?)\}\}", text):
            self.assertIn(expr.strip(), fallback_runner.VARIABLES)
        # Raises on anything the fallback cannot render.
        fallback_runner.render(text, {"project_name": "a", "project_version": "b"})

    def test_default_declares_only_cli_answered_questions(self):
        # The legacy 'ivpm init NAME [-v VER]' must never prompt.
        qs = template_dir.read_questions(os.path.join(builtin_templates_dir(), "default"))
        self.assertEqual(["project_name", "project_version"], [q.name for q in qs])

    @unittest.skipUnless(copier_available(), "copier is not installed")
    def test_copier_and_fallback_agree(self):
        t = InitTemplate(name="default", origin="built-in", trusted=True,
                         src=os.path.join(builtin_templates_dir(), "default"))
        data = {"project_name": "my-project", "project_version": "0.1.0",
                "ivpm": {"version": "x"}}
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            copier_runner.run(t, a, data, {}, RunOptions(defaults=True, quiet=True))
            fallback_runner.run(t, b, data, {}, RunOptions())
            self.assertEqual(["ivpm.yaml"], os.listdir(a))
            with open(os.path.join(a, "ivpm.yaml")) as fa, \
                    open(os.path.join(b, "ivpm.yaml")) as fb:
                self.assertEqual(fa.read(), fb.read())
            with open(os.path.join(a, "ivpm.yaml")) as fa:
                self.assertEqual(golden("my-project"), fa.read())


class TestWheelContents(unittest.TestCase):
    """Package-data globs must pick up the whole template tree (incl. dotfiles)."""

    def test_wheel_ships_template_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fn in ("pyproject.toml", "LICENSE", "README.md"):
                shutil.copy(os.path.join(REPO, fn), tmp)
            shutil.copytree(os.path.join(REPO, "src", "ivpm"), os.path.join(tmp, "src", "ivpm"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            # A dotfile, as real templates have (.gitignore.jinja), must ship too.
            dot = os.path.join(tmp, "src", "ivpm", "templates", "init", "default",
                               "template", ".probe.jinja")
            open(dot, "w").close()
            r = subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "-q", "--no-deps",
                 "--no-build-isolation", ".", "-w", "dist"],
                cwd=tmp, capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("cannot build a wheel here: %s" % r.stderr.strip()[-300:])
            whl = glob.glob(os.path.join(tmp, "dist", "*.whl"))[0]
            with zipfile.ZipFile(whl) as z:
                shipped = set(z.namelist())
            src_root = os.path.join(tmp, "src")
            for dirpath, _dirs, fns in os.walk(os.path.join(src_root, "ivpm", "templates",
                                                            "init")):
                for f in fns:
                    rel = os.path.relpath(os.path.join(dirpath, f), src_root)
                    self.assertIn(rel.replace(os.sep, "/"), shipped)
            self.assertIn("ivpm/templates/init/default/template/.probe.jinja", shipped)


if __name__ == "__main__":
    unittest.main()
