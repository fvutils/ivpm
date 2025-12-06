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
