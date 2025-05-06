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