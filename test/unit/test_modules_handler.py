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
