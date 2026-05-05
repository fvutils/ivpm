import logging
import os
import unittest
from .test_base import TestBase


class TestFuseSoCHandler(TestBase):

    # ------------------------------------------------------------------ #
    # Basic Output Generation                                             #
    # ------------------------------------------------------------------ #

    def test_cores_envrc_generated(self):
        """Single dep with .core → fusesoc-cores.envrc created with correct content."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_fusesoc_basic
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        self.assertTrue(os.path.isfile(envrc), "fusesoc-cores.envrc should exist")
        with open(envrc) as f:
            content = f.read()
        self.assertIn("FUSESOC_CORES", content)
        self.assertIn("packages/fusesoc_leaf1", content)

    def test_cores_txt_generated(self):
        """fusesoc-cores.txt created with absolute paths and provenance comments."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_fusesoc_txt
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        txt = os.path.join(self.testdir, "packages", "fusesoc-cores.txt")
        self.assertTrue(os.path.isfile(txt), "fusesoc-cores.txt should exist")
        with open(txt) as f:
            content = f.read()
        self.assertIn("from: fusesoc_leaf1", content)
        self.assertIn("packages/fusesoc_leaf1", content)

    def test_no_cores_no_output(self):
        """Dep without .core files → no output files created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_fusesoc_no_cores
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_no_cores
                      url: file://${DATA_DIR}/fusesoc_no_cores
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        txt = os.path.join(self.testdir, "packages", "fusesoc-cores.txt")
        self.assertFalse(os.path.isfile(envrc))
        self.assertFalse(os.path.isfile(txt))

    def test_project_root_cores_included(self):
        """Root project has .core file → included in outputs."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_root_cores
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("my_core.core", """\
CAPI=2:
  name: ::my_core:1.0
  filesets:
    rtl:
      files: [my_core.v]
""")
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        self.assertTrue(os.path.isfile(envrc))
        with open(envrc) as f:
            content = f.read()
        self.assertIn(os.path.basename(self.testdir), content)

    # ------------------------------------------------------------------ #
    # Explicit Declaration                                                #
    # ------------------------------------------------------------------ #

    def test_declared_cores_used(self):
        """Dep declares with.fusesoc.cores → those dirs used, not auto-detect."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_declared_cores
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf3
                      url: file://${DATA_DIR}/fusesoc_leaf3
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        self.assertTrue(os.path.isfile(envrc))
        with open(envrc) as f:
            content = f.read()
        # Should reference the cores/ subdirectory, not the root
        self.assertIn("cores", content)

    def test_declared_nonroot_dirs(self):
        """Dep declares subdirectory core path → that path appears in outputs."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_declared_subdir
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf3
                      url: file://${DATA_DIR}/fusesoc_leaf3
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        txt = os.path.join(self.testdir, "packages", "fusesoc-cores.txt")
        with open(txt) as f:
            content = f.read()
        self.assertIn("cores", content)

    # ------------------------------------------------------------------ #
    # Import Filtering                                                    #
    # ------------------------------------------------------------------ #

    def test_import_all_default(self):
        """import: all (or absent) → all dep cores included."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_import_all
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
                    - name: fusesoc_leaf3
                      url: file://${DATA_DIR}/fusesoc_leaf3
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        with open(envrc) as f:
            content = f.read()
        self.assertIn("fusesoc_leaf1", content)
        self.assertIn("fusesoc_leaf3", content)

    def test_import_filtered_list(self):
        """import: [pkg_a] → only pkg_a's cores included."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_import_filtered
            with:
                fusesoc:
                    import: [fusesoc_leaf1]
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
                    - name: fusesoc_leaf3
                      url: file://${DATA_DIR}/fusesoc_leaf3
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        txt = os.path.join(self.testdir, "packages", "fusesoc-cores.txt")
        with open(txt) as f:
            content = f.read()
        self.assertIn("fusesoc_leaf1", content)
        self.assertNotIn("fusesoc_leaf3", content)

    def test_import_empty_list(self):
        """import: [] → only project root cores included."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_import_empty
            with:
                fusesoc:
                    import: []
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.mkFile("my_core.core", """\
CAPI=2:
  name: ::my_core:1.0
  filesets:
    rtl:
      files: [my_core.v]
""")
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        with open(envrc) as f:
            content = f.read()
        # Should NOT contain leaf1 cores
        self.assertNotIn("fusesoc_leaf1", content)
        # Should still contain the project root core
        self.assertIn(os.path.basename(self.testdir), content)

    # ------------------------------------------------------------------ #
    # packages.envrc Integration                                          #
    # ------------------------------------------------------------------ #

    def test_appends_to_packages_envrc(self):
        """Direnv handler active → packages.envrc contains sentinel-wrapped fusesoc section."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_appends_envrc
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        # Create packages.envrc to simulate direnv handler being active
        envrc_dir = os.path.join(self.testdir, "packages")
        os.makedirs(envrc_dir, exist_ok=True)
        with open(os.path.join(envrc_dir, "packages.envrc"), "w") as f:
            f.write("# existing content\n")

        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages", "packages.envrc")
        with open(envrc_path) as f:
            content = f.read()
        self.assertIn("ivpm:fusesoc begin", content)
        self.assertIn("ivpm:fusesoc end", content)
        self.assertIn("fusesoc-cores.envrc", content)

    def test_no_direnv_no_append(self):
        """Direnv handler not active → packages.envrc not created/touched."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_no_direnv
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages", "packages.envrc")
        self.assertFalse(os.path.isfile(envrc_path))

    def test_sentinel_replaces_old_section(self):
        """Second ivpm update → old sentinel section replaced, not duplicated."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_sentinel_replace
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        # Create packages.envrc with old fusesoc section
        envrc_dir = os.path.join(self.testdir, "packages")
        os.makedirs(envrc_dir, exist_ok=True)
        with open(os.path.join(envrc_dir, "packages.envrc"), "w") as f:
            f.write("# existing\n# --- ivpm:fusesoc begin ---\nsource_env ./fusesoc-cores.envrc\n# --- ivpm:fusesoc end ---\n# trailing\n")

        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages", "packages.envrc")
        with open(envrc_path) as f:
            content = f.read()
        # Should have exactly one fusesoc section
        self.assertEqual(content.count("ivpm:fusesoc begin"), 1)
        self.assertEqual(content.count("ivpm:fusesoc end"), 1)

    # ------------------------------------------------------------------ #
    # Edge Cases                                                           #
    # ------------------------------------------------------------------ #

    def test_pypi_packages_skipped(self):
        """PyPI dep with .core files → not scanned."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_pypi_skip
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_pypi
                      url: file://${DATA_DIR}/fusesoc_pypi
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        self.assertFalse(os.path.isfile(envrc))

    def test_fake_core_file_ignored(self):
        """data.core without CAPI-2 content → not registered."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_fake_core
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_not_core
                      url: file://${DATA_DIR}/fusesoc_not_core
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        self.assertFalse(os.path.isfile(envrc))

    def test_dedup_core_dirs(self):
        """Two deps pointing to same dir → single entry in outputs."""
        # Both leaf1 and a copy will have the same set of cores
        self.mkFile("ivpm.yaml", """
        package:
            name: test_dedup
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        txt = os.path.join(self.testdir, "packages", "fusesoc-cores.txt")
        with open(txt) as f:
            lines = [l for l in f.read().splitlines() if l and not l.startswith("#")]
        # Count unique paths
        self.assertEqual(len(lines), len(set(lines)))

    def test_no_deps_no_project_cores(self):
        """No deps + no project cores → no files created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_nothing
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        txt = os.path.join(self.testdir, "packages", "fusesoc-cores.txt")
        self.assertFalse(os.path.isfile(envrc))
        self.assertFalse(os.path.isfile(txt))

    def test_missing_declared_dir_warns(self):
        """Dep declares nonexistent core dir → warning logged."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_missing_dir
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_missing_core
                      url: file://${DATA_DIR}/fusesoc_missing_core
                      src: dir
        """)
        with self.assertLogs("ivpm.handlers.package_handler_fusesoc", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)
        self.assertTrue(any("matched no files" in msg or "does not exist" in msg
                            for msg in cm.output))

    # ------------------------------------------------------------------ #
    # Stale Entry Cleanup                                                 #
    # ------------------------------------------------------------------ #

    def test_stale_entries_removed(self):
        """Second run with fewer deps → stale envrc/txt updated."""
        # First run: one dep
        self.mkFile("ivpm.yaml", """
        package:
            name: test_stale
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        self.assertTrue(os.path.isfile(envrc))

        # Second run: no deps, no cores
        self.mkFile("ivpm.yaml", """
        package:
            name: test_stale
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.isfile(envrc),
                         "Stale fusesoc-cores.envrc should have been removed")

    def test_idempotent_second_run(self):
        """Second run with same deps → outputs unchanged."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_idempotent
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        envrc = os.path.join(self.testdir, "packages", "fusesoc-cores.envrc")
        with open(envrc) as f:
            content1 = f.read()

        self.ivpm_update(skip_venv=True)

        with open(envrc) as f:
            content2 = f.read()
        self.assertEqual(content1, content2)

    # ------------------------------------------------------------------ #
    # fusesoc.conf Update (opt-in)                                        #
    # ------------------------------------------------------------------ #

    def test_conf_update_creates_sections(self):
        """update-conf: true → fusesoc.conf has [library.ivpm.*] sections."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_conf_create
            with:
                fusesoc:
                    update-conf: true
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        conf_path = os.path.join(self.testdir, "fusesoc.conf")
        self.assertTrue(os.path.isfile(conf_path))
        with open(conf_path) as f:
            content = f.read()
        self.assertIn("library.ivpm.fusesoc_leaf1", content)

    def test_conf_update_false_default(self):
        """update-conf: false (default) → fusesoc.conf not touched."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_conf_false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: fusesoc_leaf1
                      url: file://${DATA_DIR}/fusesoc_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        conf_path = os.path.join(self.testdir, "fusesoc.conf")
        self.assertFalse(os.path.isfile(conf_path))


if __name__ == "__main__":
    unittest.main()
