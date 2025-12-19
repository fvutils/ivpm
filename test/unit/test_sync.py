import os
import stat
import subprocess
import sys

from .test_base import TestBase

class TestSync(TestBase):

    def test_sync_editable_only(self):
        """Test that sync only updates editable (non-cached) packages"""
        # Create a project with git packages with different cache settings
        self.mkFile("ivpm.yaml", """
        package:
            name: test_sync
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim_no_cache
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
                      cache: false
                    - name: vlsim_editable
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
        """)

        # Update packages - cache:false makes it read-only, no cache attr makes it writable
        self.ivpm_update(skip_venv=True)

        # Verify both packages exist
        no_cache_path = os.path.join(self.testdir, "packages/vlsim_no_cache")
        editable_path = os.path.join(self.testdir, "packages/vlsim_editable")
        self.assertTrue(os.path.isdir(no_cache_path))
        self.assertTrue(os.path.isdir(editable_path))

        # Verify no_cache package is read-only
        no_cache_mode = os.stat(no_cache_path).st_mode
        self.assertFalse(bool(no_cache_mode & stat.S_IWUSR), 
                        "cache:false package should be read-only")

        # Verify editable package is writable
        editable_mode = os.stat(editable_path).st_mode
        self.assertTrue(bool(editable_mode & stat.S_IWUSR), 
                       "Editable package should be writable")

        # Get initial commit hashes
        no_cache_commit_before = self.exec(
            ["git", "rev-parse", "HEAD"],
            cwd=no_cache_path).strip()
        editable_commit_before = self.exec(
            ["git", "rev-parse", "HEAD"],
            cwd=editable_path).strip()

        # Run sync command
        self.ivpm_sync()

        # Verify no_cache package is still read-only
        no_cache_mode_after = os.stat(no_cache_path).st_mode
        self.assertFalse(bool(no_cache_mode_after & stat.S_IWUSR), 
                        "cache:false package should still be read-only")
        
        no_cache_commit_after = self.exec(
            ["git", "rev-parse", "HEAD"],
            cwd=no_cache_path).strip()
        self.assertEqual(no_cache_commit_before, no_cache_commit_after,
                        "cache:false package should not be updated by sync")

        # Editable package should still be writable
        editable_mode_after = os.stat(editable_path).st_mode
        self.assertTrue(bool(editable_mode_after & stat.S_IWUSR), 
                       "Editable package should still be writable")

    def test_sync_git_only(self):
        """Test that sync only processes git packages"""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_sync_git
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        # Verify both packages exist
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/vlsim")))
        self.assertTrue(os.path.isdir(os.path.join(self.testdir, "packages/leaf_proj1")))

        # Sync should work without errors, skipping non-git packages
        self.ivpm_sync()

    def test_sync_no_cached_packages(self):
        """Test sync works when there are no cached packages"""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_sync_no_cache
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
        """)

        self.ivpm_update(skip_venv=True)

        # Verify package exists and is writable
        pkg_path = os.path.join(self.testdir, "packages/vlsim")
        self.assertTrue(os.path.isdir(pkg_path))
        mode = os.stat(pkg_path).st_mode
        self.assertTrue(bool(mode & stat.S_IWUSR), 
                       "Package without cache attribute should be writable")

        # Sync should process this package
        self.ivpm_sync()

if __name__ == '__main__':
    import unittest
    unittest.main()

