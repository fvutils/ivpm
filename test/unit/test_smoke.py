import os
from .test_base import TestBase

class TestSmoke(TestBase):

    def test_smoke(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: smoke
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update()

        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/leaf_proj1")))
        self.assertTrue(os.path.isfile(os.path.join(self.testdir, "packages/leaf_proj1/leaf_proj1.txt")))

    def test_nested_2(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: smoke
            dep-sets:
                - name: default-dev
                  deps:
                    - name: nonleaf_refleaf_proj1
                      url: file://${DATA_DIR}/nonleaf_refleaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/nonleaf_refleaf_proj1")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/leaf_proj1")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/leaf_proj2")))
#        self.assertTrue(os.path.isfile(os.path.join(self.testdir, "packages/leaf_proj1/leaf_proj1.txt")))

    def test_git_fetch_leaf1(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: git_fetch_leaf1
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
        """)

        self.ivpm_update(skip_venv=False)

        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/vlsim")))
        self.assertEqual(self.exec(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=os.path.join(self.testdir, "packages/vlsim")).strip(), "master")

    def test_url_tar_gz(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: git_fetch_leaf1
            dep-sets:
                - name: default-dev
                  deps:
                    - name: googletest
                      url: https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
        """)

        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/googletest")))
        self.assertTrue(os.path.isfile(os.path.join(self.testdir, "packages/googletest/README.md")))

    def test_url_tar_gz_rename(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: git_fetch_leaf1
            dep-sets:
                - name: default-dev
                  deps:
                    - name: gtest
                      url: https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
        """)

        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/gtest")))
        self.assertTrue(os.path.isfile(os.path.join(self.testdir, "packages/gtest/README.md")))

    def test_url_jar(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: url_jar
            dep-sets:
                - name: default-dev
                  deps:
                    - name: antlr-runtime.jar
                      url: https://www.antlr.org/download/antlr-runtime-4.13.2.jar
        """)

        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.isfile(os.path.join(self.testdir, "packages/antlr-runtime.jar")))

    def test_git_fetch_override(self):
        """Test that the branch spec from super-spec overrides the branch spec from the sub-spec"""
        self.mkFile("ivpm.yaml", """
        package:
            name: git_fetch_override
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      branch: gh-pages
                      anonymous: true
                    - name: git_fetch_leaf1
                      url: file://${DATA_DIR}/git_fetch_leaf1
                      src: dir
        """)

        self.ivpm_update(skip_venv=False)

        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/vlsim")))
        self.assertEqual(self.exec(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=os.path.join(self.testdir, "packages/vlsim")).strip(), "gh-pages")

