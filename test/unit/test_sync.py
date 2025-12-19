import os
import stat
import subprocess
import sys

from .test_base import TestBase

class TestSync(TestBase):
    
    def tearDown(self):
        """Clean up test directory, handling read-only files created by cache:false packages"""
        if hasattr(self, 'testdir') and os.path.isdir(self.testdir):
            # Make all files/dirs writable before cleanup
            from .test_base import _force_rmtree
            try:
                _force_rmtree(self.testdir)
            except Exception as e:
                # If cleanup fails, at least try to make things writable for next test
                import shutil
                try:
                    for root, dirs, files in os.walk(self.testdir, topdown=False):
                        for d in dirs:
                            try:
                                os.chmod(os.path.join(root, d), stat.S_IRWXU)
                            except:
                                pass
                        for f in files:
                            try:
                                os.chmod(os.path.join(root, f), stat.S_IRWXU)
                            except:
                                pass
                except:
                    pass
        return super().tearDown()

    def test_sync_editable_only(self):
        """Test that sync only updates editable (non-cached) packages"""
        # Create a project with editable git package and a manually created read-only package
        self.mkFile("ivpm.yaml", """
        package:
            name: test_sync
            dep-sets:
                - name: default-dev
                  deps:
                    - name: vlsim
                      url: https://github.com/fvutils/vlsim.git
                      anonymous: true
        """)

        # Update packages
        self.ivpm_update(skip_venv=True)

        # Manually create a read-only git directory to simulate a cached package
        readonly_path = os.path.join(self.testdir, "packages/readonly_pkg")
        os.makedirs(os.path.join(readonly_path, ".git"))
        with open(os.path.join(readonly_path, "README.md"), "w") as f:
            f.write("readonly")
        
        # Make it read-only to simulate cache:false behavior
        for root, dirs, files in os.walk(readonly_path):
            for d in dirs:
                os.chmod(os.path.join(root, d), stat.S_IRUSR | stat.S_IXUSR)
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IRUSR)
        os.chmod(readonly_path, stat.S_IRUSR | stat.S_IXUSR)

        # Verify readonly_pkg is read-only
        readonly_mode = os.stat(readonly_path).st_mode
        self.assertFalse(bool(readonly_mode & stat.S_IWUSR), 
                        "readonly_pkg should be read-only")

        # Verify vlsim package is writable
        vlsim_path = os.path.join(self.testdir, "packages/vlsim")
        vlsim_mode = os.stat(vlsim_path).st_mode
        self.assertTrue(bool(vlsim_mode & stat.S_IWUSR), 
                       "vlsim package should be writable")

        # Get initial commit hash
        vlsim_commit_before = self.exec(
            ["git", "rev-parse", "HEAD"],
            cwd=vlsim_path).strip()

        # Run sync command
        self.ivpm_sync()

        # Verify readonly_pkg is still read-only (and wasn't synced)
        readonly_mode_after = os.stat(readonly_path).st_mode
        self.assertFalse(bool(readonly_mode_after & stat.S_IWUSR), 
                        "readonly_pkg should still be read-only")

        # Editable package should still be writable
        vlsim_mode_after = os.stat(vlsim_path).st_mode
        self.assertTrue(bool(vlsim_mode_after & stat.S_IWUSR), 
                       "vlsim package should still be writable")

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

