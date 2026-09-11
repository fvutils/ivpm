"""
Unit tests for PackageHandlerModules.

Uses the TestBase integration pattern with self.mkFile() and
self.ivpm_update(skip_venv=True).  Module packages are simulated using
src: dir with type: module to avoid needing a real module system.
"""
import json
import os
import unittest
from .test_base import TestBase


class TestModulesHandlerBasic(TestBase):
    """Basic output generation tests."""

    def test_modules_envrc_generated(self):
        """Single module dep with type: module -> modules.envrc created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modules_basic
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          load: true
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "modules.envrc")
        self.assertTrue(os.path.isfile(envrc), "modules.envrc should exist")
        with open(envrc) as f:
            content = f.read()
        self.assertIn("module load", content)

    def test_no_module_deps_no_output(self):
        """No module-typed deps -> modules.envrc not created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_no_modules
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "modules.envrc")
        self.assertFalse(os.path.isfile(envrc))

    def test_modules_envrc_multiple(self):
        """Multiple module deps -> all module load statements present."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_multi_modules
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          load: true
                    - name: module_leaf2
                      url: file://${DATA_DIR}/module_leaf2
                      src: dir
                      type:
                        module:
                          load: true
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "modules.envrc")
        self.assertTrue(os.path.isfile(envrc))
        with open(envrc) as f:
            content = f.read()
        # Both should appear
        self.assertEqual(content.count("module load"), 2)


class TestModulesHandlerLoadFalse(TestBase):
    """Tests for load: false behavior."""

    def test_load_false_no_module_load(self):
        """type: { module: { load: false } } -> no module load in envrc."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_load_false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          load: false
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "modules.envrc")
        # No module deps with load=true -> no envrc generated
        self.assertFalse(os.path.isfile(envrc))


class TestModulesContentTypeYaml(TestBase):
    """Tests that the module content type is recognized in YAML."""

    def test_module_type_recognized(self):
        """type: module in YAML -> no error, package created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_module_type
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type: module
        """)
        # Should not raise
        self.ivpm_update(skip_venv=True)

    def test_module_type_unknown_param_raises(self):
        """type: { module: { foo: 1 } } -> fatal()."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_module_bad_param
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          foo: bar
        """)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)
        self.assertIn("foo", str(ctx.exception))


class TestModulesHandlerModulefile(TestBase):
    """src: module with modulefile: -- a path on disk, no modules system."""

    @property
    def _tool1(self):
        return os.path.join(self.data_dir, "modulefiles", "tool1", "1.0")

    def _envrc(self):
        path = os.path.join(self.testdir, "packages", "modules.envrc")
        self.assertTrue(os.path.isfile(path), "modules.envrc should exist")
        with open(path) as fp:
            return fp.read()

    def test_modulefile_dep_emits_absolute_path(self):
        """modulefile: dep -> 'module load <abs path>' and no 'module use'."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile
            dep-sets:
                - name: default-dev
                  deps:
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
        """)
        self.ivpm_update(skip_venv=True)

        content = self._envrc()
        self.assertIn("module load %s\n" % self._tool1, content)
        self.assertNotIn("module use", content)

    def test_status_after_update_modulefile_dep(self):
        """update -> status round-trip for a modulefile: dep (no fatal).

        The lock used to record both 'module' (null) and 'modulefile', which
        status() fed back through the manifest parser -- and that rejects an
        entry naming both forms.
        """
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile_status
            dep-sets:
                - name: default-dev
                  deps:
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
        """)
        self.ivpm_update(skip_venv=True)

        from ivpm.project_ops import ProjectOps
        _root, results = ProjectOps(self.testdir).status()

        r = next(x for x in results if x.name == "tool1")
        self.assertEqual(r.src_type, "module")

    def test_modulefile_relative_to_ivpm_yaml(self):
        """A relative modulefile: resolves against the declaring ivpm.yaml."""
        self.mkFile("etc/modulefiles/local/1.0",
                    "#%Module1.0\nsetenv LOCAL_HOME /opt/local\n")
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile_rel
            dep-sets:
                - name: default-dev
                  deps:
                    - name: local
                      src: module
                      modulefile: etc/modulefiles/local/1.0
        """)
        self.ivpm_update(skip_venv=True)

        expect = os.path.join(self.testdir, "etc", "modulefiles", "local", "1.0")
        self.assertIn("module load %s\n" % expect, self._envrc())

    def test_mixed_modulefile_and_type_module(self):
        """A modulefile: dep and a type: module dep each emit one load line."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile_mixed
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          load: true
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
        """)
        self.ivpm_update(skip_venv=True)

        content = self._envrc()
        self.assertEqual(content.count("module load"), 2)
        # The path form emits the absolute path; the fallback form the name
        self.assertIn("module load %s\n" % self._tool1, content)
        self.assertIn("module load module_leaf1\n", content)

    def test_load_false_resolves_but_emits_nothing(self):
        """load: false -> package still resolved, but no envrc generated."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile_noload
            dep-sets:
                - name: default-dev
                  deps:
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
                      type:
                        module:
                          load: false
        """)
        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.isfile(
            os.path.join(self.testdir, "packages", "modules.envrc")))

        # ...but the dep resolved: the lock records it with its root
        with open(os.path.join(self.testdir, "packages", "package-lock.json")) as fp:
            lock = json.load(fp)
        self.assertIn("tool1", lock["packages"])

    def test_missing_modulefile_is_fatal(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile_missing
            dep-sets:
                - name: default-dev
                  deps:
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/nope/1.0
        """)
        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)
        self.assertIn("does not exist", str(ctx.exception))


class TestModulesHandlerLock(TestBase):
    """Lock/state tagging of the two dep forms."""

    def _lock(self):
        with open(os.path.join(self.testdir, "packages", "package-lock.json")) as fp:
            return json.load(fp)

    def test_lock_tags_modulefile_and_module_forms(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modules_lock
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          load: true
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
        """)
        self.ivpm_update(skip_venv=True)

        modules = self._lock()["modules"]
        tool1 = os.path.join(self.data_dir, "modulefiles", "tool1", "1.0")
        self.assertEqual(modules["tool1"], {"modulefile": tool1})
        # Logical/fallback form keeps today's shape exactly
        self.assertEqual(modules["module_leaf1"], {"module": "module_leaf1"})

    def test_state_entries_round_trip(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modules_state
            dep-sets:
                - name: default-dev
                  deps:
                    - name: module_leaf1
                      url: file://${DATA_DIR}/module_leaf1
                      src: dir
                      type:
                        module:
                          load: true
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
        """)
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.testdir, "packages", "ivpm.json")) as fp:
            state = json.load(fp)
        module_pkgs = state["handlers"]["modules"]["module_pkgs"]
        tool1 = os.path.join(self.data_dir, "modulefiles", "tool1", "1.0")
        self.assertEqual(module_pkgs["tool1"],
                         {"spec": tool1, "modulefile": True})
        self.assertEqual(module_pkgs["module_leaf1"],
                         {"spec": "module_leaf1", "modulefile": False})


class TestModulefileReproduction(TestBase):
    """'ivpm update --lock' -- rebuilt from the lock, not from ivpm.yaml."""

    def test_modulefile_dep_reproduces(self):
        """A modulefile: dep resolves from the lock and keeps its envrc line.

        The reconstruction path builds the package by assigning attributes
        directly, so neither the declared form nor the module type-data used
        to survive it: resolution fell through to the logical branch (with no
        specifier), and the modules handler saw no module deps -- so it
        deleted modules.envrc instead of writing it.
        """
        import shutil

        tool1 = os.path.join(self.data_dir, "modulefiles", "tool1", "1.0")
        self.mkFile("ivpm.yaml", """
        package:
            name: test_modulefile_repro
            dep-sets:
                - name: default-dev
                  deps:
                    - name: tool1
                      src: module
                      modulefile: ${DATA_DIR}/modulefiles/tool1/1.0
        """)
        self.ivpm_update(skip_venv=True)

        packages = os.path.join(self.testdir, "packages")
        saved = os.path.join(self.testdir, "saved-lock.json")
        shutil.copy(os.path.join(packages, "package-lock.json"), saved)
        shutil.rmtree(packages)

        from ivpm.project_ops import ProjectOps

        class Args:
            anonymous_git = None
        ProjectOps(self.testdir, Args()).update(
            dep_set="default-dev", skip_venv=True, args=Args(), lock_file=saved)

        envrc = os.path.join(packages, "modules.envrc")
        self.assertTrue(os.path.isfile(envrc),
                        "modules.envrc should survive reproduction")
        with open(envrc) as fp:
            self.assertIn("module load %s\n" % tool1, fp.read())


class TestModuleFormSwitch(TestBase):
    """Editing ivpm.yaml to change which form a module dep is declared with.

    A module dep is resolved from the manifest on every update (nothing is
    materialized under the deps-dir to go stale), so the interesting question
    is whether the *lock* follows the edit -- including dropping the key for
    the form that is no longer declared.
    """

    def _lock_entry(self, name="tool1"):
        path = os.path.join(self.testdir, "packages", "package-lock.json")
        with open(path) as fp:
            return json.load(fp)["packages"][name]

    def _envrc(self):
        with open(os.path.join(self.testdir, "packages", "modules.envrc")) as fp:
            return fp.read()

    def _write_yaml(self, spec_line):
        self.mkFile("ivpm.yaml", """
        package:
            name: test_module_form_switch
            dep-sets:
                - name: default-dev
                  deps:
                    - name: tool1
                      src: module
                      %s
        """ % spec_line)

    def _fake_modules(self, path):
        """Patch in a modules interface that resolves 'tool1/1.0' to *path*."""
        from unittest import mock
        from .test_package_module import FakeModulesInterface
        return mock.patch(
            "ivpm.pkg_types.package_module._get_modules_interface",
            return_value=FakeModulesInterface({"tool1/1.0": path}))

    # The lock records the modulefile spec *as declared* (variables intact,
    # like a 'dir' path), and the machine-specific absolute path separately.
    _tool1_spec = "${DATA_DIR}/modulefiles/tool1/1.0"
    _tool2_spec = "${DATA_DIR}/modulefiles/tool2"

    @property
    def _tool1(self):
        return os.path.join(self.data_dir, "modulefiles", "tool1", "1.0")

    @property
    def _tool2(self):
        return os.path.join(self.data_dir, "modulefiles", "tool2")

    def test_switch_modulefile_to_module(self):
        """modulefile: -> module: rewrites the lock to the logical form."""
        self._write_yaml("modulefile: ${DATA_DIR}/modulefiles/tool1/1.0")
        self.ivpm_update(skip_venv=True)
        self.assertEqual(self._lock_entry()["modulefile"], self._tool1_spec)

        self._write_yaml("module: tool1/1.0")
        with self._fake_modules(self._tool1):
            self.ivpm_update(skip_venv=True)

        entry = self._lock_entry()
        self.assertEqual(entry["module"], "tool1/1.0")
        self.assertNotIn("modulefile", entry)
        self.assertEqual(entry["modulefile_resolved"], self._tool1)
        # The envrc follows too: a logical module loads by name, not by path.
        self.assertIn("module load tool1/1.0\n", self._envrc())

    def test_switch_module_to_modulefile(self):
        """module: -> modulefile: rewrites the lock to the path form."""
        self._write_yaml("module: tool1/1.0")
        with self._fake_modules(self._tool1):
            self.ivpm_update(skip_venv=True)
        self.assertEqual(self._lock_entry()["module"], "tool1/1.0")

        self._write_yaml("modulefile: ${DATA_DIR}/modulefiles/tool2")
        self.ivpm_update(skip_venv=True)

        entry = self._lock_entry()
        self.assertEqual(entry["modulefile"], self._tool2_spec)
        self.assertNotIn("module", entry)
        self.assertEqual(entry["modulefile_resolved"], self._tool2)
        self.assertIn("module load %s\n" % self._tool2, self._envrc())

    def test_repoint_modulefile(self):
        """A modulefile: pointed at a different file is picked up."""
        self._write_yaml("modulefile: ${DATA_DIR}/modulefiles/tool1/1.0")
        self.ivpm_update(skip_venv=True)

        self._write_yaml("modulefile: ${DATA_DIR}/modulefiles/tool2")
        self.ivpm_update(skip_venv=True)

        entry = self._lock_entry()
        self.assertEqual(entry["modulefile"], self._tool2_spec)
        self.assertEqual(entry["modulefile_resolved"], self._tool2)
        envrc = self._envrc()
        self.assertIn("module load %s\n" % self._tool2, envrc)
        self.assertNotIn(self._tool1, envrc)

    def test_status_after_switch(self):
        """status() reads the post-switch lock without a fatal."""
        self._write_yaml("module: tool1/1.0")
        with self._fake_modules(self._tool1):
            self.ivpm_update(skip_venv=True)

        self._write_yaml("modulefile: ${DATA_DIR}/modulefiles/tool1/1.0")
        self.ivpm_update(skip_venv=True)

        from ivpm.project_ops import ProjectOps
        _root, results = ProjectOps(self.testdir).status()
        r = next(x for x in results if x.name == "tool1")
        self.assertEqual(r.src_type, "module")

    def test_switch_reports_drift(self):
        """The lock comparison sees a form switch as a spec change.

        Not reachable from the update path (a module dep is never resident
        under the deps-dir, so it is re-resolved rather than drift-checked),
        but check_lock_changes() is public and must not report 'unchanged'
        for two different specs.
        """
        from ivpm.package_lock import check_lock_changes
        from ivpm.pkg_types.package_module import PackageModule

        self._write_yaml("modulefile: ${DATA_DIR}/modulefiles/tool1/1.0")
        self.ivpm_update(skip_venv=True)

        now = PackageModule.create("tool1", {"module": "tool1/1.0"}, None)
        now.resolved_by = "root"
        diffs = check_lock_changes(
            os.path.join(self.testdir, "packages"), {"tool1": now})
        self.assertIn("tool1", diffs)
