import os
import subprocess
from .test_base import TestBase
from toposort import CircularDependencyError


class TestCircularDeps(TestBase):
    """
    Tests for handling circular dependencies in sub-packages.
    
    When the root project specifies all packages, the sub-packages' 
    declared dependencies on each other should not create a cycle
    because the root "owns" those dependencies.
    
    Example cycle:
    - pkg_a depends on pkg_b
    - pkg_b depends on pkg_c  
    - pkg_c depends on pkg_a
    
    When root specifies all three, no cycle should exist because
    root resolved all dependencies at the top level.
    """

    def test_circular_deps_from_root(self):
        """
        Test that circular dependencies in sub-packages don't cause
        a CircularDependencyError when all packages are specified
        at the root level.
        
        The root project specifies A, B, and C directly.
        Even though A->B->C->A forms a cycle in the sub-package
        declarations, no error should occur because the root
        "owns" all three dependencies.
        """
        self.mkFile("ivpm.yaml", """
        package:
            name: circular_root
            dep-sets:
                - name: default-dev
                  deps:
                    - name: circular_pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
                    - name: circular_pkg_b
                      url: file://${DATA_DIR}/circular_pkg_b
                      src: dir
                    - name: circular_pkg_c
                      url: file://${DATA_DIR}/circular_pkg_c
                      src: dir
        """)

        # This should NOT raise CircularDependencyError
        # because all packages are resolved at root level
        self.ivpm_update(skip_venv=False)

        # Verify all packages were loaded
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/circular_pkg_a")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/circular_pkg_b")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/circular_pkg_c")))

    def test_circular_deps_partial_from_root(self):
        """
        Test circular dependencies when root specifies only some packages.
        
        Root specifies A and C. A depends on B, B depends on C, C depends on A.
        - A is resolved by root
        - B is resolved by A (so A->B edge exists)
        - C is resolved by root (so B->C edge should NOT exist)
        
        This should work because B's dependency on C is overridden by root.
        """
        self.mkFile("ivpm.yaml", """
        package:
            name: circular_partial
            dep-sets:
                - name: default-dev
                  deps:
                    - name: circular_pkg_a
                      url: file://${DATA_DIR}/circular_pkg_a
                      src: dir
                    - name: circular_pkg_c
                      url: file://${DATA_DIR}/circular_pkg_c
                      src: dir
        """)

        # This should NOT raise CircularDependencyError
        self.ivpm_update(skip_venv=False)

        # Verify packages were loaded (B should be pulled in by A)
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/circular_pkg_a")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/circular_pkg_b")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/circular_pkg_c")))
