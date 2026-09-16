#****************************************************************************
#* test_compat.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software
#* distributed under the License is distributed on an "AS IS" BASIS,
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#* See the License for the specific language governing permissions and
#* limitations under the License.
#*
#****************************************************************************
"""Tests for the 3.9 shims in ivpm._compat.

Every test here drives the FALLBACK implementation directly rather than the
version-dispatching wrapper. On a 3.10+ developer machine the wrapper delegates
to the stdlib, so testing only the wrapper would leave the code that exists
solely for 3.9 covered by nothing but the 3.9 CI row -- the one place a breakage
is most expensive to discover.
"""
import glob
import os
import shutil
import sys
import tempfile
import unittest

from ivpm._compat import (
    _entry_points_fallback, _glob_rel_fallback, entry_points, glob_rel,
)


class TestGlobRel(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ivpm-compat-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _touch(self, rel):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fp:
            fp.write("x")
        return path

    def _both(self, pattern, root=None, recursive=True):
        """Return (fallback, reference) results for the same query."""
        root = self.root if root is None else root
        fallback = sorted(_glob_rel_fallback(pattern, root, recursive=recursive))
        if sys.version_info >= (3, 10):
            reference = sorted(glob.glob(pattern, root_dir=root,
                                         recursive=recursive))
        else:
            reference = None
        return fallback, reference

    def test_returns_paths_relative_to_root(self):
        self._touch("plugin.json")
        self._touch("plugins/a/plugin.json")
        fallback, reference = self._both("plugins/*/plugin.json")
        self.assertEqual([os.path.join("plugins", "a", "plugin.json")], fallback)
        if reference is not None:
            self.assertEqual(reference, fallback)

    def test_recursive_globstar(self):
        self._touch("skills/one/SKILL.md")
        self._touch("skills/one/nested/SKILL.md")
        self._touch("skills/other.md")
        fallback, reference = self._both("**/SKILL.md")
        self.assertEqual(
            [os.path.join("skills", "one", "SKILL.md"),
             os.path.join("skills", "one", "nested", "SKILL.md")],
            fallback)
        if reference is not None:
            self.assertEqual(reference, fallback)

    def test_no_match_is_empty(self):
        fallback, reference = self._both("nothing/*/plugin.json")
        self.assertEqual([], fallback)
        if reference is not None:
            self.assertEqual(reference, fallback)

    def test_root_containing_glob_metacharacters(self):
        """A checkout under 'pkg[1]/' must match itself, not be read as a class.

        This is the failure the fallback has that the stdlib does not: the root
        is concatenated into the pattern, so it has to be escaped -- and the
        returned paths still have to be relative to the UNESCAPED root.
        """
        root = os.path.join(self.root, "pkg[1]")
        os.makedirs(root)
        with open(os.path.join(root, "plugin.json"), "w") as fp:
            fp.write("x")
        fallback, reference = self._both("plugin.json", root=root)
        self.assertEqual(["plugin.json"], fallback)
        if reference is not None:
            self.assertEqual(reference, fallback)

    def test_wrapper_agrees_with_fallback(self):
        self._touch("plugins/a/plugin.json")
        self.assertEqual(
            sorted(glob_rel("plugins/*/plugin.json", self.root, recursive=True)),
            sorted(_glob_rel_fallback("plugins/*/plugin.json", self.root,
                                      recursive=True)))


class TestEntryPoints(unittest.TestCase):

    #: Published by IVPM itself (see [project.entry-points] in pyproject.toml),
    #: so it is present in any environment that can import ivpm from an install.
    GROUP = "ivpm.handlers"

    def _names(self, eps):
        return sorted(ep.name for ep in eps)

    def test_fallback_matches_stdlib(self):
        fallback = self._names(_entry_points_fallback(self.GROUP))
        if not fallback:
            self.skipTest("ivpm metadata not installed (running from source only)")
        self.assertIn("python", fallback)
        if sys.version_info >= (3, 10):
            import importlib.metadata as md
            self.assertEqual(self._names(md.entry_points(group=self.GROUP)),
                             fallback)

    def test_fallback_filters_by_group(self):
        self.assertEqual([], list(_entry_points_fallback("ivpm.no-such-group")))

    def test_fallback_entry_points_are_loadable_and_attributed(self):
        eps = [ep for ep in _entry_points_fallback(self.GROUP)
               if ep.name == "python"]
        if not eps:
            self.skipTest("ivpm metadata not installed (running from source only)")
        ep = eps[0]
        self.assertEqual(self.GROUP, ep.group)
        self.assertTrue(ep.value)
        # .dist is the whole reason the fallback walks distributions rather than
        # reading 3.9's flat entry_points() dict: `ivpm show` reports provenance
        # from it, and a plugin with no attributable distribution silently
        # renders as built-in.
        from ivpm.show.info_types import ep_provenance
        provider, _version = ep_provenance(ep)
        self.assertEqual("ivpm", provider)
        self.assertTrue(callable(ep.load()) or ep.load() is not None)

    def test_wrapper_agrees_with_fallback(self):
        self.assertEqual(self._names(entry_points(group=self.GROUP)),
                         self._names(_entry_points_fallback(self.GROUP)))


if __name__ == "__main__":
    unittest.main()
