import os
import subprocess
from .test_base import TestBase

class TestDepsetRef(TestBase):

    def test_cross_dep(self):
        self.mkFile("ivpm.yaml", """
        package:
            name: p1
            dep-sets:
                - name: default-dev
                  deps:
                    - name: p2
                      url: file://${TEST_DIR}/p2
                      src: dir
                      dep-set: default
        """)

        self.mkFile("p2/ivpm.yaml", """
        package:
            name: p2
            dep-sets:
                - name: default-dev
                  deps:
                    - name: p1
                      url: file://${TEST_DIR}
                      src: dir
                - name: default
                  deps: []
        """)

    def test_dep_set_less_dependency(self):
        """A dependency whose own manifest declares no dep-sets contributes no
        dependencies. Every dep-set name is absent from such a manifest, so
        reporting the (usually inherited) name as missing named a problem with
        no fix -- there is no other name it could have meant."""
        self.mkFile("ivpm.yaml", """
        package:
            name: p1
            dep-sets:
                - name: default-dev
                  deps:
                    - name: p2
                      url: file://${TEST_DIR}/p2
                      src: dir
        """)

        # p2 exists only to carry a 'with:' clause -- no dep-sets at all.
        self.mkFile("p2/ivpm.yaml", """
        package:
            name: p2
            with:
                env:
                    - name: FOO
                      value: bar
        """)

        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.path.isdir(os.path.join(
            self.testdir, "packages", "p2")))