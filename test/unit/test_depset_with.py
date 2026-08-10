"""Tests for dep-set-specific ``with:`` handler controls.

Covers parsing/inheritance of a per-dep-set ``with:`` block, the effective
config computed when a dep-set is the selected install target (package-level
``with:`` overlaid by the dep-set's own), multi-dep-set merge, and located
errors on bad dep-set ``with:`` keys.
"""
import io
import unittest

from ivpm.ivpm_yaml_reader import (
    IvpmYamlReader, resolve_effective_with, merge_with, parse_with_section)
from ivpm.proj_info import VenvMode
from ivpm.project_ops import ProjectOps


def _parse(yaml_text, name="test.yaml"):
    return IvpmYamlReader().read(io.StringIO(yaml_text), name)


def _assert_fatal(test_case, yaml_text, *fragments):
    with test_case.assertRaises(Exception) as ctx:
        _parse(yaml_text)
    msg = str(ctx.exception)
    for frag in fragments:
        test_case.assertIn(frag, msg,
            "Expected %r in error message: %r" % (frag, msg))


class TestDepSetWithParsing(unittest.TestCase):
    """Parse-time behavior: raw storage, inheritance, located errors."""

    def test_depset_with_stored_raw(self):
        info = _parse("""
package:
  name: mypkg
  dep-sets:
    - name: default
      deps: []
    - name: ci
      with:
        python: { venv: false }
      deps: []
""")
        self.assertIsNone(info.get_dep_set("default").with_raw)
        self.assertEqual(
            info.get_dep_set("ci").with_raw,
            {"python": {"venv": False}})

    def test_depset_with_inherits_and_overrides_base(self):
        """A dep-set inherits its base's ``with:`` (own keys win)."""
        info = _parse("""
package:
  name: mypkg
  dep-sets:
    - name: base
      with:
        python: { venv: uv, system-site-packages: true }
      deps: []
    - name: child
      uses: base
      with:
        python: { venv: false }
        node: { manager: pnpm }
      deps: []
""")
        # Base is unchanged.
        self.assertEqual(
            info.get_dep_set("base").with_raw,
            {"python": {"venv": "uv", "system-site-packages": True}})
        # Child: own venv wins, base system-site-packages inherited, own node added.
        self.assertEqual(
            info.get_dep_set("child").with_raw,
            {"python": {"venv": False, "system-site-packages": True},
             "node": {"manager": "pnpm"}})

    def test_regression_package_only_no_depset_with(self):
        """A manifest with only package-level ``with:`` leaves dep-sets bare."""
        info = _parse("""
package:
  name: mypkg
  with:
    python: { venv: uv }
  dep-sets:
    - name: default
      deps: []
""")
        self.assertEqual(info.with_raw, {"python": {"venv": "uv"}})
        self.assertIsNone(info.get_dep_set("default").with_raw)
        self.assertEqual(info.python_config.venv, VenvMode.UV)

    def test_bad_depset_with_key_located_error(self):
        _assert_fatal(self,
            """
package:
  name: mypkg
  dep-sets:
    - name: ci
      with:
        python: { enable-venv: true }
      deps: []
""",
            "enable-venv",
            "dep-set 'ci'.with.python",
        )

    def test_bad_depset_with_handler_located_error(self):
        _assert_fatal(self,
            """
package:
  name: mypkg
  dep-sets:
    - name: ci
      with:
        ruby: {}
      deps: []
""",
            "ruby",
            "dep-set 'ci'.with",
        )


class TestEffectiveWith(unittest.TestCase):
    """resolve_effective_with(): the config a selected dep-set actually gets."""

    def _effective(self, yaml_text, dep_set):
        info = _parse(yaml_text)
        ds = info.get_dep_set(dep_set)
        return resolve_effective_with(info, ds)

    def test_no_depset_with_uses_package(self):
        """A dep-set with no ``with:`` gets the package-level config verbatim."""
        py, node, handlers, _env = self._effective("""
package:
  name: mypkg
  with:
    python: { venv: uv, system-site-packages: true }
  dep-sets:
    - name: default
      deps: []
""", "default")
        self.assertEqual(py.venv, VenvMode.UV)
        self.assertTrue(py.system_site_packages)

    def test_depset_venv_overrides_package_uv_to_skip(self):
        py, _, _, _ = self._effective("""
package:
  name: mypkg
  with:
    python: { venv: uv }
  dep-sets:
    - name: default
      deps: []
    - name: ci
      with:
        python: { venv: false }
      deps: []
""", "ci")
        self.assertEqual(py.venv, VenvMode.SKIP)

    def test_depset_venv_overrides_package_skip_to_uv(self):
        py, _, _, _ = self._effective("""
package:
  name: mypkg
  with:
    python: { venv: false }
  dep-sets:
    - name: dev
      with:
        python: { venv: uv }
      deps: []
""", "dev")
        self.assertEqual(py.venv, VenvMode.UV)

    def test_partial_override_merges_with_package(self):
        """Dep-set sets only venv; system-site-packages falls back to package."""
        py, _, _, _ = self._effective("""
package:
  name: mypkg
  with:
    python: { venv: uv, system-site-packages: true }
  dep-sets:
    - name: ci
      with:
        python: { venv: pip }
      deps: []
""", "ci")
        self.assertEqual(py.venv, VenvMode.PIP)
        self.assertTrue(py.system_site_packages)  # inherited from package level

    def test_node_manager_override(self):
        _, node, _, _ = self._effective("""
package:
  name: mypkg
  with:
    node: { manager: npm }
  dep-sets:
    - name: dev
      with:
        node: { manager: pnpm }
      deps: []
""", "dev")
        self.assertEqual(node.manager, "pnpm")

    def test_plugin_handler_config_override(self):
        """A dep-set can override a plugin handler's config block."""
        py, node, handlers, _env = self._effective("""
package:
  name: mypkg
  with:
    direnv: { a: 1, b: 2 }
  dep-sets:
    - name: ci
      with:
        direnv: { b: 99 }
      deps: []
""", "ci")
        # merge_with is deep: b overridden, a inherited from package level.
        self.assertEqual(handlers["direnv"], {"a": 1, "b": 99})

    def test_clone_overlay_handler_preserved(self):
        """A handler config present only via a clone overlay (not in with_raw)
        survives when the dep-set overrides an unrelated setting."""
        info = _parse("""
package:
  name: mypkg
  with:
    python: { venv: uv }
  dep-sets:
    - name: ci
      with:
        python: { venv: false }
      deps: []
""")
        # Simulate a clone-provided handler overlay merged onto the parsed
        # package-level handler_configs (project_ops does this before resolve).
        info.handler_configs["agents"] = {"skill-path": "x"}
        py, node, handlers, _env = resolve_effective_with(
            info, info.get_dep_set("ci"))
        self.assertEqual(py.venv, VenvMode.SKIP)
        self.assertEqual(handlers.get("agents"), {"skill-path": "x"})


class TestMultiDepSetMerge(unittest.TestCase):
    """_getDepSets merges the with_raw of several selected dep-sets."""

    def test_multi_depset_with_later_wins(self):
        info = _parse("""
package:
  name: mypkg
  dep-sets:
    - name: a
      with:
        python: { venv: uv, system-site-packages: true }
      deps: []
    - name: b
      with:
        python: { venv: false }
      deps: []
""")
        names, merged = ProjectOps(root_dir=".")._getDepSets(info, ["a", "b"])
        self.assertEqual(names, ["a", "b"])
        # Later set (b) wins on venv; a's system-site-packages survives.
        self.assertEqual(
            merged.with_raw,
            {"python": {"venv": False, "system-site-packages": True}})
        # And the resolved effective config reflects the merge.
        py, _, _, _ = resolve_effective_with(info, merged)
        self.assertEqual(py.venv, VenvMode.SKIP)
        self.assertTrue(py.system_site_packages)


class TestMergeWithHelper(unittest.TestCase):
    """merge_with(): winner direction and non-mutation of shared nodes."""

    def test_over_wins_and_base_untouched(self):
        base = {"python": {"venv": "uv", "system-site-packages": True}}
        over = {"python": {"venv": "false"}, "node": {"manager": "yarn"}}
        result = merge_with(base, over)
        self.assertEqual(result, {
            "python": {"venv": "false", "system-site-packages": True},
            "node": {"manager": "yarn"}})
        # Inputs are not mutated.
        self.assertEqual(base, {"python": {"venv": "uv",
                                           "system-site-packages": True}})
        self.assertEqual(over, {"python": {"venv": "false"},
                                "node": {"manager": "yarn"}})

    def test_empty_operands(self):
        self.assertEqual(merge_with({}, {"a": 1}), {"a": 1})
        self.assertEqual(merge_with({"a": 1}, {}), {"a": 1})
        self.assertEqual(merge_with(None, None), {})


if __name__ == "__main__":
    unittest.main()
