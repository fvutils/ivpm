"""
Tests that http and gh-rls packages process a nested ivpm.yaml found inside
the unpacked bundle, so the bundle's transitive dependencies are discovered.

These exercise the "already loaded" path of update(), which scans the existing
pkg_dir via ProjInfo.mkFromProj() with no network I/O -- the same scan all
fetch paths fall through to. Mirrors the long-standing behavior of git/file
packages (package_git.py:182).
"""
import os
import tempfile
import unittest

from ivpm.pkg_types.package_http import PackageHttp
from ivpm.pkg_types.package_gh_rls import PackageGhRls


class _UpdateInfo:
    """Minimal ProjectUpdateInfo stub for the 'already loaded' path."""
    def __init__(self, deps_dir):
        self.deps_dir = deps_dir
        self.deps_source = None

    def report_package(self, cacheable=False, editable=False):
        pass


_NESTED_YAML = """\
package:
  name: bundle
  dep-sets:
    - name: default-dev
      deps:
        - name: transitive-dep
          url: https://example.com/transitive-dep.git
    - name: default
      deps: []
"""


class TestNestedIvpmYaml(unittest.TestCase):

    def _make_deps_dir(self, name, with_yaml=True):
        tmp = tempfile.mkdtemp()
        pkg_dir = os.path.join(tmp, name)
        os.makedirs(pkg_dir)
        if with_yaml:
            with open(os.path.join(pkg_dir, "ivpm.yaml"), "w") as fp:
                fp.write(_NESTED_YAML)
        return tmp

    def _assert_nested_dep_found(self, proj_info):
        self.assertIsNotNone(proj_info, "update() should return the nested ProjInfo")
        self.assertTrue(proj_info.has_dep_set("default-dev"))
        deps = proj_info.get_dep_set("default-dev")
        names = [p.name for p in deps.packages.values()]
        self.assertIn("transitive-dep", names)

    def test_http_processes_nested_yaml(self):
        deps_dir = self._make_deps_dir("mypkg")
        pkg = PackageHttp.__new__(PackageHttp)
        pkg.name = "mypkg"
        pkg.url = "https://example.com/mypkg.tar.gz"
        pkg.cache = None
        pkg.patches = []
        self._assert_nested_dep_found(pkg.update(_UpdateInfo(deps_dir)))

    def test_gh_rls_processes_nested_yaml(self):
        deps_dir = self._make_deps_dir("mypkg")
        pkg = PackageGhRls.__new__(PackageGhRls)
        pkg.name = "mypkg"
        pkg.url = "https://github.com/owner/mypkg"
        pkg.cache = None
        self._assert_nested_dep_found(pkg.update(_UpdateInfo(deps_dir)))

    def test_http_no_nested_yaml_returns_none(self):
        deps_dir = self._make_deps_dir("mypkg", with_yaml=False)
        pkg = PackageHttp.__new__(PackageHttp)
        pkg.name = "mypkg"
        pkg.url = "https://example.com/mypkg.tar.gz"
        pkg.cache = None
        pkg.patches = []
        self.assertIsNone(pkg.update(_UpdateInfo(deps_dir)))

    def test_gh_rls_no_nested_yaml_returns_none(self):
        deps_dir = self._make_deps_dir("mypkg", with_yaml=False)
        pkg = PackageGhRls.__new__(PackageGhRls)
        pkg.name = "mypkg"
        pkg.url = "https://github.com/owner/mypkg"
        pkg.cache = None
        self.assertIsNone(pkg.update(_UpdateInfo(deps_dir)))


if __name__ == "__main__":
    unittest.main()
