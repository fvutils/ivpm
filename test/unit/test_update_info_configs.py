"""The effective project settings reach the *leaf* update-info, not just the
handler one.

An update runs with two ProjectUpdateInfo instances: ``updater.update_info``,
which every leaf callback and source provider receives (as a scope view), and
``handler_update_info``, which the root-phase callbacks receive. The settings
resolved from the project's ``with:`` block previously landed only on the
latter, so anything reading ``handler_configs`` at leaf time silently saw ``{}``.

See pkg-prepare-design.md §6 / pkg-prepare-impl-plan.md P0.
"""
import os

from .test_base import TestBase

from ivpm.handlers import PackageHandler
from ivpm.handlers.package_handler_rgy import PackageHandlerRgy


class _RecordingHandler(PackageHandler):
    """Captures what each callback saw, so the two info objects can be compared."""

    name = "leaf-config-probe"
    description = "test handler: records the settings visible at each callback"

    # Class-level, because the dispatcher instantiates the handler itself.
    leaf_pre = []
    leaf_post = []
    root_post = []

    @classmethod
    def reset(cls):
        cls.leaf_pre = []
        cls.leaf_post = []
        cls.root_post = []

    @staticmethod
    def _snapshot(pkg, update_info):
        return {
            "pkg": pkg.name if pkg is not None else None,
            "scope_key": getattr(pkg, "scope_key", None) if pkg is not None else None,
            "handler_configs": dict(getattr(update_info, "handler_configs", {}) or {}),
            "python_config": getattr(update_info, "python_config", None),
            "node_config": getattr(update_info, "node_config", None),
            "env_settings": list(getattr(update_info, "env_settings", []) or []),
            "root_var": getattr(update_info, "root_var", None),
            "handler_state": dict(getattr(update_info, "handler_state", {}) or {}),
        }

    def on_leaf_pre_load(self, pkg, update_info):
        type(self).leaf_pre.append(self._snapshot(pkg, update_info))

    def on_leaf_post_load(self, pkg, update_info):
        type(self).leaf_post.append(self._snapshot(pkg, update_info))

    def on_root_post_load(self, update_info):
        type(self).root_post.append(self._snapshot(None, update_info))


class _ConfigProbeBase(TestBase):
    """Registers the recording handler for the duration of one test."""

    def setUp(self):
        super().setUp()
        _RecordingHandler.reset()
        rgy = PackageHandlerRgy.inst()
        self._saved_handlers = list(rgy.handlers)
        self._saved_meta = dict(rgy._meta)
        rgy.addHandler(_RecordingHandler, origin="test")

    def tearDown(self):
        rgy = PackageHandlerRgy.inst()
        rgy.handlers = self._saved_handlers
        rgy._meta = self._saved_meta
        _RecordingHandler.reset()
        return super().tearDown()

    def _dep(self, name, data_pkg):
        return ("                - name: %s\n"
                "                  url: file://${DATA_DIR}/%s\n"
                "                  src: dir\n"
                "                  link: false\n" % (name, data_pkg))


class TestLeafSeesEffectiveSettings(_ConfigProbeBase):

    def test_leaf_callbacks_see_package_level_with(self):
        """A package-level 'with:' key is visible to both leaf callbacks."""
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cfg_root\n"
                    "    with:\n"
                    "        leaf-config-probe:\n"
                    "            default-group: eng\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % self._dep("leaf1", "leaf_proj1"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(_RecordingHandler.leaf_pre, "no leaf pre-load callback ran")
        self.assertTrue(_RecordingHandler.leaf_post, "no leaf post-load callback ran")

        for phase, records in (("pre", _RecordingHandler.leaf_pre),
                               ("post", _RecordingHandler.leaf_post)):
            for rec in records:
                self.assertEqual(
                    rec["handler_configs"].get("leaf-config-probe"),
                    {"default-group": "eng"},
                    "leaf %s-load for %s saw %r" % (
                        phase, rec["pkg"], rec["handler_configs"]))

    def test_leaf_and_root_agree(self):
        """The leaf and root callbacks observe the same effective settings."""
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cfg_root\n"
                    "    with:\n"
                    "        leaf-config-probe:\n"
                    "            k: v\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % self._dep("leaf1", "leaf_proj1"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(_RecordingHandler.root_post)
        root = _RecordingHandler.root_post[0]
        leaf = _RecordingHandler.leaf_post[0]

        for key in ("handler_configs", "python_config", "node_config",
                    "env_settings", "root_var", "handler_state"):
            self.assertEqual(leaf[key], root[key],
                             "%s differs between leaf and root" % key)

    def test_depset_with_overlays_package_with_at_leaf(self):
        """The dep-set's own 'with:' wins, and the leaf sees the overlaid result."""
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cfg_root\n"
                    "    with:\n"
                    "        leaf-config-probe:\n"
                    "            group: base\n"
                    "            keep: yes\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          with:\n"
                    "              leaf-config-probe:\n"
                    "                  group: overridden\n"
                    "          deps:\n"
                    "%s" % self._dep("leaf1", "leaf_proj1"))
        self.ivpm_update(skip_venv=True)

        cfg = _RecordingHandler.leaf_pre[0]["handler_configs"]["leaf-config-probe"]
        self.assertEqual(cfg.get("group"), "overridden")

    def test_no_with_block_yields_empty_config(self):
        """Absent 'with:' is an empty dict, not a missing attribute."""
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cfg_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % self._dep("leaf1", "leaf_proj1"))
        self.ivpm_update(skip_venv=True)

        rec = _RecordingHandler.leaf_pre[0]
        self.assertEqual(rec["handler_configs"].get("leaf-config-probe"), None)
        self.assertIsInstance(rec["handler_configs"], dict)


class TestNestedScopeSeesEffectiveSettings(_ConfigProbeBase):
    """A nested-scope package's update_info is a _ScopedUpdateInfo view, which
    delegates attribute reads to the base -- so it must see the settings too."""

    def test_nested_package_sees_with(self):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cfg_root\n"
                    "    with:\n"
                    "        leaf-config-probe:\n"
                    "            default-group: eng\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: nested_toolB\n"
                    "                  url: file://${DATA_DIR}/nested_toolB\n"
                    "                  src: dir\n"
                    "                  link: false\n"
                    "                  deps-mode: nested\n")
        self.ivpm_update(skip_venv=True)

        nested = [r for r in _RecordingHandler.leaf_pre
                  if r["scope_key"] and "/" in r["scope_key"]]
        self.assertTrue(nested,
                        "expected at least one nested-scope package; saw %r"
                        % [r["scope_key"] for r in _RecordingHandler.leaf_pre])
        for rec in nested:
            self.assertEqual(
                rec["handler_configs"].get("leaf-config-probe"),
                {"default-group": "eng"},
                "nested package %s saw %r" % (rec["scope_key"],
                                              rec["handler_configs"]))
