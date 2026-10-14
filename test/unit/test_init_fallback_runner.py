"""Init templates: the copier-less renderer for the built-in 'default'."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.init_template import InitTemplate
from ivpm.init_template import fallback_runner as fb
from ivpm.init_template.copier_runner import RunOptions
from ivpm.init_template.errors import InitError
from ivpm.init_template.template_rgy import builtin_templates_dir

from .init_template_fixtures import golden, copier_hidden


def _default():
    return InitTemplate(name="default", origin="built-in", trusted=True,
                        src=os.path.join(builtin_templates_dir(), "default"))


class TestFallbackRunner(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def read(self):
        with open(os.path.join(self.dest, "ivpm.yaml")) as fp:
            return fp.read()

    def test_golden(self):
        with copier_hidden():
            fb.run(_default(), self.dest, {"project_name": "my-project",
                                           "project_version": "0.1.0"}, {}, RunOptions())
        self.assertEqual(golden("my-project"), self.read())
        self.assertEqual(["ivpm.yaml"], os.listdir(self.dest))

    def test_name_from_user_defaults(self):
        fb.run(_default(), self.dest, {}, {"project_name": "derived"}, RunOptions())
        self.assertEqual(golden("derived"), self.read())

    def test_supports_only_builtin_default(self):
        self.assertTrue(fb.supports(_default()))
        shadow = InitTemplate(name="default", src="/site/default", origin="site-config")
        self.assertFalse(fb.supports(shadow))
        self.assertFalse(fb.supports(InitTemplate(name="other", src="/x",
                                                  origin="built-in")))

    def test_existing_without_overwrite(self):
        with open(os.path.join(self.dest, "ivpm.yaml"), "w") as fp:
            fp.write("mine\n")
        with self.assertRaises(InitError) as cm:
            fb.run(_default(), self.dest, {"project_name": "p"}, {}, RunOptions())
        self.assertIn("--force", str(cm.exception))
        self.assertEqual("mine\n", self.read())
        fb.run(_default(), self.dest, {"project_name": "p"}, {}, RunOptions(overwrite=True))
        self.assertEqual(golden("p"), self.read())

    def test_pretend(self):
        dest = os.path.join(self.dest, "new")
        fb.run(_default(), dest, {"project_name": "p"}, {}, RunOptions(pretend=True))
        self.assertFalse(os.path.exists(dest))

    def test_render_rejects_other_jinja(self):
        self.assertEqual("a b", fb.render("{{ project_name }} {{project_version}}",
                                          {"project_name": "a", "project_version": "b"}))
        for bad in ("{{ other }}", "{% if x %}", "{# c #}", "{{ project_name|upper }}"):
            with self.assertRaises(RuntimeError, msg=bad):
                fb.render(bad, {"project_name": "a", "project_version": "b"})


if __name__ == "__main__":
    unittest.main()
