"""
Unit tests for PackageHandlerModules.

Uses the TestBase integration pattern with self.mkFile() and
self.ivpm_update(skip_venv=True).  Module packages are simulated using
src: dir with type: module to avoid needing a real module system.
"""
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
