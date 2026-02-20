import os
from .test_base import TestBase


class TestDirenv(TestBase):

    def test_leaf_export_envrc(self):
        """A single package with export.envrc produces packages.envrc."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_direnv_leaf
            dep-sets:
                - name: default-dev
                  deps:
                    - name: envrc_leaf1
                      url: file://${DATA_DIR}/envrc_leaf1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages.envrc")
        self.assertTrue(os.path.isfile(envrc_path),
                        "packages.envrc should be generated")

        with open(envrc_path) as f:
            content = f.read()
        self.assertIn("source_env ./packages/envrc_leaf1/export.envrc", content)

    def test_no_envrc_no_file(self):
        """Packages without envrc files produce no packages.envrc."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_direnv_no_envrc
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages.envrc")
        self.assertFalse(os.path.isfile(envrc_path),
                         "packages.envrc should NOT be generated when no envrc files exist")

    def test_dotenvrc_used_when_no_export(self):
        """A package with only .envrc (no export.envrc) is included via .envrc."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_direnv_dotenvrc
            dep-sets:
                - name: default-dev
                  deps:
                    - name: envrc_leaf2
                      url: file://${DATA_DIR}/envrc_leaf2
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages.envrc")
        self.assertTrue(os.path.isfile(envrc_path))
        with open(envrc_path) as f:
            content = f.read()
        self.assertIn("source_env ./packages/envrc_leaf2/.envrc", content)

    def test_dependency_order(self):
        """Leaf packages appear before dependents in packages.envrc."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_direnv_order
            dep-sets:
                - name: default-dev
                  deps:
                    - name: envrc_nonleaf
                      url: file://${DATA_DIR}/envrc_nonleaf
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        envrc_path = os.path.join(self.testdir, "packages.envrc")
        self.assertTrue(os.path.isfile(envrc_path))
        with open(envrc_path) as f:
            content = f.read()

        # All three packages should appear
        self.assertIn("packages/envrc_leaf1/export.envrc", content)
        self.assertIn("packages/envrc_leaf2/.envrc", content)
        self.assertIn("packages/envrc_nonleaf/export.envrc", content)

        # Leaves must appear before the non-leaf that depends on them
        idx_leaf1 = content.index("envrc_leaf1")
        idx_leaf2 = content.index("envrc_leaf2")
        idx_nonleaf = content.index("envrc_nonleaf")
        self.assertLess(idx_leaf1, idx_nonleaf,
                        "envrc_leaf1 should appear before envrc_nonleaf")
        self.assertLess(idx_leaf2, idx_nonleaf,
                        "envrc_leaf2 should appear before envrc_nonleaf")

    def test_export_envrc_preferred_over_dotenvrc(self):
        """export.envrc is preferred when a package has both files."""
        # Create a package with both files in the test run directory
        self.mkFile("ivpm.yaml", """
        package:
            name: test_direnv_prefer
            dep-sets:
                - name: default-dev
                  deps:
                    - name: envrc_leaf1
                      url: file://${DATA_DIR}/envrc_leaf1
                      src: dir
        """)
        # envrc_leaf1 already has export.envrc; verify it's used not .envrc
        self.ivpm_update(skip_venv=True)

        with open(os.path.join(self.testdir, "packages.envrc")) as f:
            content = f.read()
        self.assertIn("export.envrc", content)
        self.assertNotIn("envrc_leaf1/.envrc", content)
