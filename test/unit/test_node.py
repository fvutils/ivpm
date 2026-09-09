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
  N19      npm install subprocess call (mocked)
  N20      Source packages emitted as file: deps in the generated package.json
  N21      get_state_entries() structure
  N22      Auto-detection of source packages with package.json
  N23-N24  Hash-based install skip (sync-like idempotency)
  N25-N32  Root node_modules symlink: creation, config, guards, destroy
  N33      A failed install keeps the installer output that explains it
  N34-N36  subdir: the package.json is not always at the package root
  N24b-c   A linked source package re-runs npm install, so `prepare` rebuilds
"""

import contextlib
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

from ivpm.package import get_type_data
from ivpm.pkg_types.package_npm import PackageNpm
from ivpm.pkg_types.package_packagejson import PackagePackageJson
from ivpm.pkg_content_type import NodeTypeData, NodeContentType, parse_type_field
from ivpm.pkg_content_type_rgy import PkgContentTypeRgy
from ivpm.proj_info import NodeConfig, ProjInfo
from ivpm.handlers.package_handler_node import (
    PackageHandlerNode, _patch_packages_envrc_node, _write_node_envrc,
    _NODE_SENTINEL_BEGIN, _NODE_SENTINEL_END,
)
from ivpm.installer_run import InstallerResult
from ivpm.project_ops_info import ProjectUpdateInfo

from ivpm.yamlsrc.loader import SrcLoaderError as FATAL_ERROR

from .test_base import TestBase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def patch_npm(returncode=0, lines=(), spawn_failed=False):
    """Stand in for the node package manager for the duration of a block.

    These tests assert on the command the handler *builds*; none of them want
    a real ``npm install``. The handler runs installers through
    ``run_installer`` rather than ``subprocess.run``, so that is what has to be
    intercepted -- patching ``subprocess.run`` would leave npm to run for real.

    The mock's ``call_args_list`` holds the argv as ``args[0]``, matching what
    the previous ``subprocess.run`` patch exposed.
    """
    with patch("ivpm.handlers.package_handler_node.run_installer") as m:
        m.side_effect = lambda cmd, **kw: InstallerResult(
            returncode=returncode, lines=list(lines), cmd=list(cmd),
            spawn_failed=spawn_failed)
        yield m


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


def _make_node_src_pkg(testdir, name, pkg_json_name=None, dev=False, link=True,
                       subdir=None):
    """Build a source Package carrying NodeTypeData, backed by a real dir.

    A package.json is written when *pkg_json_name* is given so the handler can
    read the package's true name -- which is what the generated dep must be
    keyed by. It is written into *subdir* when one is given, which is the
    layout ``subdir`` exists for: a repository whose npm package is one
    directory among several.
    """
    from ivpm.package import Package
    pkg = Package(name)
    pkg.src_type = "git"
    pkg.pkg_type = None
    pkg.path = os.path.join(testdir, "packages", name)
    manifest_dir = (os.path.join(pkg.path, *subdir.split("/"))
                    if subdir else pkg.path)
    os.makedirs(manifest_dir, exist_ok=True)
    if pkg_json_name is not None:
        with open(os.path.join(manifest_dir, "package.json"), "w") as fp:
            json.dump({"name": pkg_json_name, "version": "1.0.0"}, fp)

    nd = NodeTypeData(dev=dev, link=link, subdir=subdir)
    nd.type_name = "node"
    pkg.type_data.append(nd)
    return pkg


def _read_generated_pkg_json(ui):
    """Load the packages/node/package.json the handler synthesised."""
    with open(os.path.join(ui.deps_dir, "node", "package.json")) as fp:
        return json.load(fp)


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
        """N07: type: node leaves dev/link unspecified; resolved() applies the
        defaults.

        The fields default to None rather than to False/True so that a
        provider's 'provides:' declaration and a consumer's 'with:' can be
        merged field by field -- with concrete defaults, "unset" and
        "explicitly set to the default" are the same value and the provider's
        setting could never survive.
        """
        ct = NodeContentType()
        data = ct.create_data({}, si=None)
        self.assertIsInstance(data, NodeTypeData)
        self.assertIsNone(data.dev)
        self.assertIsNone(data.link)

        resolved = data.resolved()
        self.assertFalse(resolved.dev)
        self.assertTrue(resolved.link)
        self.assertEqual(data.type_name, "node")

    def test_N08_node_content_type_dev_link(self):
        """N08: type: {node: {dev: true, link: false}} sets both fields."""
        ct = NodeContentType()
        data = ct.create_data({"dev": True, "link": False}, si=None)
        self.assertTrue(data.dev)
        self.assertFalse(data.link)

    def test_N08b_subdir_parsed_and_normalised(self):
        """N08b: subdir is a relative POSIX path; separators are normalised.

        Windows separators are accepted because the value lands in a
        package.json, where npm wants forward slashes whatever platform wrote
        the file.
        """
        ct = NodeContentType()
        self.assertIsNone(ct.create_data({}, si=None).subdir)
        self.assertEqual(ct.create_data({"subdir": "ts"}, si=None).subdir, "ts")
        self.assertEqual(
            ct.create_data({"subdir": "packages/core"}, si=None).subdir,
            "packages/core")
        self.assertEqual(
            ct.create_data({"subdir": "packages\\core"}, si=None).subdir,
            "packages/core")
        self.assertEqual(ct.create_data({"subdir": "ts/"}, si=None).subdir, "ts")
        # "the package root", spelled two ways, is the same as unspecified.
        self.assertIsNone(ct.create_data({"subdir": "."}, si=None).subdir)
        self.assertIsNone(ct.create_data({"subdir": ""}, si=None).subdir)

    def test_N08c_subdir_escaping_the_package_is_rejected(self):
        """N08c: '..' and absolute paths are refused at parse time.

        They resolve outside the fetched package, so the generated file: spec
        would name a directory the manifest never mentioned -- a dependency
        silently taken from somewhere else entirely. An absolute path is
        rejected rather than quietly reinterpreted as relative: '/ts' meaning
        'ts' is a guess about intent, and guessing here picks a different
        package's sources.
        """
        ct = NodeContentType()
        for bad in ("../elsewhere", "ts/../../elsewhere", "/abs/path", "/ts/"):
            with self.subTest(subdir=bad):
                with self.assertRaises(FATAL_ERROR):
                    ct.create_data({"subdir": bad}, si=None)

    def test_N08d_unknown_node_option_still_rejected(self):
        """N08d: adding subdir did not open the option set."""
        ct = NodeContentType()
        with self.assertRaises(FATAL_ERROR):
            ct.create_data({"subdirectory": "ts"}, si=None)

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
        self.assertIn("subdir", schema["properties"])
        self.assertEqual(schema["properties"]["dev"]["type"], "boolean")
        self.assertEqual(schema["properties"]["link"]["type"], "boolean")
        self.assertEqual(schema["properties"]["subdir"]["type"], "string")

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

        with patch_npm():
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

        with patch_npm():
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

        with patch_npm():
            handler.on_root_post_load(ui)

        envrc_path = os.path.join(ui.deps_dir, "packages.envrc")
        self.assertTrue(os.path.isfile(envrc_path))
        with open(envrc_path) as f:
            content = f.read()
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

        with open(os.path.join(deps_dir, "packages.envrc")) as f:
            content = f.read()
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

        with patch_npm():
            handler.on_root_post_load(ui)

        nvmrc = os.path.join(ui.deps_dir, "node", ".nvmrc")
        self.assertTrue(os.path.isfile(nvmrc))
        with open(nvmrc) as f:
            self.assertEqual(f.read().strip(), "20")

    def test_N17_nvmrc_not_written_when_absent(self):
        """N17: No version: → no .nvmrc file."""
        nc = NodeConfig()   # version=None
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir, node_config=nc)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch_npm():
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
        """N19: Verify the installer is invoked with npm install --prefix."""
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch_npm() as mock_run:
            handler.on_root_post_load(ui)

        node_dir = os.path.join(ui.deps_dir, "node")
        called_cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
        install_calls = [c for c in called_cmds if "install" in c]
        self.assertTrue(any("npm" in c and "--prefix" in c and node_dir in c
                            for c in install_calls),
                        "Expected npm install --prefix %s in calls: %s" % (node_dir, called_cmds))

    def test_N20_source_pkg_emitted_as_file_dep(self):
        """N20: type: node source packages become file: deps, not a link call.

        Asserts on the generated package.json rather than on argv: the previous
        spelling of this test mocked the installer and only checked that an
        `npm link ... --prefix` command was *constructed*. That command could
        never succeed -- --prefix is the global prefix in link mode, so npm
        looked for <node_dir>/lib and exited ENOENT -- and the failure was
        swallowed as a warning, so source packages were silently never
        installed. A mock-shaped assertion cannot catch that; this one can.
        """
        src_pkg = _make_node_src_pkg(self.testdir, "my_ts_lib",
                                     pkg_json_name="@org/my-ts-lib")

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)

        with patch_npm() as mock_run:
            handler.on_root_post_load(ui)

        data = _read_generated_pkg_json(ui)
        # Keyed by the name from the package's own package.json: npm will
        # install a file: dep under any key, but only the real name is
        # importable by the package's dependants.
        self.assertEqual(data["dependencies"].get("@org/my-ts-lib"),
                         "file:../my_ts_lib")
        self.assertNotIn("my_ts_lib", data["dependencies"])

        called_cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
        self.assertEqual([c for c in called_cmds if "link" in c], [],
                         "no separate link command should be issued")

    def test_N20b_source_pkg_dev_and_link_false(self):
        """N20b: dev: true routes to devDependencies; link: false is excluded."""
        dev_pkg = _make_node_src_pkg(self.testdir, "dev_lib",
                                     pkg_json_name="dev-lib", dev=True)
        skip_pkg = _make_node_src_pkg(self.testdir, "tracked_only",
                                      pkg_json_name="tracked-only", link=False)

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(dev_pkg, ui)
        handler.on_leaf_post_load(skip_pkg, ui)

        with patch_npm() as mock_run:
            handler.on_root_post_load(ui)

        data = _read_generated_pkg_json(ui)
        self.assertEqual(data["devDependencies"].get("dev-lib"),
                         "file:../dev_lib")
        # link: false means "track the source, keep it out of the node
        # environment" -- it must not appear in either dependency map.
        self.assertNotIn("tracked-only", data.get("dependencies", {}))
        self.assertNotIn("tracked-only", data.get("devDependencies", {}))

    def test_N20c_source_pkg_file_spec_is_relative(self):
        """N20c: file: specs are relative, so the tree stays relocatable."""
        src_pkg = _make_node_src_pkg(self.testdir, "rel_lib",
                                     pkg_json_name="rel-lib")
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)

        with patch_npm() as mock_run:
            handler.on_root_post_load(ui)

        spec = _read_generated_pkg_json(ui)["dependencies"]["rel-lib"]
        self.assertTrue(spec.startswith("file:.."),
                        "expected a relative file: spec, got %r" % spec)
        self.assertNotIn(self.testdir, spec)

    def test_N34_subdir_routes_spec_and_key(self):
        """N34: subdir moves *both* the file: spec and the dep key.

        The two are asserted together deliberately. They are read from the same
        directory by construction (`_node_pkg_dir`), and letting them drift
        would install the right tree under a name nothing imports it by --
        which presents exactly like the package not being installed at all.
        """
        src_pkg = _make_node_src_pkg(self.testdir, "pssparser",
                                     pkg_json_name="@psstools/pssparser",
                                     subdir="ts")

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)

        with patch_npm():
            handler.on_root_post_load(ui)

        deps = _read_generated_pkg_json(ui)["dependencies"]
        self.assertEqual(deps.get("@psstools/pssparser"),
                         "file:../pssparser/ts")
        # Not the repository root, which is what it named before subdir
        # existed and is a directory npm cannot read a manifest from.
        self.assertNotIn("file:../pssparser", deps.values())

    def test_N35_subdir_absent_names_the_package_root(self):
        """N35: without subdir the spec is unchanged — the default is the root."""
        src_pkg = _make_node_src_pkg(self.testdir, "flat_lib",
                                     pkg_json_name="flat-lib")
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)

        with patch_npm():
            handler.on_root_post_load(ui)

        self.assertEqual(
            _read_generated_pkg_json(ui)["dependencies"]["flat-lib"],
            "file:../flat_lib")

    def test_N36_explicit_node_without_manifest_is_fatal(self):
        """N36: 'type: node' naming a directory with no package.json is fatal.

        The regression this closes: the handler warned, wrote a file: spec for
        the manifest-less directory, npm reported "added 1 package", and the
        update exited 0 -- leaving a symlink that resolves to nothing. A silent
        success is the worst available outcome for a declaration that cannot be
        honoured.
        """
        src_pkg = _make_node_src_pkg(self.testdir, "no_manifest",
                                     pkg_json_name=None)
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)

        with self.assertRaises(FATAL_ERROR):
            handler.on_leaf_post_load(src_pkg, ui)

    def test_N36b_wrong_subdir_is_fatal_and_says_so(self):
        """N36b: a subdir that names the wrong directory is fatal, located."""
        src_pkg = _make_node_src_pkg(self.testdir, "wrong_subdir",
                                     pkg_json_name="wrong-subdir",
                                     subdir="ts")
        # The manifest is at ts/; point the declaration at a sibling.
        get_type_data(src_pkg, NodeTypeData).subdir = "client"

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)

        with self.assertRaises(FATAL_ERROR):
            handler.on_leaf_post_load(src_pkg, ui)

    def test_N36c_link_false_is_not_checked(self):
        """N36c: link: false keeps the package out of the environment, so it
        has no manifest requirement to meet."""
        src_pkg = _make_node_src_pkg(self.testdir, "tracked_no_manifest",
                                     pkg_json_name=None, link=False)
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)  # must not raise

        with patch_npm():
            handler.on_root_post_load(ui)

        data = _read_generated_pkg_json(ui)
        self.assertNotIn("tracked_no_manifest", data.get("dependencies", {}))


# ---------------------------------------------------------------------------
# N25-N29 — root node_modules symlink (code-development support)
# ---------------------------------------------------------------------------

class TestRootNodeModulesLink(TestBase):
    """The root symlink is what makes the managed packages importable from
    project sources. NODE_PATH cannot substitute: the ESM resolver ignores it,
    so `import` from project code fails while `require()` works."""

    def _run(self, node_config=None, install_mode=None):
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir, node_config=node_config)
        if install_mode is not None:
            ui.install_mode = install_mode
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)
        with patch_npm() as mock_run:
            # the installer is mocked, so npm never creates node_modules;
            # create it so the handler sees a real install to link at.
            os.makedirs(os.path.join(ui.deps_dir, "node", "node_modules"),
                        exist_ok=True)
            handler.on_root_post_load(ui)
        return handler, ui

    def test_N25_root_symlink_created(self):
        """N25: <project>/node_modules links to packages/node/node_modules."""
        _, ui = self._run()
        link = os.path.join(self.testdir, "node_modules")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(
            os.path.realpath(link),
            os.path.realpath(os.path.join(ui.deps_dir, "node", "node_modules")))

    def test_N26_root_symlink_is_relative(self):
        """N26: the link target is relative, so the tree can be moved."""
        self._run()
        target = os.readlink(os.path.join(self.testdir, "node_modules"))
        self.assertFalse(os.path.isabs(target),
                         "expected a relative target, got %r" % target)

    def test_N27_root_symlink_disabled_by_config(self):
        """N27: with.node.link-root: false suppresses the symlink."""
        self._run(node_config=NodeConfig(link_root=False))
        self.assertFalse(os.path.exists(os.path.join(self.testdir, "node_modules")))

    def test_N28_existing_real_dir_not_clobbered(self):
        """N28: a real node_modules is left alone -- it may hold packages IVPM
        knows nothing about, and replacing it would silently delete them."""
        real = os.path.join(self.testdir, "node_modules")
        os.makedirs(real)
        canary = os.path.join(real, "PRECIOUS")
        open(canary, "w").close()

        self._run()

        self.assertFalse(os.path.islink(real))
        self.assertTrue(os.path.isfile(canary))

    def test_N29_no_root_symlink_in_toolchain_mode(self):
        """N29: a toolchain deps-dir is the root; there is no project to link."""
        from ivpm.project_ops_info import InstallMode
        _, ui = self._run(install_mode=InstallMode.TOOLCHAIN)
        self.assertFalse(
            os.path.exists(os.path.join(self.testdir, "node_modules")))

    def test_N30_rerun_is_idempotent(self):
        """N30: a second update leaves the existing correct link in place."""
        self._run()
        first = os.readlink(os.path.join(self.testdir, "node_modules"))
        self._run()
        self.assertEqual(os.readlink(os.path.join(self.testdir, "node_modules")),
                         first)

    def test_N31_destroy_removes_root_symlink(self):
        """N31: destroy removes the link it created along with packages/node."""
        from ivpm.project_ops_info import ProjectRemoveInfo
        handler, ui = self._run()
        link = os.path.join(self.testdir, "node_modules")
        self.assertTrue(os.path.islink(link))

        ri = ProjectRemoveInfo(args=MagicMock(), deps_dir=ui.deps_dir)
        removed = handler.on_destroy(ri)

        self.assertIn(link, removed)
        self.assertFalse(os.path.lexists(link))
        self.assertFalse(os.path.isdir(os.path.join(ui.deps_dir, "node")))

    def test_N32_destroy_leaves_foreign_node_modules(self):
        """N32: destroy must not remove a node_modules it did not create."""
        from ivpm.project_ops_info import ProjectRemoveInfo
        handler, ui = self._run(node_config=NodeConfig(link_root=False))
        real = os.path.join(self.testdir, "node_modules")
        os.makedirs(real)

        ri = ProjectRemoveInfo(args=MagicMock(), deps_dir=ui.deps_dir)
        removed = handler.on_destroy(ri)

        self.assertNotIn(real, removed)
        self.assertTrue(os.path.isdir(real))


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

        with patch_npm():
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

        with patch_npm() as mock_run:
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
        with patch_npm():
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

        with patch_npm() as mock_run2:
            handler2.on_root_post_load(ui2)

        called_cmds = [c.args[0] for c in mock_run2.call_args_list if c.args]
        install_calls = [c for c in called_cmds if "install" in c]
        self.assertEqual(len(install_calls), 0,
                         "Expected npm install to be skipped when hash unchanged. "
                         "Calls: %s" % called_cmds)

    def test_N24b_linked_source_pkg_defeats_the_skip(self):
        """N24b: a linked source package forces the install even on a match.

        This is what makes a source dependency's build automatic. npm re-runs a
        file: dependency's `prepare` script on every install, and `prepare` is
        where a source package builds itself. The hash only covers the
        *generated manifest*, which does not change when the linked package's
        sources do -- so skipping meant the dependency was fetched, its build
        ran exactly once, and every later update left the consumer compiling
        against a stale artifact with nothing said.
        """
        src_pkg = _make_node_src_pkg(self.testdir, "src_lib",
                                     pkg_json_name="src-lib")

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)
        with patch_npm():
            handler.on_root_post_load(ui)

        real_hash = handler.get_state_entries()["package_json_hash"]
        os.makedirs(os.path.join(ui.deps_dir, "node", "node_modules"),
                    exist_ok=True)

        # Second run: identical manifest, node_modules present -- every
        # condition the skip tests for is satisfied.
        handler2 = PackageHandlerNode()
        ui2 = _make_update_info(self.testdir)
        ui2.handler_state = {"node": {"package_json_hash": real_hash}}
        ui2.deps_dir = ui.deps_dir
        handler2.on_root_pre_load(ui2)
        handler2.on_leaf_post_load(
            _make_node_src_pkg(self.testdir, "src_lib",
                               pkg_json_name="src-lib"), ui2)

        with patch_npm() as mock_run2:
            handler2.on_root_post_load(ui2)

        install_calls = [c.args[0] for c in mock_run2.call_args_list
                         if c.args and "install" in c.args[0]]
        self.assertEqual(len(install_calls), 1,
                         "a linked source package must re-run npm install so "
                         "its prepare script rebuilds")

    def test_N24c_link_false_source_pkg_does_not_defeat_the_skip(self):
        """N24c: link: false keeps the package out of the environment, so it
        has no prepare to re-run and must not force an install."""
        src_pkg = _make_node_src_pkg(self.testdir, "tracked_lib",
                                     pkg_json_name="tracked-lib", link=False)

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(src_pkg, ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash", "^4.0.0"), ui)
        with patch_npm():
            handler.on_root_post_load(ui)

        real_hash = handler.get_state_entries()["package_json_hash"]
        os.makedirs(os.path.join(ui.deps_dir, "node", "node_modules"),
                    exist_ok=True)

        handler2 = PackageHandlerNode()
        ui2 = _make_update_info(self.testdir)
        ui2.handler_state = {"node": {"package_json_hash": real_hash}}
        ui2.deps_dir = ui.deps_dir
        handler2.on_root_pre_load(ui2)
        handler2.on_leaf_post_load(
            _make_node_src_pkg(self.testdir, "tracked_lib",
                               pkg_json_name="tracked-lib", link=False), ui2)
        handler2.on_leaf_post_load(_make_npm_pkg("lodash", "^4.0.0"), ui2)

        with patch_npm() as mock_run2:
            handler2.on_root_post_load(ui2)

        install_calls = [c.args[0] for c in mock_run2.call_args_list
                         if c.args and "install" in c.args[0]]
        self.assertEqual(len(install_calls), 0, "Calls: %s" % install_calls)


# ---------------------------------------------------------------------------
# N33 — a failed install keeps its evidence
# ---------------------------------------------------------------------------

class TestFailedInstallReporting(TestBase):
    """suppress_output used to mean stdout=DEVNULL, which threw away npm's
    account of the failure at exactly the moment it was needed."""

    def _run_failing_install(self, lines):
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        ui.suppress_output = True
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch_npm(returncode=1, lines=lines):
            with self.assertRaises(Exception) as ctx:
                handler.on_root_post_load(ui)
        return str(ctx.exception)

    def test_N33a_installer_output_survives_suppression(self):
        out = self._run_failing_install([
            "npm error code EJSONPARSE",
            "npm error JSON.parse Unexpected token } in JSON at position 91",
        ])
        self.assertIn("EJSONPARSE", out)
        self.assertIn("Unexpected token", out)

    def test_N33b_exit_code_and_command_are_reported(self):
        out = self._run_failing_install(["npm error boom"])
        self.assertIn("exit 1", out)
        self.assertIn("npm", out)

    def test_N33c_missing_npm_is_reported_as_missing(self):
        """An absent package manager needs different advice from an install
        that ran and failed."""
        out = self._run_failing_install([])
        self.assertIn("exit 1", out)

        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)
        with patch_npm(returncode=127, spawn_failed=True):
            with self.assertRaises(Exception) as ctx:
                handler.on_root_post_load(ui)
        self.assertIn("not found", str(ctx.exception))

    def test_N33d_exit_127_from_a_script_is_not_missing_npm(self):
        """127 alone does not mean the manager is absent.

        npm exits 127 when a lifecycle script it ran hits a missing command --
        a `prepare` that reaches `tsc: not found` is the case that produced
        this. Reporting that as "npm not found, please install Node.js" sends
        the user to fix a tool that had just successfully run their build, and
        throws away npm's own output, which names the real missing command.
        """
        handler = PackageHandlerNode()
        ui = _make_update_info(self.testdir)
        handler.on_root_pre_load(ui)
        handler.on_leaf_post_load(_make_npm_pkg("lodash"), ui)

        with patch_npm(returncode=127, lines=["sh: 1: tsc: not found"]):
            with self.assertRaises(Exception) as ctx:
                handler.on_root_post_load(ui)

        msg = str(ctx.exception)
        self.assertNotIn("please install Node.js", msg)
        self.assertIn("tsc: not found", msg)


if __name__ == "__main__":
    unittest.main()
