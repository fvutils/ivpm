"""
Tests that the built-in handlers are registered without relying on package
metadata.

Running from a source tree (PYTHONPATH=src, no egg-info/dist-info) makes
importlib.metadata entry-point discovery return nothing. When the registry
depended on entry points alone, that produced an empty handler set and
'ivpm update' silently skipped Python installation, envrc generation and every
other root-phase step -- while still exiting 0.

Coverage:
- _load_builtins() registers every shipped handler with no metadata present
- the built-in list matches [project.entry-points."ivpm.handlers"]
- _load() does not register a handler twice when entry points are available
"""

import os
import unittest

from ivpm.handlers.package_handler_rgy import PackageHandlerRgy

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


EXPECTED = ["agents", "direnv", "dv-flow", "fusesoc", "modules", "node", "python"]


def _pyproject_path():
    # test/unit/<this file> -> repo root
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "pyproject.toml")


class TestHandlerRgyBuiltins(unittest.TestCase):

    def test_builtins_registered_without_metadata(self):
        """_load_builtins alone must yield the full shipped handler set."""
        rgy = PackageHandlerRgy()
        rgy._load_builtins()
        self.assertEqual(EXPECTED, sorted(h.name for h in rgy.handlers))

    def test_builtins_match_entry_points(self):
        """The built-in list and the published entry points must not drift."""
        pyproject = _pyproject_path()
        if not os.path.isfile(pyproject):
            self.skipTest("pyproject.toml not available (installed package)")

        with open(pyproject, "rb") as fp:
            data = tomllib.load(fp)

        eps = data["project"]["entry-points"]["ivpm.handlers"]
        self.assertEqual(sorted(eps.keys()), EXPECTED)

    def test_load_does_not_duplicate(self):
        """Built-ins are also published as entry points; each must load once."""
        rgy = PackageHandlerRgy()
        rgy._load()
        names = sorted(h.name for h in rgy.handlers)
        self.assertEqual(EXPECTED, names)
        self.assertEqual(len(rgy.handlers), len(set(rgy.handlers)))


if __name__ == "__main__":
    unittest.main()
