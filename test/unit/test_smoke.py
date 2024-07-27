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


