"""
Regression tests for source-located dependency-update failures.

When a package fails to fetch (bad URL, network error, ...), the user must be
told *which* dependency specification caused it and *where* it lives
(file:line:col), not just the package name. These tests cover the two halves of
that contract:

* ``ProjectUpdateInfo.package_error`` attaches the dependency's
  ``file:line:col`` to the dispatched PACKAGE_ERROR event.
* ``package_updater._origin_suffix`` summarises the failing spec (src/url and,
  for transitive deps, the requiring package).
"""
import unittest

from ivpm.package import Package
from ivpm.package_updater import _origin_suffix
from ivpm.project_ops_info import ProjectUpdateInfo
from ivpm.update_event import (
    UpdateEventDispatcher, UpdateEventListener, UpdateEventType,
)
from ivpm.yamlsrc import SrcInfo, SrcText


class _Collector(UpdateEventListener):
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


def _si(line=7, col=7, filename="/proj/ivpm.yaml"):
    return SrcInfo(filename, line, col, srctext=SrcText(filename, None))


class TestPackageErrorLocation(unittest.TestCase):

    def _mk_info(self):
        collector = _Collector()
        dispatcher = UpdateEventDispatcher()
        dispatcher.add_listener(collector)
        info = ProjectUpdateInfo(args=object(), deps_dir="/proj/packages")
        info.event_dispatcher = dispatcher
        return info, collector

    def test_loc_attached_from_srcinfo(self):
        info, collector = self._mk_info()
        info.package_error("sim", "boom", loc=_si(7, 7))
        ev = collector.events[-1]
        self.assertEqual(ev.event_type, UpdateEventType.PACKAGE_ERROR)
        self.assertEqual(ev.package_loc, "/proj/ivpm.yaml:7:7")

    def test_loc_resolved_from_package(self):
        # A Package carries its location on .srcinfo; package_error should
        # accept the package directly and dig the location out.
        info, collector = self._mk_info()
        pkg = Package(name="sim", srcinfo=_si(12, 5))
        info.package_error("sim", "boom", loc=pkg)
        self.assertEqual(collector.events[-1].package_loc, "/proj/ivpm.yaml:12:5")

    def test_no_loc_is_none(self):
        info, collector = self._mk_info()
        info.package_error("sim", "boom")
        self.assertIsNone(collector.events[-1].package_loc)

    def test_unknown_location_is_none(self):
        # A SrcInfo with no resolved position must not produce a bogus ":-1:-1".
        info, collector = self._mk_info()
        info.package_error("sim", "boom", loc=SrcInfo("ivpm.yaml"))
        self.assertIsNone(collector.events[-1].package_loc)


class TestOriginSuffix(unittest.TestCase):

    def test_src_and_url(self):
        pkg = Package(name="sim")
        pkg.src_type = "git"
        pkg.url = "https://example.com/x.git"
        suffix = _origin_suffix(pkg)
        self.assertIn("src: git", suffix)
        self.assertIn("url: https://example.com/x.git", suffix)

    def test_transitive_names_requirer(self):
        pkg = Package(name="sim")
        pkg.resolved_by = "top"
        self.assertIn("required by: top", _origin_suffix(pkg))

    def test_empty_when_nothing_known(self):
        self.assertEqual(_origin_suffix(Package(name="sim")), "")


if __name__ == "__main__":
    unittest.main()
