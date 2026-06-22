import os
import yaml
from .test_base import TestBase

MAP_NAME = "dv-flow-package-map.yaml"


class TestDvFlow(TestBase):

    def _map_path(self):
        return os.path.join(self.testdir, "packages", MAP_NAME)

    def _load_map(self):
        with open(self._map_path()) as f:
            return yaml.safe_load(f)

    def test_single_flow_dep(self):
        """A dependency with a root flow.yaml is enumerated, keyed by its package.name."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_single
            dep-sets:
                - name: default-dev
                  deps:
                    - name: flow_leaf1
                      url: file://${DATA_DIR}/flow_leaf1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isfile(self._map_path()),
                        "%s should be generated" % MAP_NAME)
        doc = self._load_map()
        pm = doc["package-map"]
        self.assertEqual(pm["version"], 1)
        self.assertEqual(pm["packages"],
                         [{"name": "hdl.sim.vcs", "path": "flow_leaf1/flow.yaml"}])

    def test_name_authority(self):
        """The map keys on the dv-flow package.name, not the ivpm dir/package name."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_authority
            dep-sets:
                - name: default-dev
                  deps:
                    - name: flow_leaf1
                      url: file://${DATA_DIR}/flow_leaf1
                      src: dir
                    - name: flow_leaf2
                      url: file://${DATA_DIR}/flow_leaf2
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        pkgs = self._load_map()["package-map"]["packages"]
        names = [p["name"] for p in pkgs]
        # dv-flow names, sorted; dir names (flow_leaf1/2) appear only in paths
        self.assertEqual(names, ["hdl.sim.vcs", "uvm.util"])
        by_name = {p["name"]: p["path"] for p in pkgs}
        self.assertEqual(by_name["hdl.sim.vcs"], "flow_leaf1/flow.yaml")
        self.assertEqual(by_name["uvm.util"], "flow_leaf2/flow.yaml")

    def test_no_flow_no_map(self):
        """No dependency with flow.yaml and no config -> no map file."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_none
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.isfile(self._map_path()),
                         "%s should NOT be generated when no flow.yaml deps exist" % MAP_NAME)

    def test_enable_forces_empty_map(self):
        """dv-flow.enable: true writes a (possibly empty) map even with no flow deps."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_enable
            with:
                dv-flow:
                    enable: true
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isfile(self._map_path()),
                        "%s should be generated when enable: true" % MAP_NAME)
        self.assertEqual(self._load_map()["package-map"]["packages"], [])

    def test_enable_false_suppresses(self):
        """dv-flow.enable: false suppresses the map even when flow.yaml deps exist."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_optout
            with:
                dv-flow:
                    enable: false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: flow_leaf1
                      url: file://${DATA_DIR}/flow_leaf1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.isfile(self._map_path()),
                         "%s should NOT be generated when enable: false" % MAP_NAME)

    def test_missing_package_name_skipped(self):
        """A flow.yaml without package.name is skipped (no entry, no crash)."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_noname
            dep-sets:
                - name: default-dev
                  deps:
                    - name: flow_leaf1
                      url: file://${DATA_DIR}/flow_leaf1
                      src: dir
                    - name: flow_noname
                      url: file://${DATA_DIR}/flow_noname
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        pkgs = self._load_map()["package-map"]["packages"]
        self.assertEqual([p["name"] for p in pkgs], ["hdl.sim.vcs"])

    def test_stale_map_removed(self):
        """A previously-generated map is deleted when activation flips off."""
        os.makedirs(os.path.join(self.testdir, "packages"), exist_ok=True)
        with open(self._map_path(), "w") as f:
            f.write("# Generated by IVPM dv-flow handler — do not edit.\n")
            yaml.safe_dump({"package-map": {"version": 1, "packages": [
                {"name": "stale.pkg", "path": "gone/flow.yaml"}]}}, f)
        self.assertTrue(os.path.isfile(self._map_path()))

        self.mkFile("ivpm.yaml", """
        package:
            name: test_dv_flow_stale
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.isfile(self._map_path()),
                         "stale %s should be removed on a non-activated update" % MAP_NAME)
