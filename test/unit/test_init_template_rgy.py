"""Init-template registry: sources, precedence, shadowing, trust defaults."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm import site_config
from ivpm.site_config import DefaultSiteConfig
from ivpm.init_template import InitTemplate
from ivpm.init_template.template_rgy import (
    InitTemplateRgy, INIT_TEMPLATES_GROUP, ENV_TEMPLATE_PATH, ENV_DEFAULT_TEMPLATE)

from .test_clone_preparer_rgy import _with_entry_points


class _Dist(object):
    def __init__(self, name, version="1.0"):
        self.name = name
        self.version = version


class _EP(object):
    def __init__(self, name, value, load, dist=None):
        self.name = name
        self.value = value
        self._load = load
        self.dist = dist

    def load(self):
        return self._load()


def mk_template(path, name=None, description=""):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "copier.yml"), "w") as fp:
        meta = {}
        if name:
            meta["name"] = name
        if description:
            meta["description"] = description
        if meta:
            fp.write("_ivpm:\n")
            for k, v in meta.items():
                fp.write("  %s: %s\n" % (k, v))
        fp.write("project_name: x\n")
    return path


class _HookSiteConfig(DefaultSiteConfig):
    items = ()
    default_template = None
    defaults = {}

    def get_init_templates(self):
        if isinstance(self.items, Exception):
            raise self.items
        return self.items

    def get_default_init_template(self):
        return self.default_template


class RgyTestBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._env = mock.patch.dict(os.environ, {
            "IVPM_USER_CONFIG": os.path.join(self.tmp, "user.yaml"),
            "IVPM_SITE_CONFIG": os.path.join(self.tmp, "site.yaml"),
        })
        self._env.start()
        for k in (ENV_TEMPLATE_PATH, ENV_DEFAULT_TEMPLATE):
            os.environ.pop(k, None)
        site_config._config_files = None
        InitTemplateRgy._reset()
        self.site_cfg = _HookSiteConfig()
        self._sc = mock.patch.object(site_config, "get_site_config", lambda: self.site_cfg)
        self._sc.start()
        self.builtin = os.path.join(self.tmp, "builtin")
        self._bi = mock.patch(
            "ivpm.init_template.template_rgy.builtin_templates_dir", lambda: self.builtin)
        self._bi.start()
        self.eps = []

    def tearDown(self):
        self._bi.stop()
        self._sc.stop()
        self._env.stop()
        site_config._config_files = None
        InitTemplateRgy._reset()
        self._tmp.cleanup()

    def write_config(self, which, text):
        with open(os.environ["IVPM_USER_CONFIG" if which == "user" else "IVPM_SITE_CONFIG"],
                  "w") as fp:
            fp.write(textwrap.dedent(text))
        site_config._config_files = None

    def load(self):
        rgy = InitTemplateRgy()
        self.groups = _with_entry_points(self.eps, rgy._load)
        return rgy


class TestSources(RgyTestBase):

    def test_builtin(self):
        mk_template(os.path.join(self.builtin, "default"))
        rgy = self.load()
        t = rgy.get("default")
        self.assertEqual(("built-in", True), (t.origin, t.trusted))
        self.assertEqual([INIT_TEMPLATES_GROUP], self.groups)

    def test_env_dir_collection_and_index(self):
        single = mk_template(os.path.join(self.tmp, "single"))
        coll = os.path.join(self.tmp, "coll")
        mk_template(os.path.join(coll, "c1"))
        mk_template(os.path.join(coll, "c2"))
        local = mk_template(os.path.join(self.tmp, "idx", "local"), description="from idx")
        list_idx = os.path.join(self.tmp, "idx", "list.yaml")
        with open(list_idx, "w") as fp:
            fp.write("- name: remote\n  src: https://example.com/t.git\n  ref: v1\n"
                     "- name: loc\n  src: local\n")
        map_idx = os.path.join(self.tmp, "map.yaml")
        with open(map_idx, "w") as fp:
            fp.write("init-templates:\n  - name: mapped\n  src: gh:org/repo\n"
                     .replace("\n  src", "\n    src"))
        os.environ[ENV_TEMPLATE_PATH] = os.pathsep.join(
            [single, "", coll, list_idx, map_idx])
        rgy = self.load()
        self.assertEqual(["single", "c1", "c2", "remote", "loc", "mapped"], rgy.names())
        for t in rgy.templates():
            self.assertEqual(("env", False), (t.origin, t.trusted), t.name)
        self.assertEqual("v1", rgy.get("remote").ref)
        self.assertEqual(local, rgy.get("loc").src)
        self.assertEqual("from idx", rgy.get("loc").description)
        self.assertEqual([], rgy.warnings())

    def test_env_missing_entry_warns(self):
        os.environ[ENV_TEMPLATE_PATH] = os.path.join(self.tmp, "gone")
        rgy = self.load()
        self.assertEqual([], rgy.names())
        self.assertEqual(1, len(rgy.warnings()))
        self.assertIn("does not exist", rgy.warnings()[0])

    def test_env_relative_entry_uses_cwd(self):
        mk_template(os.path.join(self.tmp, "rel"))
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            os.environ[ENV_TEMPLATE_PATH] = "rel"
            rgy = self.load()
        finally:
            os.chdir(cwd)
        self.assertEqual(["rel"], rgy.names())

    def test_env_trust_true_ignored(self):
        idx = os.path.join(self.tmp, "i.yaml")
        with open(idx, "w") as fp:
            fp.write("- name: r\n  src: gh:a/b\n  trust: true\n")
        os.environ[ENV_TEMPLATE_PATH] = idx
        rgy = self.load()
        self.assertFalse(rgy.get("r").trusted)
        self.assertIn("ignored", rgy.warnings()[0])

    def test_config_files(self):
        user_t = mk_template(os.path.join(self.tmp, "ut"))
        coll = os.path.join(self.tmp, "sc")
        mk_template(os.path.join(coll, "s1"))
        self.write_config("user", """\
            init-templates:
              - name: both
                src: %s
                trust: true
              - name: bad name
                src: /x
              - name: uonly
                src: %s
            """ % (user_t, user_t))
        self.write_config("site", """\
            init-templates:
              - path: %s
                trust: true
              - name: both
                src: gh:site/both
            """ % coll)
        rgy = self.load()
        self.assertEqual(["both", "uonly", "s1"], rgy.names())
        both = rgy.get("both")
        self.assertEqual(("user-config", True), (both.origin, both.trusted))
        self.assertEqual(["site-config-file"], [s.origin for s in both.shadowed])
        self.assertFalse(rgy.get("uonly").trusted)
        self.assertEqual(("site-config-file", True),
                         (rgy.get("s1").origin, rgy.get("s1").trusted))
        self.assertEqual(1, len(rgy.warnings()))
        self.assertIn("init-templates[1]", rgy.warnings()[0])

    def test_config_not_a_list_warns(self):
        self.write_config("user", "init-templates: {name: x}\n")
        rgy = self.load()
        self.assertEqual([], rgy.names())
        self.assertIn("must be a list", rgy.warnings()[0])

    def test_site_config_hook(self):
        coll = os.path.join(self.tmp, "hook")
        mk_template(os.path.join(coll, "h1"))
        self.site_cfg.items = [coll,
                               InitTemplate(name="h2", src="gh:corp/h2"),
                               InitTemplate(name="h3", src="gh:corp/h3", trusted=False)]
        rgy = self.load()
        self.assertEqual(["h1", "h2", "h3"], rgy.names())
        self.assertEqual(["site-config"] * 3, [t.origin for t in rgy.templates()])
        self.assertEqual([True, True, False], [t.trusted for t in rgy.templates()])

    def test_site_config_hook_raises(self):
        self.site_cfg.items = RuntimeError("boom")
        mk_template(os.path.join(self.builtin, "default"))
        rgy = self.load()
        self.assertEqual(["default"], rgy.names())
        self.assertIn("boom", rgy.warnings()[0])

    def test_entry_points(self):
        p1 = mk_template(os.path.join(self.tmp, "ep1"), name="epa")
        p2 = os.path.join(self.tmp, "ep2")
        mk_template(os.path.join(p2, "epb"))

        def _bad():
            raise ImportError("no module acme_bad")
        self.eps = [
            # deliberately out of order: result must not depend on it
            _EP("zz", "zpack:get", lambda: (lambda: [p2]), _Dist("zpack", "2.0")),
            _EP("aa", "apack:get", lambda: (lambda: [p1]), _Dist("apack", "1.5")),
            _EP("dup", "apack:get", lambda: (lambda: [p1]), _Dist("apack", "1.5")),
            _EP("desc", "dpack:T", lambda: [InitTemplate(name="epc", src="gh:d/c")],
                _Dist("dpack")),
            _EP("bad", "bad:get", _bad, _Dist("bpack")),
        ]
        rgy = self.load()
        self.assertEqual(["epa", "epc", "epb"], rgy.names())
        epa = rgy.get("epa")
        self.assertEqual(("apack", "apack", "1.5", True),
                         (epa.origin, epa.provider, epa.version, epa.trusted))
        self.assertEqual("dpack", rgy.get("epc").origin)
        self.assertEqual(1, len(rgy.warnings()))
        self.assertIn("no module acme_bad", rgy.warnings()[0])

    def test_entry_point_instance_not_mutated(self):
        shared = InitTemplate(name="s", src="gh:x/s")
        self.eps = [_EP("s", "spack:T", lambda: [shared], _Dist("spack"))]
        self.load()
        self.load()
        self.assertEqual(("", []), (shared.origin, shared.shadowed))


class TestPrecedence(RgyTestBase):

    def test_same_name_from_all_six(self):
        mk_template(os.path.join(self.builtin, "dup"))
        mk_template(os.path.join(self.tmp, "ep", "dup"))
        self.eps = [_EP("e", "epack:get",
                        lambda: [os.path.join(self.tmp, "ep")], _Dist("epack"))]
        self.site_cfg.items = [InitTemplate(name="dup", src="gh:hook/dup")]
        self.write_config("site", "init-templates:\n  - {name: dup, src: 'gh:site/dup'}\n")
        self.write_config("user", "init-templates:\n  - {name: dup, src: 'gh:user/dup'}\n")
        env_t = mk_template(os.path.join(self.tmp, "envdir", "dup"))
        os.environ[ENV_TEMPLATE_PATH] = env_t
        rgy = self.load()
        t = rgy.get("dup")
        self.assertEqual("env", t.origin)
        self.assertEqual(["user-config", "site-config-file", "site-config", "epack",
                          "built-in"], [s.origin for s in t.shadowed])
        self.assertEqual(["dup"], rgy.names())


class TestLookup(RgyTestBase):

    def test_hidden(self):
        os.makedirs(os.path.join(self.builtin, "h"))
        with open(os.path.join(self.builtin, "h", "copier.yml"), "w") as fp:
            fp.write("_ivpm:\n  hidden: true\n")
        mk_template(os.path.join(self.builtin, "v"))
        rgy = self.load()
        self.assertEqual(["v"], [t.name for t in rgy.templates()])
        self.assertEqual(["h", "v"], [t.name for t in rgy.templates(include_hidden=True)])

    def test_suggest(self):
        for n in ("python-lib", "python-app", "hdl-block"):
            mk_template(os.path.join(self.builtin, n))
        rgy = self.load()
        self.assertIn("python-lib", rgy.suggest("pyhton-lib"))
        self.assertEqual([], rgy.suggest("zzzzzz"))

    def test_default_name_order(self):
        rgy = self.load()
        self.assertEqual("default", rgy.default_name())
        self.site_cfg.default_template = "from-hook"
        self.assertEqual("from-hook", rgy.default_name())
        self.write_config("site", "default-init-template: from-site\n")
        self.assertEqual("from-site", rgy.default_name())
        self.write_config("user", "default-init-template: from-user\n")
        self.assertEqual("from-user", rgy.default_name())
        os.environ[ENV_DEFAULT_TEMPLATE] = "from-env"
        self.assertEqual("from-env", rgy.default_name())


class TestNotBuiltAtStartup(unittest.TestCase):

    def test_help_does_not_scan_init_template_eps(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "src")
        code = textwrap.dedent("""\
            import sys
            import ivpm._compat as compat
            real = compat.entry_points
            groups = []
            def fake(group=None):
                groups.append(group)
                return real(group=group)
            compat.entry_points = fake
            sys.argv = ["ivpm", "--help"]
            from ivpm.__main__ import main
            try:
                main()
            except SystemExit:
                pass
            sys.stderr.write("GROUPS=%r\\n" % groups)
            """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=dict(os.environ, PYTHONPATH=src))
        self.assertIn("GROUPS=", out.stderr)
        self.assertNotIn(INIT_TEMPLATES_GROUP, out.stderr)


if __name__ == "__main__":
    unittest.main()
