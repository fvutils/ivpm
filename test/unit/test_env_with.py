"""Tests for ``env:`` as a standard ``with:`` clause.

Covers parsing ``with.env`` at the package and dep-set level, the additive
(never-replacing) merge of environment directives across ``uses:`` inheritance
/ multi-dep-set selection / the package-to-dep-set overlay, the deprecated
top-level ``env:`` spelling and its warning, and end-to-end emission into
``packages.envrc``.
"""
import io
import os
import shutil
import tempfile
import unittest

from ivpm import msg
from ivpm.diagnostics import CollectingSink, Severity
from ivpm.env_spec import EnvSpec
from ivpm.ivpm_yaml_reader import (
    IvpmYamlReader, merge_with, parse_with_section, resolve_effective_with)
from ivpm.project_ops import ProjectOps

from .test_base import TestBase


def _parse(yaml_text, name="test.yaml", is_root=False):
    return IvpmYamlReader().read(io.StringIO(yaml_text), name, is_root=is_root)


def _assert_fatal(test_case, yaml_text, *fragments):
    with test_case.assertRaises(Exception) as ctx:
        _parse(yaml_text)
    msg_s = str(ctx.exception)
    for frag in fragments:
        test_case.assertIn(frag, msg_s,
            "Expected %r in error message: %r" % (frag, msg_s))


def _vars(env_settings):
    """(var, action, value) triples -- compact assertion form."""
    return [(e.var, e.act, e.val) for e in env_settings]


class TestEnvWithParsing(unittest.TestCase):
    """Parse-time behavior of the ``with.env`` clause."""

    def test_package_with_env_parsed(self):
        info = _parse("""
package:
  name: mypkg
  with:
    env:
      - { name: MYPROJ_HOME, value: "${IVPM_PROJECT}" }
      - { name: MYPROJ_PATHS, path: ["a", "b"] }
      - { name: PATH, path-prepend: "bin" }
      - { name: LD_LIBRARY_PATH, path-append: "lib" }
  dep-sets:
    - name: default
      deps: []
""")
        self.assertEqual(_vars(info.env_settings), [
            ("MYPROJ_HOME", EnvSpec.Act.Set, "${IVPM_PROJECT}"),
            ("MYPROJ_PATHS", EnvSpec.Act.Path, ["a", "b"]),
            ("PATH", EnvSpec.Act.PathPrepend, "bin"),
            ("LD_LIBRARY_PATH", EnvSpec.Act.PathAppend, "lib"),
        ])

    def test_env_not_passed_to_handler_configs(self):
        """'env' is core-parsed, not dispatched to a handler."""
        info = _parse("""
package:
  name: mypkg
  with:
    env:
      - { name: A, value: 1 }
  dep-sets:
    - name: default
      deps: []
""")
        self.assertNotIn("env", info.handler_configs)

    def test_env_must_be_a_list(self):
        _assert_fatal(self, """
package:
  name: mypkg
  with:
    env:
      name: A
  dep-sets:
    - name: default
      deps: []
""", "package.with.env", "must be a list")

    def test_env_entry_requires_name(self):
        _assert_fatal(self, """
package:
  name: mypkg
  with:
    env:
      - { value: 1 }
  dep-sets:
    - name: default
      deps: []
""", "No variable-name specified")

    def test_env_entry_rejects_multiple_actions(self):
        _assert_fatal(self, """
package:
  name: mypkg
  with:
    env:
      - { name: A, value: 1, path: b }
  dep-sets:
    - name: default
      deps: []
""", "Multiple variable-setting directives")

    def test_env_entry_requires_an_action(self):
        _assert_fatal(self, """
package:
  name: mypkg
  with:
    env:
      - { name: A }
  dep-sets:
    - name: default
      deps: []
""", "No variable-directive setting")

    def test_env_listed_among_valid_with_keys(self):
        _assert_fatal(self, """
package:
  name: mypkg
  with:
    bogus: {}
  dep-sets:
    - name: default
      deps: []
""", "Unknown key 'bogus'", "env")

    def test_depset_env_stored_raw(self):
        info = _parse("""
package:
  name: mypkg
  dep-sets:
    - name: ci
      with:
        env:
          - { name: MYPROJ_MODE, value: ci }
      deps: []
""")
        ds = info.get_dep_set("ci")
        self.assertEqual(ds.with_raw["env"],
                         [{"name": "MYPROJ_MODE", "value": "ci"}])
        # Package level is untouched by a dep-set declaration.
        self.assertEqual(info.env_settings, [])

    def test_depset_env_validated_at_parse_time(self):
        _assert_fatal(self, """
package:
  name: mypkg
  dep-sets:
    - name: ci
      with:
        env:
          - { name: A, value: 1, path: b }
      deps: []
""", "Multiple variable-setting directives")

    def test_node_env_still_boolean(self):
        """with.node.env (a bool) does not collide with with.env."""
        info = _parse("""
package:
  name: mypkg
  with:
    node: { env: false }
    env:
      - { name: A, value: 1 }
  dep-sets:
    - name: default
      deps: []
""")
        self.assertIs(info.node_config.env, False)
        self.assertEqual(_vars(info.env_settings),
                         [("A", EnvSpec.Act.Set, 1)])


class TestEnvMerge(unittest.TestCase):
    """env is additive: concatenated base-first, never replaced."""

    def _effective_env(self, yaml_text, dep_set):
        info = _parse(yaml_text)
        _, _, _, env = resolve_effective_with(info, info.get_dep_set(dep_set))
        return env

    def test_package_and_depset_concatenate(self):
        env = self._effective_env("""
package:
  name: mypkg
  with:
    env:
      - { name: PKG, value: 1 }
  dep-sets:
    - name: ci
      with:
        env:
          - { name: CI, value: 1 }
      deps: []
""", "ci")
        self.assertEqual([e.var for e in env], ["PKG", "CI"])

    def test_same_var_depset_last(self):
        """Both directives survive; the dep-set's is emitted last (wins)."""
        env = self._effective_env("""
package:
  name: mypkg
  with:
    env:
      - { name: MODE, value: dev }
  dep-sets:
    - name: ci
      with:
        env:
          - { name: MODE, value: ci }
      deps: []
""", "ci")
        self.assertEqual(_vars(env), [
            ("MODE", EnvSpec.Act.Set, "dev"),
            ("MODE", EnvSpec.Act.Set, "ci"),
        ])

    def test_path_prepend_accumulates(self):
        env = self._effective_env("""
package:
  name: mypkg
  with:
    env:
      - { name: PATH, path-prepend: pkg/bin }
  dep-sets:
    - name: ci
      with:
        env:
          - { name: PATH, path-prepend: ci/bin }
      deps: []
""", "ci")
        self.assertEqual([e.val for e in env], ["pkg/bin", "ci/bin"])

    def test_uses_inheritance_concatenates(self):
        env = self._effective_env("""
package:
  name: mypkg
  dep-sets:
    - name: base
      with:
        env:
          - { name: BASE, value: 1 }
      deps: []
    - name: child
      uses: base
      with:
        env:
          - { name: CHILD, value: 1 }
      deps: []
""", "child")
        self.assertEqual([e.var for e in env], ["BASE", "CHILD"])

    def test_multi_depset_selection_concatenates(self):
        info = _parse("""
package:
  name: mypkg
  dep-sets:
    - name: a
      with:
        env:
          - { name: A, value: 1 }
      deps: []
    - name: b
      with:
        env:
          - { name: B, value: 1 }
      deps: []
""")
        _, merged = ProjectOps(root_dir=".")._getDepSets(info, ["a", "b"])
        _, _, _, env = resolve_effective_with(info, merged)
        self.assertEqual([e.var for e in env], ["A", "B"])

    def test_depset_env_with_no_package_env(self):
        env = self._effective_env("""
package:
  name: mypkg
  with:
    python: { venv: uv }
  dep-sets:
    - name: ci
      with:
        env:
          - { name: CI, value: 1 }
      deps: []
""", "ci")
        self.assertEqual([e.var for e in env], ["CI"])

    def test_package_env_with_no_depset_with(self):
        env = self._effective_env("""
package:
  name: mypkg
  with:
    env:
      - { name: PKG, value: 1 }
  dep-sets:
    - name: ci
      deps: []
""", "ci")
        self.assertEqual([e.var for e in env], ["PKG"])

    def test_depset_cannot_clear_inherited_env(self):
        """An empty dep-set env: adds nothing -- it does not reset."""
        env = self._effective_env("""
package:
  name: mypkg
  with:
    env:
      - { name: PKG, value: 1 }
  dep-sets:
    - name: ci
      with:
        env: []
      deps: []
""", "ci")
        self.assertEqual([e.var for e in env], ["PKG"])


class TestMergeWithEnvRule(unittest.TestCase):
    """merge_with()'s top-level env concatenation and its depth guard."""

    def test_top_level_env_concatenates(self):
        base = {"env": [{"name": "A"}]}
        over = {"env": [{"name": "B"}]}
        self.assertEqual(merge_with(base, over)["env"],
                         [{"name": "A"}, {"name": "B"}])

    def test_nested_env_still_replaces(self):
        """with.node.env is a bool -- the depth guard must not concatenate."""
        merged = merge_with({"node": {"env": True}}, {"node": {"env": False}})
        self.assertEqual(merged, {"node": {"env": False}})

    def test_nested_env_list_still_replaces(self):
        """A hypothetical plugin handler with a list-valued 'env' key."""
        merged = merge_with({"plug": {"env": ["a"]}}, {"plug": {"env": ["b"]}})
        self.assertEqual(merged["plug"]["env"], ["b"])

    def test_inputs_not_mutated(self):
        base = {"env": [{"name": "A"}]}
        over = {"env": [{"name": "B"}]}
        merge_with(base, over)
        self.assertEqual(base["env"], [{"name": "A"}])
        self.assertEqual(over["env"], [{"name": "B"}])

    def test_env_only_on_one_side(self):
        self.assertEqual(merge_with({"env": [1]}, {"python": {}})["env"], [1])
        self.assertEqual(merge_with({"python": {}}, {"env": [1]})["env"], [1])

    def test_non_list_env_falls_back_to_replace(self):
        """Malformed input must not crash the merge; parse reports the error."""
        self.assertEqual(merge_with({"env": [1]}, {"env": "bogus"})["env"],
                         "bogus")


class TestTopLevelEnvAlias(unittest.TestCase):
    """Top-level env: is folded into with.env and behaves identically."""

    _TOP_LEVEL = """
package:
  name: mypkg
  env:
    - { name: A, value: 1 }
    - { name: PATH, path-prepend: bin }
  dep-sets:
    - name: default
      deps: []
"""

    _WITH_ENV = """
package:
  name: mypkg
  with:
    env:
      - { name: A, value: 1 }
      - { name: PATH, path-prepend: bin }
  dep-sets:
    - name: default
      deps: []
"""

    def test_parity_with_with_env(self):
        self.assertEqual(_vars(_parse(self._TOP_LEVEL).env_settings),
                         _vars(_parse(self._WITH_ENV).env_settings))

    def test_folded_into_with_raw(self):
        info = _parse(self._TOP_LEVEL)
        self.assertIn("env", info.with_raw)
        self.assertEqual(len(info.with_raw["env"]), 2)

    def test_both_spellings_top_level_first(self):
        info = _parse("""
package:
  name: mypkg
  env:
    - { name: TOP, value: 1 }
  with:
    env:
      - { name: NESTED, value: 1 }
  dep-sets:
    - name: default
      deps: []
""")
        self.assertEqual([e.var for e in info.env_settings], ["TOP", "NESTED"])

    def test_top_level_env_then_depset_env(self):
        info = _parse("""
package:
  name: mypkg
  env:
    - { name: TOP, value: 1 }
  dep-sets:
    - name: ci
      with:
        env:
          - { name: CI, value: 1 }
      deps: []
""")
        _, _, _, env = resolve_effective_with(info, info.get_dep_set("ci"))
        self.assertEqual([e.var for e in env], ["TOP", "CI"])

    def test_top_level_env_preserves_other_with_keys(self):
        info = _parse("""
package:
  name: mypkg
  with:
    python: { venv: uv }
  env:
    - { name: A, value: 1 }
  dep-sets:
    - name: default
      deps: []
""")
        self.assertEqual(info.python_config.venv.value, "uv")
        self.assertEqual([e.var for e in info.env_settings], ["A"])

    def test_top_level_env_must_be_a_list(self):
        _assert_fatal(self, """
package:
  name: mypkg
  env:
    name: A
  dep-sets:
    - name: default
      deps: []
""", "'env' must be a list")


class TestEnvDeprecationWarning(unittest.TestCase):
    """Top-level env: warns -- once, root manifests only."""

    def setUp(self):
        self.sink = CollectingSink()
        self.prev = msg.use_sink(self.sink)

    def tearDown(self):
        msg.use_sink(self.prev)

    def _warnings(self):
        return [d for d in self.sink.records
                if d.severity is Severity.WARNING
                and "top-level 'env:'" in d.message]

    _MULTI = """
package:
  name: mypkg
  env:
    - { name: A, value: 1 }
    - { name: B, value: 2 }
    - { name: C, value: 3 }
  dep-sets:
    - name: default
      deps: []
"""

    def test_root_manifest_warns_once(self):
        _parse(self._MULTI, is_root=True)
        warns = self._warnings()
        self.assertEqual(len(warns), 1, self.sink.messages())
        self.assertIn("with: { env: [...] }", warns[0].message)

    def test_dependency_manifest_does_not_warn(self):
        _parse(self._MULTI)          # is_root defaults to False
        self.assertEqual(self._warnings(), [])

    def test_with_env_does_not_warn(self):
        _parse("""
package:
  name: mypkg
  with:
    env:
      - { name: A, value: 1 }
  dep-sets:
    - name: default
      deps: []
""", is_root=True)
        self.assertEqual(self._warnings(), [])

    def test_warning_carries_a_location(self):
        """The warning points at the offending block, not just the file."""
        _parse(self._MULTI, is_root=True)
        diag = self._warnings()[0]
        self.assertIsNotNone(diag.srcinfo)
        self.assertIn("test.yaml", diag.format())

    def test_warning_does_not_suppress_the_fold(self):
        info = _parse(self._MULTI, is_root=True)
        self.assertEqual([e.var for e in info.env_settings], ["A", "B", "C"])


class TestEnvIncludeMerge(unittest.TestCase):
    """Both spellings merge additively across include: -- an include's env is
    never discarded. Uses real files because include: resolves on disk."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ivpm-env-with-")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _read(self, root_text, incl_text):
        for nm, txt in (("ivpm.yaml", root_text), ("common.yaml", incl_text)):
            with open(os.path.join(self._dir, nm), "w") as fp:
                fp.write(txt)
        path = os.path.join(self._dir, "ivpm.yaml")
        with open(path) as fp:
            return IvpmYamlReader().read(fp, path)

    def test_top_level_env_appends_across_include(self):
        proj = self._read(
            "package:\n"
            "  name: demo\n"
            "  include: [common.yaml]\n"
            "  env:\n"
            "    - { name: LOCAL, value: 1 }\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps: []\n",
            "package:\n"
            "  env:\n"
            "    - { name: INCL, value: 1 }\n")
        self.assertEqual([e.var for e in proj.env_settings],
                         ["LOCAL", "INCL"])

    def test_with_env_appends_across_include(self):
        """Same result as the top-level spelling -- 'with' otherwise
        local-wins on lists, which would have dropped the include's env."""
        proj = self._read(
            "package:\n"
            "  name: demo\n"
            "  include: [common.yaml]\n"
            "  with:\n"
            "    env:\n"
            "      - { name: LOCAL, value: 1 }\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps: []\n",
            "package:\n"
            "  with:\n"
            "    env:\n"
            "      - { name: INCL, value: 1 }\n")
        self.assertEqual([e.var for e in proj.env_settings],
                         ["LOCAL", "INCL"])

    def test_with_env_adopted_when_absent_locally(self):
        proj = self._read(
            "package:\n"
            "  name: demo\n"
            "  include: [common.yaml]\n"
            "  with:\n"
            "    python: { venv: uv }\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps: []\n",
            "package:\n"
            "  with:\n"
            "    env:\n"
            "      - { name: INCL, value: 1 }\n")
        self.assertEqual([e.var for e in proj.env_settings], ["INCL"])

    def test_with_env_no_duplication_when_include_has_none(self):
        proj = self._read(
            "package:\n"
            "  name: demo\n"
            "  include: [common.yaml]\n"
            "  with:\n"
            "    env:\n"
            "      - { name: LOCAL, value: 1 }\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps: []\n",
            "package:\n"
            "  with:\n"
            "    python: { venv: uv }\n")
        self.assertEqual([e.var for e in proj.env_settings], ["LOCAL"])

    def test_nested_node_env_still_local_wins_across_include(self):
        proj = self._read(
            "package:\n"
            "  name: demo\n"
            "  include: [common.yaml]\n"
            "  with:\n"
            "    node: { env: false }\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps: []\n",
            "package:\n"
            "  with:\n"
            "    node: { env: true }\n")
        self.assertIs(proj.node_config.env, False)


class TestEnvEmission(TestBase):
    """End-to-end: effective env reaches packages.envrc."""

    def _envrc(self):
        path = os.path.join(self.testdir, "packages", "packages.envrc")
        self.assertTrue(os.path.isfile(path), "packages.envrc not generated")
        with open(path) as fp:
            return fp.read()

    def test_package_with_env_emitted(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_env_with_pkg
            with:
                env:
                    - { name: MYVAR, value: hello }
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.ivpm_update(skip_venv=True)
        content = self._envrc()
        self.assertIn("# --- ivpm:env (project) ---", content)
        self.assertIn('export MYVAR="hello"', content)

    def test_depset_env_emitted_after_package_env(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_env_with_depset
            with:
                env:
                    - { name: MODE, value: base }
            dep-sets:
                - name: default-dev
                  with:
                    env:
                        - { name: MODE, value: ci }
                  deps: []
        """)
        self.ivpm_update(skip_venv=True)
        content = self._envrc()
        self.assertLess(content.index('export MODE="base"'),
                        content.index('export MODE="ci"'),
                        "dep-set env must be emitted after package env")

    # Two dep-sets, one manifest. Each is exercised by its own test method:
    # switching the selected dep-set within a single workspace is rejected by
    # 'ivpm update' (a separate, deliberate guard), and TestBase gives each
    # test a fresh rundir.
    _SELECT_MANIFEST = """
        package:
            name: test_env_with_select
            dep-sets:
                - name: default-dev
                  with:
                    env:
                        - { name: WHICH, value: dev }
                  deps: []
                - name: ci
                  with:
                    env:
                        - { name: WHICH, value: ci }
                  deps: []
        """

    def test_selected_depset_env_default(self):
        self.mkFile("ivpm.yaml", self._SELECT_MANIFEST)
        self.ivpm_update(skip_venv=True)
        content = self._envrc()
        self.assertIn('export WHICH="dev"', content)
        self.assertNotIn('export WHICH="ci"', content)

    def test_selected_depset_env_alternate(self):
        self.mkFile("ivpm.yaml", self._SELECT_MANIFEST)
        self.ivpm_update(dep_set="ci", skip_venv=True)
        content = self._envrc()
        self.assertIn('export WHICH="ci"', content)
        self.assertNotIn('export WHICH="dev"', content)

    def test_top_level_env_parity(self):
        """The deprecated spelling emits exactly what with.env emits."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_env_with_parity
            env:
                - { name: MYVAR, value: hello }
                - { name: PATH, path-prepend: bin }
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.ivpm_update(skip_venv=True)
        top_level = self._envrc()

        self.mkFile("ivpm.yaml", """
        package:
            name: test_env_with_parity
            with:
                env:
                    - { name: MYVAR, value: hello }
                    - { name: PATH, path-prepend: bin }
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.ivpm_update(skip_venv=True)
        self.assertEqual(top_level, self._envrc())

    def test_package_envrc_sourced_before_env_block(self):
        """source_env lines precede the project's own directives."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_env_with_order
            with:
                env:
                    - { name: MYVAR, value: hello }
            dep-sets:
                - name: default-dev
                  deps:
                    - name: envrc_leaf1
                      url: file://${DATA_DIR}/envrc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)
        content = self._envrc()
        self.assertLess(content.index("source_env ./envrc_leaf1/export.envrc"),
                        content.index('export MYVAR="hello"'))


if __name__ == "__main__":
    unittest.main()
