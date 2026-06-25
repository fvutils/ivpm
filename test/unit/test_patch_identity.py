#****************************************************************************
#* test_patch_identity.py
#*
#* Unit tests for the patch identity layer (patch.py, Phase 1):
#* PatchSpec / PatchSet / patchset_id / effective_version, md5_file, the
#* patch manifest (write/read/round-trip), and manifest_matches.
#*
#* These are pure -- no dependency tree is fetched and no subprocess runs.
#****************************************************************************
import os
import json
import hashlib
import tempfile
import shutil
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.patch import (
    PatchSpec, PatchSet, PatchCapability, PatchError,
    PATCH_SEP, PATCH_MANIFEST_REL, IVPM_PATCH_VERSION,
    md5_file, effective_version,
    write_manifest, read_manifest, manifest_matches,
)


# --- shared fixtures -------------------------------------------------------

MD5_A = "5d41402abc4b2a76b9719d911017c592"   # md5("hello")
MD5_B = "7d793037a0760186574b0282f2f435e7"   # md5("world")


def spec_a(**kw):
    base = dict(name="fix-build.patch", source="fix-build.patch",
                resolved_path="/x/fix-build.patch", md5=MD5_A,
                strip=1, directory=None)
    base.update(kw)
    return PatchSpec(**base)


def spec_b(**kw):
    base = dict(name="add-feature.patch", source="patches/add-feature.patch",
                resolved_path="/x/patches/add-feature.patch", md5=MD5_B,
                strip=1, directory="src")
    base.update(kw)
    return PatchSpec(**base)


class TestPatchSetId(unittest.TestCase):

    def test_golden_digest(self):
        # *** Golden value: pins the on-disk patchset_id encoding (plan sec.10
        # risk). If this fails, the canonical JSON encoding in
        # PatchSet.patchset_id changed and every existing cache entry/manifest
        # was silently invalidated -- that change must be deliberate. ***
        ps = PatchSet((spec_a(), spec_b()))
        self.assertEqual(
            ps.patchset_id,
            "42aff8b9c7e29466934b3392593e10dfef6be0ace4157381c7d82d1261ab27b6")

    def test_order_sensitive(self):
        forward = PatchSet((spec_a(), spec_b())).patchset_id
        reverse = PatchSet((spec_b(), spec_a())).patchset_id
        self.assertNotEqual(forward, reverse)

    def test_content_addressed_md5(self):
        # Flipping one byte of content (its MD5) changes the id.
        base = PatchSet((spec_a(),)).patchset_id
        changed = PatchSet((spec_a(md5=MD5_B),)).patchset_id
        self.assertNotEqual(base, changed)

    def test_strip_and_dir_part_of_identity(self):
        base = PatchSet((spec_a(),)).patchset_id
        self.assertNotEqual(base, PatchSet((spec_a(strip=2),)).patchset_id)
        self.assertNotEqual(base, PatchSet((spec_a(directory="sub"),)).patchset_id)

    def test_name_and_source_not_part_of_identity(self):
        # Renaming the file (same bytes/strip/dir) does NOT change the id.
        a1 = spec_a(name="renamed.patch", source="some/other/renamed.patch",
                    resolved_path="/totally/different/renamed.patch")
        self.assertEqual(
            PatchSet((spec_a(),)).patchset_id,
            PatchSet((a1,)).patchset_id)

    def test_stable_across_constructions(self):
        self.assertEqual(
            PatchSet((spec_a(), spec_b())).patchset_id,
            PatchSet((spec_a(), spec_b())).patchset_id)

    def test_empty_set(self):
        self.assertTrue(PatchSet().is_empty)
        self.assertTrue(PatchSet(()).is_empty)
        self.assertFalse(PatchSet((spec_a(),)).is_empty)


class TestEffectiveVersion(unittest.TestCase):

    def test_empty_returns_base_byte_for_byte(self):
        base = "abc123def456"
        self.assertEqual(effective_version(base, PatchSet()), base)
        # Identity, not merely equality of value.
        self.assertIs(effective_version(base, PatchSet()), base)

    def test_non_empty_appends_patch_marker(self):
        base = "abc123def456"
        eff = effective_version(base, PatchSet((spec_a(), spec_b())))
        self.assertTrue(eff.startswith(base + PATCH_SEP))
        suffix = eff[len(base) + len(PATCH_SEP):]
        self.assertEqual(len(suffix), 16)
        # 16 hex chars
        int(suffix, 16)

    def test_uses_first_16_of_full_id(self):
        ps = PatchSet((spec_a(),))
        eff = effective_version("base", ps)
        self.assertEqual(eff, "base" + PATCH_SEP + ps.patchset_id[:16])

    def test_base_independent_id(self):
        # The same patch set yields the same id regardless of base version.
        ps = PatchSet((spec_a(),))
        e1 = effective_version("v1", ps)
        e2 = effective_version("v2", ps)
        self.assertEqual(e1.split(PATCH_SEP)[1], e2.split(PATCH_SEP)[1])


class TestMd5File(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_matches_hashlib(self):
        p = os.path.join(self.d, "f.patch")
        data = b"diff --git a/x b/x\n+hello\n"
        with open(p, "wb") as fp:
            fp.write(data)
        self.assertEqual(md5_file(p), hashlib.md5(data).hexdigest())

    def test_known_value(self):
        p = os.path.join(self.d, "hello.txt")
        with open(p, "wb") as fp:
            fp.write(b"hello")
        self.assertEqual(md5_file(p), MD5_A)


class TestManifest(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_round_trip(self):
        ps = PatchSet((spec_a(), spec_b()))
        written = write_manifest(self.d, "abc123", "git", ps)
        # Manifest file lives at the pinned relative location.
        self.assertTrue(os.path.isfile(os.path.join(self.d, PATCH_MANIFEST_REL)))

        read = read_manifest(self.d)
        self.assertIsNotNone(read)
        self.assertEqual(read["ivpm_patch_version"], IVPM_PATCH_VERSION)
        self.assertEqual(read["base_version"], "abc123")
        self.assertEqual(read["base_src"], "git")
        self.assertEqual(read["patchset_id"], ps.patchset_id)
        self.assertEqual(len(read["applied"]), 2)
        self.assertEqual(read["applied"][0]["md5"], MD5_A)
        self.assertEqual(read["applied"][1]["directory"], "src")
        self.assertIn("applied_at", read)
        # write_manifest returns the same dict it persisted.
        self.assertEqual(written["patchset_id"], read["patchset_id"])

    def test_optional_base_and_result_blocks(self):
        ps = PatchSet((spec_a(),))
        base_ref = {"kind": "cache", "version": "abc123"}
        result = [{"path": "src/foo.c", "op": "modified", "sha256": "deadbeef"}]
        write_manifest(self.d, "abc123", "git", ps,
                       base_ref=base_ref, result=result)
        read = read_manifest(self.d)
        self.assertEqual(read["base"], base_ref)
        self.assertEqual(read["result"], result)

    def test_blocks_absent_by_default(self):
        write_manifest(self.d, "abc123", "git", PatchSet((spec_a(),)))
        read = read_manifest(self.d)
        self.assertNotIn("base", read)
        self.assertNotIn("result", read)

    def test_read_missing_returns_none(self):
        self.assertIsNone(read_manifest(self.d))


class TestManifestMatches(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ps = PatchSet((spec_a(), spec_b()))
        self.manifest = write_manifest(self.d, "abc123", "git", self.ps)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_true_on_identical(self):
        self.assertTrue(manifest_matches(self.manifest, "abc123", self.ps))

    def test_none_manifest(self):
        self.assertFalse(manifest_matches(None, "abc123", self.ps))

    def test_changed_base(self):
        self.assertFalse(manifest_matches(self.manifest, "different", self.ps))

    def test_changed_md5(self):
        changed = PatchSet((spec_a(md5=MD5_B), spec_b()))
        self.assertFalse(manifest_matches(self.manifest, "abc123", changed))

    def test_changed_order(self):
        reordered = PatchSet((spec_b(), spec_a()))
        self.assertFalse(manifest_matches(self.manifest, "abc123", reordered))

    def test_removed_member(self):
        fewer = PatchSet((spec_a(),))
        self.assertFalse(manifest_matches(self.manifest, "abc123", fewer))

    def test_added_member(self):
        more = PatchSet((spec_a(), spec_b(), spec_a(md5=MD5_B)))
        self.assertFalse(manifest_matches(self.manifest, "abc123", more))

    def test_applied_at_ignored(self):
        # A manifest with a different applied_at but the same identity matches.
        clone = dict(self.manifest)
        clone["applied_at"] = "1999-01-01T00:00:00+00:00"
        self.assertTrue(manifest_matches(clone, "abc123", self.ps))


class TestPatchCapability(unittest.TestCase):

    def test_tiers_ordered(self):
        self.assertEqual(PatchCapability.NONE.value, 0)
        self.assertEqual(PatchCapability.CACHE.value, 1)
        self.assertEqual(PatchCapability.EDITABLE.value, 2)


class TestPatchError(unittest.TestCase):

    def test_is_exception(self):
        self.assertTrue(issubclass(PatchError, Exception))


if __name__ == "__main__":
    unittest.main()
