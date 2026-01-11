import os
import platform
import unittest

from .test_base import TestBase


def _check_github_api_available():
    """Check if GitHub API is accessible (not rate-limited)."""
    try:
        import httpx
        resp = httpx.get("https://api.github.com/rate_limit", timeout=5)
        if resp.status_code == 403:
            return False
        return True
    except Exception:
        return False


class TestGhRls(TestBase):

    @unittest.skipUnless(platform.system().lower() == "linux", "Linux-only test")
    @unittest.skipUnless(_check_github_api_available(), "GitHub API rate-limited or unavailable")
    def test_verilator_bin_linux(self):
        # Create a project that depends on a GitHub Release package
        self.mkFile("ivpm.yaml", """
        package:
            name: gh_rls_verilator
            dep-sets:
                - name: default-dev
                  deps:
                    - name: verilator-bin
                      url: https://github.com/pss-hands-on/verilator-bin
                      src: gh-rls
        """)

        # Fetch and install dependencies (no venv needed)
        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "verilator-bin")
        self.assertTrue(os.path.isdir(pkg_dir), "verilator-bin package directory missing")

        # Locate the 'verilator' executable in the unpacked package
        exe_path = None
        for root, _, files in os.walk(pkg_dir):
            if "verilator" in files:
                candidate = os.path.join(root, "verilator")
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    exe_path = candidate
                    break

        self.assertIsNotNone(exe_path, "verilator executable not found in installed package")

        # Running '--version' verifies we selected a runnable Linux binary
        out = self.exec([exe_path, "--version"], cwd=self.testdir)
        self.assertIn("Verilator", out)

    @unittest.skipUnless(platform.system().lower() == "linux", "Linux-only test")
    @unittest.skipUnless(_check_github_api_available(), "GitHub API rate-limited or unavailable")
    def test_protobuf_linux(self):
        # Create a project that depends on protobuf (uses generic linux-x86_64 naming)
        self.mkFile("ivpm.yaml", """
        package:
            name: gh_rls_protobuf
            dep-sets:
                - name: default-dev
                  deps:
                    - name: protobuf
                      url: https://github.com/protocolbuffers/protobuf
                      src: gh-rls
                      version: latest
        """)

        # Fetch and install dependencies (no venv needed)
        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "protobuf")
        self.assertTrue(os.path.isdir(pkg_dir), "protobuf package directory missing")

        # Locate the 'protoc' executable in the unpacked package (typically in bin/)
        exe_path = None
        for root, _, files in os.walk(pkg_dir):
            if "protoc" in files:
                candidate = os.path.join(root, "protoc")
                if os.path.isfile(candidate):
                    # Make executable if not already
                    if not os.access(candidate, os.X_OK):
                        os.chmod(candidate, 0o755)
                    exe_path = candidate
                    break

        self.assertIsNotNone(exe_path, "protoc executable not found in installed package")

        # Running '--version' verifies we selected a runnable Linux binary
        out = self.exec([exe_path, "--version"], cwd=self.testdir)
        self.assertIn("libprotoc", out)

    @unittest.skipUnless(_check_github_api_available(), "GitHub API rate-limited or unavailable")
    def test_source_forced(self):
        # Test that source=true forces download of source archive instead of binary
        self.mkFile("ivpm.yaml", """
        package:
            name: gh_rls_source_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test-pkg
                      url: https://github.com/astral-sh/uv
                      src: gh-rls
                      version: latest
                      source: true
        """)

        # Fetch and install dependencies
        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "test-pkg")
        self.assertTrue(os.path.isdir(pkg_dir), "test-pkg package directory missing")
        
        # When source=true, we should get source files, not just binaries
        # Check for common source indicators (e.g., Cargo.toml for uv, or README)
        has_source = False
        for item in os.listdir(pkg_dir):
            # Look for source indicators
            if item in ["Cargo.toml", "pyproject.toml", "setup.py", "README.md", "src", "crates"]:
                has_source = True
                break
        
        self.assertTrue(has_source, "Source files not found - may have downloaded binary instead")

    @unittest.skipUnless(_check_github_api_available(), "GitHub API rate-limited or unavailable")
    def test_trailing_slash_in_url(self):
        # Test that URLs with trailing slashes are handled correctly
        self.mkFile("ivpm.yaml", """
        package:
            name: gh_rls_trailing_slash_test
            dep-sets:
                - name: default-dev
                  deps:
                    - name: test-pkg-slash
                      url: https://github.com/astral-sh/uv/
                      src: gh-rls
                      version: latest
                      source: true
        """)

        # Fetch and install dependencies - should not fail with 404
        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "test-pkg-slash")
        self.assertTrue(os.path.isdir(pkg_dir), "test-pkg-slash package directory missing")
        
        # Verify source was downloaded
        has_source = False
        for item in os.listdir(pkg_dir):
            if item in ["Cargo.toml", "pyproject.toml", "setup.py", "README.md", "src", "crates"]:
                has_source = True
                break
        
        self.assertTrue(has_source, "Source files not found - trailing slash may have broken URL processing")

    @unittest.skipUnless(platform.system().lower() == "linux", "Linux-only test")
    @unittest.skipUnless(_check_github_api_available(), "GitHub API rate-limited or unavailable")
    def test_os_specific_packages(self):
        # Test that OS-specific packages (like qemu-riscv) are handled correctly
        self.mkFile("ivpm.yaml", """
        package:
            name: gh_rls_qemu_riscv
            dep-sets:
                - name: default-dev
                  deps:
                    - name: qemu-riscv
                      url: https://github.com/edapack/qemu-riscv
                      src: gh-rls
                      version: latest
        """)

        # Fetch and install dependencies
        # This should either succeed (if on ubuntu 22.04 or 24.04 x86_64) 
        # or fail with clear error message about OS-specific packages
        try:
            self.ivpm_update(skip_venv=True)
            pkg_dir = os.path.join(self.testdir, "packages", "qemu-riscv")
            self.assertTrue(os.path.isdir(pkg_dir), "qemu-riscv package directory missing")
        except Exception as e:
            # Should get informative error about OS-specific packages
            error_msg = str(e)
            self.assertTrue(
                "OS-specific package" in error_msg or "ubuntu" in error_msg.lower(),
                f"Expected OS-specific package error, got: {error_msg}"
            )



