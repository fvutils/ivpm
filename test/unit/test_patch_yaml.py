#****************************************************************************
#* test_patch_yaml.py
#*
#* Phase 3 -- declaration parsing: 'patches:' flows from ivpm.yaml into a
#* resolved PatchSet on the package, paths resolve against the declaring
#* ivpm.yaml's directory, and the lock entry round-trips the patch identity.
#****************************************************************************
import os
import shutil
import tempfile
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.patch import PatchSet, md5_file
from ivpm.package_lock import _entry_from_pkg, _spec_matches_lock, _patch_spec_matches

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch")


def _consumer_yaml(patches_block, src="git", extra=""):
    return """
package:
  name: consumer
  dep-sets:
    - name: default
      deps:
        - name: somelib
          src: %s
          url: https://github.com/foo/somelib.git
          cache: true
%s%s
""" % (src, patches_block, extra)


class _ReaderBase(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        # Patch files travel with the consumer's ivpm.yaml.
        for p in ("fix.patch", "sub.patch"):
            shutil.copy(os.path.join(DATA, p), os.path.join(self.d, p))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def read(self, yaml_text):
        path = os.path.join(self.d, "ivpm.yaml")
        with open(path, "w") as fp:
            fp.write(yaml_text)
        with open(path) as fp:
            return IvpmYamlReader().read(fp, path)

    def pkg(self, info, name="somelib", dep_set="default"):
        return info.dep_set_m[dep_set].packages[name]


class TestPatchParsing(_ReaderBase):

    def test_string_and_mapping_forms(self):
        info = self.read(_consumer_yaml(
            "          patches:\n"
            "            - fix.patch\n"
            "            - file: sub.patch\n"
            "              strip: 1\n"
            "              directory: src\n"))
        pkg = self.pkg(info)
        self.assertEqual(len(pkg.patches), 2)

        a, b = pkg.patches
        self.assertEqual(a.name, "fix.patch")
        self.assertEqual(a.source, "fix.patch")
        self.assertEqual(a.strip, 1)
        self.assertIsNone(a.directory)
        self.assertEqual(a.md5, md5_file(os.path.join(self.d, "fix.patch")))
        # Resolved against the ivpm.yaml's directory.
        self.assertEqual(a.resolved_path, os.path.join(self.d, "fix.patch"))

        self.assertEqual(b.source, "sub.patch")
        self.assertEqual(b.directory, "src")

    def test_patchset_property_matches_recompute(self):
        info = self.read(_consumer_yaml(
            "          patches:\n"
            "            - fix.patch\n"))
        pkg = self.pkg(info)
        self.assertFalse(pkg.patchset.is_empty)
        self.assertEqual(pkg.patchset.patchset_id,
                         PatchSet(tuple(pkg.patches)).patchset_id)

    def test_tool_override_parsed(self):
        info = self.read(_consumer_yaml(
            "          patches:\n"
            "            - file: fix.patch\n"
            "              tool: git\n"))
        self.assertEqual(self.pkg(info).patches[0].tool, "git")

    def test_no_patches_is_empty(self):
        info = self.read(_consumer_yaml(""))
        self.assertTrue(self.pkg(info).patchset.is_empty)


class TestPatchParsingErrors(_ReaderBase):

    def _assert_fatal(self, yaml_text, *fragments):
        with self.assertRaises(Exception) as ctx:
            self.read(yaml_text)
        msg = str(ctx.exception)
        for frag in fragments:
            self.assertIn(frag, msg, "expected %r in %r" % (frag, msg))

    def test_missing_patch_file(self):
        self._assert_fatal(_consumer_yaml(
            "          patches:\n"
            "            - does-not-exist.patch\n"),
            "patch file not found", "does-not-exist.patch")

    def test_unknown_patch_option(self):
        self._assert_fatal(_consumer_yaml(
            "          patches:\n"
            "            - file: fix.patch\n"
            "              bogus: 1\n"),
            "unknown patch option", "bogus")

    def test_missing_file_key(self):
        self._assert_fatal(_consumer_yaml(
            "          patches:\n"
            "            - strip: 1\n"),
            "missing 'file'")

    def test_bad_tool(self):
        self._assert_fatal(_consumer_yaml(
            "          patches:\n"
            "            - file: fix.patch\n"
            "              tool: hg\n"),
            "must be 'git' or 'patch'")

    def test_patches_on_unsupported_source(self):
        # pypi is tier-0 (NONE): declaring patches is a config error.
        self._assert_fatal(
            """
package:
  name: consumer
  dep-sets:
    - name: default
      deps:
        - name: somepy
          pypi: true
          patches:
            - fix.patch
""",
            "does not support patches")


class TestPatchLockRoundTrip(_ReaderBase):

    def _patched_pkg(self):
        info = self.read(_consumer_yaml(
            "          patches:\n"
            "            - fix.patch\n"
            "            - file: sub.patch\n"
            "              directory: src\n"))
        pkg = self.pkg(info)
        pkg.src_type = "git"
        pkg.resolved_commit = "abc123def456"
        return pkg

    def test_entry_serializes_patch_fields(self):
        pkg = self._patched_pkg()
        entry = _entry_from_pkg(pkg)
        self.assertIn("patches", entry)
        self.assertEqual(len(entry["patches"]), 2)
        self.assertEqual(entry["patches"][0]["name"], "fix.patch")
        self.assertEqual(entry["patches"][1]["directory"], "src")
        self.assertEqual(entry["patchset_id"], pkg.patchset.patchset_id)

    def test_unpatched_entry_has_no_patch_fields(self):
        info = self.read(_consumer_yaml(""))
        pkg = self.pkg(info)
        pkg.src_type = "git"
        entry = _entry_from_pkg(pkg)
        self.assertNotIn("patches", entry)
        self.assertNotIn("patchset_id", entry)

    def test_spec_matches_lock_detects_patch_change(self):
        pkg = self._patched_pkg()
        entry = _entry_from_pkg(pkg)
        # Same spec -> matches.
        self.assertTrue(_spec_matches_lock(pkg, entry))
        # Drop a patch -> patchset_id changes -> spec no longer matches.
        pkg.patches = pkg.patches[:1]
        self.assertFalse(_spec_matches_lock(pkg, entry))

    def test_spec_match_unpatched_vs_patched(self):
        pkg = self._patched_pkg()
        entry = _entry_from_pkg(pkg)
        # An unpatched pkg must NOT match a patched lock entry.
        pkg.patches = []
        self.assertFalse(_spec_matches_lock(pkg, entry))


class TestPatchLockReproduction(_ReaderBase):
    """Gap 1: IvpmLockReader.build_packages_info() must reconstruct the patch set
    so a reproduced workspace re-derives the same patch identity (effective
    version) it was locked with -- not a silent pristine tree."""

    def _write_and_reproduce(self):
        from ivpm.package_lock import write_lock, IvpmLockReader
        from ivpm.packages_info import PackagesInfo
        info = self.read(_consumer_yaml(
            "          patches:\n"
            "            - fix.patch\n"
            "            - file: sub.patch\n"
            "              directory: src\n"))
        pkg = self.pkg(info)
        pkg.src_type = "git"
        pkg.resolved_commit = "abc123def456"
        pkg.resolved_by = "root"

        pi = PackagesInfo("root")
        pi[pkg.name] = pkg
        # deps_dir under the project root so patch files (in self.d) are found
        # via base_dirs' parent-of-lock-dir entry.
        deps_dir = os.path.join(self.d, "packages")
        write_lock(deps_dir, pi)

        reader = IvpmLockReader(os.path.join(deps_dir, "package-lock.json"))
        repro = reader.build_packages_info()
        return pkg, repro.packages["somelib"]

    def test_patchset_identity_round_trips(self):
        orig, repro = self._write_and_reproduce()
        self.assertEqual(len(repro.patches), 2)
        # Identity must match exactly -> same effective version -> cache HIT.
        self.assertFalse(repro.patchset.is_empty)
        self.assertEqual(repro.patchset.patchset_id, orig.patchset.patchset_id)

    def test_reconstructed_patch_identity_matches(self):
        orig, repro = self._write_and_reproduce()
        entry = _entry_from_pkg(orig)
        # The patch-identity comparison must hold for the reconstructed pkg --
        # this is the specific check Gap 1 broke (empty patchset -> mismatch).
        # (Full _spec_matches_lock additionally compares commit_requested, which
        # reproduction intentionally pins to commit_resolved -- out of scope here.)
        self.assertTrue(_patch_spec_matches(repro, entry))
        # And an empty-patchset pkg (the pre-fix behavior) would NOT match.
        repro.patches = []
        self.assertFalse(_patch_spec_matches(repro, entry))

    def test_resolved_path_located_for_miss(self):
        # Patch files live at the project root (parent of the deps/lock dir); the
        # reconstructed resolved_path should point at the real file so an uncached
        # MISS can re-apply rather than fail.
        _, repro = self._write_and_reproduce()
        for spec in repro.patches:
            self.assertTrue(os.path.isfile(spec.resolved_path),
                            "resolved_path not located: %s" % spec.resolved_path)
            self.assertEqual(md5_file(spec.resolved_path), spec.md5)


if __name__ == "__main__":
    unittest.main()
