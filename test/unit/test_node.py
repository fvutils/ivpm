"""
Unit tests for Node.js support in IVPM.

Covers:
  N01-N03  PackageNpm source type parsing
  N04      PackagePackageJson source type parsing
  N05-N06  package.json dep harvesting and collision resolution
  N07-N08  type: node content type parsing
  N09-N11  package.with.node config parsing
  N12-N13  Generated packages/node/package.json content
  N14-N15  packages.envrc patching (idempotency)
  N16-N17  .nvmrc file creation
  N18      Handler skips when no node packages
  N19-N20  npm install / npm link subprocess calls (mocked)
  N21      get_state_entries() structure
  N22      Auto-detection of source packages with package.json
  N23-N24  Hash-based install skip (sync-like idempotency)
"""

import dataclasses as dc
import json
import os
import sys
import textwrap
import unittest
from unittest.mock import MagicMock, patch, call

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.pkg_types.package_npm import PackageNpm
from ivpm.pkg_types.package_packagejson import PackagePackageJson
from ivpm.pkg_content_type import NodeTypeData, NodeContentType, parse_type_field
from ivpm.pkg_content_type_rgy import PkgContentTypeRgy
from ivpm.proj_info import NodeConfig, ProjInfo
from ivpm.handlers.package_handler_node import (
    PackageHandlerNode, _patch_packages_envrc_node, _write_node_envrc,
    _NODE_SENTINEL_BEGIN, _NODE_SENTINEL_END,
)
from ivpm.project_ops_info import ProjectUpdateInfo

from .test_base import TestBase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update_info(testdir, node_config=None, handler_state=None):
    """Build a minimal ProjectUpdateInfo for handler tests."""
    deps_dir = os.path.join(testdir, "packages")
    os.makedirs(deps_dir, exist_ok=True)
    args = MagicMock()
    args.suppress_output = True
    ui = ProjectUpdateInfo(
        args=args,
        deps_dir=deps_dir,
        project_dir=testdir,
        suppress_output=True,
    )
    ui.node_config = node_config
    ui.handler_state = handler_state or {}
    return ui


def _make_npm_pkg(name, version="*", dev=False, optional=False):
    pkg = PackageNpm(name)
    pkg.src_type = "npm"
    pkg.version = version
    pkg.dev = dev
    pkg.optional = optional
    return pkg


# ---------------------------------------------------------------------------
# N01-N03 — PackageNpm source type
# ---------------------------------------------------------------------------

class TestPackageNpm(unittest.TestCase):

    def test_N01_npm_package_parsed(self):
        """N01: PackageNpm created correctly from opts with src: npm, version, dev."""
        opts = {"src": "npm", "version": "^5.4.0", "dev": False}
        pkg = PackageNpm.create("typescript", opts, None)
        self.assertIsInstance(pkg, PackageNpm)
        self.assertEqual(pkg.name, "typescript")
        self.assertEqual(pkg.src_type, "npm")
        self.assertEqual(pkg.version, "^5.4.0")
        self.assertFalse(pkg.dev)
        self.assertFalse(pkg.optional)

    def test_N02_npm_package_dev_flag(self):
        """N02: dev: true sets PackageNpm.dev = True."""
        opts = {"src": "npm", "version": "^29.0.0", "dev": True}
        pkg = PackageNpm.create("jest", opts, None)
        self.assertTrue(pkg.dev)

    def test_N03_npm_package_optional_flag(self):
        """N03: optional: true sets PackageNpm.optional = True."""
        opts = {"src": "npm", "optional": True}
        pkg = PackageNpm.create("fsevents", opts, None)
        self.assertTrue(pkg.optional)
        self.assertFalse(pkg.dev)

    def test_npm_version_default(self):
        """Default version is '*' when not specified."""
        pkg = PackageNpm.create("chalk", {}, None)
        self.assertEqual(pkg.version, "*")

    def test_npm_source_info(self):
        """source_info() returns a PkgSourceInfo with name 'npm'."""
        info = PackageNpm.source_info()
        self.assertEqual(info.name, "npm")


# ---------------------------------------------------------------------------
# N04 — PackagePackageJson source type
# ---------------------------------------------------------------------------

class TestPackagePackageJson(unittest.TestCase):

    def test_N04_packagejson_source_parsed(self):
        """N04: src: package.json + url: creates PackagePackageJson."""
        opts = {"src": "package.json", "url": "file:///some/path/package.json"}
        pkg = PackagePackageJson.create("webapp_deps", opts, None)
        self.assertIsInstance(pkg, PackagePackageJson)
        self.assertEqual(pkg.src_type, "package.json")
        self.assertEqual(pkg.url, "file:///some/path/package.json")

    def test_packagejson_source_info(self):
        info = PackagePackageJson.source_info()
        self.assertEqual(info.name, "package.json")


# ---------------------------------------------------------------------------
# N05-N06 — package.json harvesting and collision resolution
# ---------------------------------------------------------------------------

class TestPackageJsonHarvesting(TestBase):

    def _make_handler_with_leaf(self, pkg):
        """Feed a package through on_leaf_post_load and return the handler."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(pkg, ui)
        return handler

    def test_N05_packagejson_harvests_deps(self):
        """N05: Handler reads node_leaf1/package.json and produces correct PackageNpm list."""
        fixture_path = os.path.join(self.data_dir, "node_leaf1", "package.json")
        pkg = PackagePackageJson.create("leaf1_deps", {
            "src": "package.json",
            "url": "file://" + fixture_path,
        }, None)

        handler = self._make_handler_with_leaf(pkg)

        self.assertIn("lodash", handler._npm_pkgs)
        self.assertEqual(handler._npm_pkgs["lodash"].version, "^4.17.21")
        self.assertFalse(handler._npm_pkgs["lodash"].dev)

        self.assertIn("jest", handler._npm_pkgs)
        self.assertTrue(handler._npm_pkgs["jest"].dev)

    def test_N06_explicit_entry_wins_collision(self):
        """N06: Explicit src: npm entry overrides same-name dep from src: package.json."""
        fixture_path = os.path.join(self.data_dir, "node_leaf1", "package.json")

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)

        # Add explicit jest entry first
        explicit_jest = PackageNpm.create("jest", {"src": "npm", "version": "^28.0.0"}, None)
        handler.on_leaf_post_load(explicit_jest, ui)

        # Now feed package.json that also has jest
        pj_pkg = PackagePackageJson.create("leaf1_deps", {
            "src": "package.json",
            "url": "file://" + fixture_path,
        }, None)
        handler.on_leaf_post_load(pj_pkg, ui)

        # Explicit entry (^28.0.0) must win over fixture's ^29.0.0
        self.assertEqual(handler._npm_pkgs["jest"].version, "^28.0.0")


# ---------------------------------------------------------------------------
# N07-N08 — type: node content type
# ---------------------------------------------------------------------------

class TestNodeContentType(unittest.TestCase):

    def setUp(self):
        # Reset singleton so NodeContentType registration is picked up freshly
        PkgContentTypeRgy._inst = None

    def test_N07_node_content_type_parsed(self):
        """N07: type: node creates NodeTypeData(link=True) by default."""
        ct = NodeContentType()
        data = ct.create_data({}, si=None)
        self.assertIsInstance(data, NodeTypeData)
        self.assertFalse(data.dev)
        self.assertTrue(data.link)
        self.assertEqual(data.type_name, "node")

    def test_N08_node_content_type_dev_link(self):
        """N08: type: {node: {dev: true, link: false}} sets both fields."""
        ct = NodeContentType()
        data = ct.create_data({"dev": True, "link": False}, si=None)
        self.assertTrue(data.dev)
        self.assertFalse(data.link)

    def test_node_content_type_registered(self):
        """NodeContentType is registered in the registry."""
        rgy = PkgContentTypeRgy.inst()
        self.assertTrue(rgy.has("node"))

    def test_node_content_type_schema(self):
        """get_json_schema() returns valid schema with dev and link properties."""
        ct = NodeContentType()
        schema = ct.get_json_schema()
        self.assertIn("dev", schema["properties"])
        self.assertIn("link", schema["properties"])
        self.assertEqual(schema["properties"]["dev"]["type"], "boolean")
        self.assertEqual(schema["properties"]["link"]["type"], "boolean")

    def tearDown(self):
        PkgContentTypeRgy._inst = None


# ---------------------------------------------------------------------------
# N09-N11 — package.with.node config parsing
# ---------------------------------------------------------------------------

class TestNodeConfig(TestBase):

    def _read_proj(self, yaml_text):
        """Helper: write ivpm.yaml and parse it."""
        self.mkFile("ivpm.yaml", textwrap.dedent(yaml_text))
        return ProjInfo.mkFromProj(self.testdir)

    def test_N09_node_config_parsed(self):
        """N09: package.with.node with all fields → correct NodeConfig."""
        proj = self._read_proj("""
            package:
                name: test_node_config
                with:
                    node:
                        manager: yarn
                        version: "20"
                        env: false
        """)
        self.assertIsNotNone(proj.node_config)
        self.assertEqual(proj.node_config.manager, "yarn")
        self.assertEqual(proj.node_config.version, "20")
        self.assertFalse(proj.node_config.env)

    def test_N10_node_config_defaults(self):
        """N10: package.with.node: {} → NodeConfig with all defaults."""
        proj = self._read_proj("""
            package:
                name: test_node_defaults
                with:
                    node: {}
        """)
        self.assertIsNotNone(proj.node_config)
        self.assertEqual(proj.node_config.manager, "npm")
        self.assertIsNone(proj.node_config.version)
        self.assertTrue(proj.node_config.env)

    def test_N11_unknown_with_node_key_fatal(self):
        """N11: package.with.node: {unknown_key: foo} → fatal() is called."""
        with self.assertRaises((SystemExit, Exception)):
            self._read_proj("""
                package:
                    name: test_bad_node_key
                    with:
                        node:
                            unknown_key: foo
            """)


# ---------------------------------------------------------------------------
# N12-N13 — Generated packages/node/package.json
# ---------------------------------------------------------------------------

class TestGeneratedPackageJson(TestBase):

    def _make_handler_with_pkgs(self, pkgs):
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        for pkg in pkgs:
            handler.on_leaf_post_load(pkg, ui)
        return handler, ui

    def test_N12_generated_packagejson_content(self):
        """N12: Handler generates correct packages/node/package.json from collected deps."""
        pkgs = [
            _make_npm_pkg("typescript", "^5.4.0"),
            _make_npm_pkg("webpack", "^5.91.0"),
        ]
        handler, ui = self._make_handler_with_pkgs(pkgs)
        pkg_json_path = os.path.join(ui.deps_dir, "node", "package.json")

        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        self.assertTrue(os.path.isfile(pkg_json_path))
        with open(pkg_json_path) as f:
            data = json.load(f)

        self.assertEqual(data["name"], "ivpm-node-env")
        self.assertTrue(data.get("private"))
        self.assertIn("typescript", data.get("dependencies", {}))
        self.assertIn("webpack", data.get("dependencies", {}))

    def test_N13_generated_packagejson_dev_separation(self):
        """N13: Dev deps land in devDependencies, normal in dependencies."""
        pkgs = [
            _make_npm_pkg("typescript", "^5.4.0", dev=False),
            _make_npm_pkg("jest", "^29.0.0", dev=True),
        ]
        handler, ui = self._make_handler_with_pkgs(pkgs)
        pkg_json_path = os.path.join(ui.deps_dir, "node", "package.json")

        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        with open(pkg_json_path) as f:
            data = json.load(f)

        self.assertIn("typescript", data.get("dependencies", {}))
        self.assertNotIn("typescript", data.get("devDependencies", {}))
        self.assertIn("jest", data.get("devDependencies", {}))
        self.assertNotIn("jest", data.get("dependencies", {}))


# ---------------------------------------------------------------------------
# N14-N15 — packages.envrc patching
# ---------------------------------------------------------------------------

class TestPackagesEnvrcPatching(TestBase):

    def test_N14_packages_envrc_patched(self):
        """N14: After handler runs, packages.envrc contains node sentinel section."""
        pkgs = [_make_npm_pkg("lodash", "^4.0.0")]
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        for p in pkgs:
            handler.on_leaf_post_load(p, ui)

        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        envrc_path = os.path.join(ui.deps_dir, "packages.envrc")
        self.assertTrue(os.path.isfile(envrc_path))
        content = open(envrc_path).read()
        self.assertIn(_NODE_SENTINEL_BEGIN, content)
        self.assertIn(_NODE_SENTINEL_END, content)
        self.assertIn("source_env ./node/export.envrc", content)

    def test_N15_packages_envrc_idempotent(self):
        """N15: Running handler twice does not duplicate the sentinel section."""
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)

        # Patch twice
        _patch_packages_envrc_node(deps_dir)
        _patch_packages_envrc_node(deps_dir)

        content = open(os.path.join(deps_dir, "packages.envrc")).read()
        self.assertEqual(content.count(_NODE_SENTINEL_BEGIN), 1)
        self.assertEqual(content.count(_NODE_SENTINEL_END), 1)


# ---------------------------------------------------------------------------
# N16-N17 — .nvmrc
# ---------------------------------------------------------------------------

class TestNvmrc(TestBase):

    def test_N16_nvmrc_written(self):
        """N16: node_config.version = '20' → packages/node/.nvmrc contains '20'."""
        nc = NodeConfig(version="20")
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir, node_config=nc)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        nvmrc = os.path.join(ui.deps_dir, "node", ".nvmrc")
        self.assertTrue(os.path.isfile(nvmrc))
        self.assertEqual(open(nvmrc).read().strip(), "20")

    def test_N17_nvmrc_not_written_when_absent(self):
        """N17: No version: → no .nvmrc file."""
        nc = NodeConfig()   # version=None
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir, node_config=nc)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        nvmrc = os.path.join(ui.deps_dir, "node", ".nvmrc")
        self.assertFalse(os.path.isfile(nvmrc))


# ---------------------------------------------------------------------------
# N18 — Handler skips with no node packages
# ---------------------------------------------------------------------------

class TestHandlerSkip(TestBase):

    def test_N18_handler_skips_with_no_node_pkgs(self):
        """N18: Project with no node packages and no node_config → no packages/node/."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir, node_config=None)
        handler.on_root_pre_load(ui)
        # No leaf calls — no node packages
        handler.on_root_post_load(ui)

        node_dir = os.path.join(ui.deps_dir, "node")
        self.assertFalse(os.path.isdir(node_dir))


# ---------------------------------------------------------------------------
# N19-N20 — subprocess mocking (npm install / npm link)
# ---------------------------------------------------------------------------

class TestSubprocessCalls(TestBase):

    def test_N19_npm_install_called(self):
        """N19: Verify subprocess.run is invoked with npm install --prefix."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            handler.on_root_post_load(ui)

        node_dir = os.path.join(ui.deps_dir, "node")
        called_cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
        install_calls = [c for c in called_cmds if "install" in c]
        self.assertTrue(any("npm" in c and "--prefix" in c and node_dir in c
                            for c in install_calls),
                        "Expected npm install --prefix %s in calls: %s" % (node_dir, called_cmds))

    def test_N20_npm_link_called_for_source_pkg(self):
        """N20: Verify npm link is invoked for type: node source package."""
        # Create a fake source package with a path
        from ivpm.package import Package
        src_pkg = Package("my_ts_lib")
        src_pkg.src_type = "git"
        src_pkg.pkg_type = None
        src_pkg.path = os.path.join(self.testdir, "packages", "my_ts_lib")
        os.makedirs(src_pkg.path, exist_ok=True)

        from ivpm.pkg_content_type import NodeTypeData
        from ivpm.package import get_type_data
        # Attach NodeTypeData directly via the proper type_data list
        nd = NodeTypeData(dev=False, link=True)
        nd.type_name = "node"
        src_pkg.type_data.append(nd)

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            handler.on_root_post_load(ui)

        called_cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
        link_calls = [c for c in called_cmds if "link" in c]
        self.assertTrue(any("npm" in c and src_pkg.path in c
                            for c in link_calls),
                        "Expected npm link %s in calls: %s" % (src_pkg.path, called_cmds))


# ---------------------------------------------------------------------------
# N21 — State entries
# ---------------------------------------------------------------------------

class TestHandlerState(TestBase):

    def test_N21_handler_state_persisted(self):
        """N21: get_state_entries() returns expected dict structure."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)
        handler.on_leaf_post_load(_make_npm_pkg("jest", dev=True), ui)

        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        state = handler.get_state_entries()
        self.assertIn("manager", state)
        self.assertIn("installed", state)
        self.assertIn("package_json_hash", state)
        self.assertIn("lodash", state["installed"])
        self.assertIn("jest", state["installed"])
        self.assertIsInstance(state["package_json_hash"], str)
        self.assertTrue(len(state["package_json_hash"]) > 0)


# ---------------------------------------------------------------------------
# N22 — Auto-detection of source packages with package.json
# ---------------------------------------------------------------------------

class TestAutoDetection(TestBase):

    def test_N22_autodetect_node_package(self):
        """N22: Git package with package.json but no explicit type: → auto-added to handler."""
        from ivpm.package import Package

        # Create a fake git package with a package.json on disk
        pkg_path = os.path.join(self.testdir, "packages", "auto_node_pkg")
        os.makedirs(pkg_path, exist_ok=True)
        with open(os.path.join(pkg_path, "package.json"), "w") as f:
            json.dump({"name": "auto-node-pkg", "version": "1.0.0"}, f)

        pkg = Package("auto_node_pkg")
        pkg.src_type = "git"
        pkg.pkg_type = None
        pkg.path = pkg_path

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(pkg, ui)

        self.assertIn("auto_node_pkg", handler._source_pkgs)
        _, td = handler._source_pkgs["auto_node_pkg"]
        self.assertTrue(td.link)  # default link=True


# ---------------------------------------------------------------------------
# N23-N24 — Hash-based install skip
# ---------------------------------------------------------------------------

class TestHashBasedSkip(TestBase):

    def test_N23_sync_reruns_install_on_change(self):
        """N23: Install runs when package.json hash changed."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        # Simulate previous state with a different hash
        ui.handler_state = {"node": {"package_json_hash": "old_hash_value"}}
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            handler.on_root_post_load(ui)

        called_cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
        install_calls = [c for c in called_cmds if "install" in c]
        self.assertTrue(len(install_calls) > 0, "Expected npm install to run on hash change")

    def test_N24_sync_skips_install_when_unchanged(self):
        """N24: Install skipped when package.json hash unchanged and node_modules exists."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        deps_dir = ui.deps_dir
        node_dir = os.path.join(deps_dir, "node")

        # First run to get real hash
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash", "^4.0.0"), ui)
        with patch("subprocess.run"):
            handler.on_root_post_load(ui)

        real_hash = handler.get_state_entries()["package_json_hash"]

        # Create fake node_modules so the skip condition is satisfied
        os.makedirs(os.path.join(node_dir, "node_modules"), exist_ok=True)

        # Second run — same hash, node_modules exists → no install
        handler2 = PackageHandlerNode()
        ui2 = _make_update_info(self.testdir)
        ui2.handler_state = {"node": {"package_json_hash": real_hash}}
        # Reuse same deps_dir
        ui2.deps_dir = deps_dir

        handler2.on_root_pre_load(ui2)
        handler2.on_leaf_post_load(_make_npm_pkg("lodash", "^4.0.0"), ui2)

        with patch("subprocess.run") as mock_run2:
            handler2.on_root_post_load(ui2)

        called_cmds = [c.args[0] for c in mock_run2.call_args_list if c.args]
        install_calls = [c for c in called_cmds if "install" in c]
        self.assertEqual(len(install_calls), 0,
                         "Expected npm install to be skipped when hash unchanged. "
                         "Calls: %s" % called_cmds)


if __name__ == "__main__":
    unittest.main()
