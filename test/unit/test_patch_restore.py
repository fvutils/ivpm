#****************************************************************************
#* test_patch_restore.py
#*
#* Phase 5 -- source-provider pristine restoration: git retain_base/
#* restore_pristine (reset+clean with the safety check) and archive
#* retain_base/restore_pristine (retain + re-extract). Plus the generic
#* _reestablish round-trip. Hermetic: a real *local* git repo (no network)
#* and a locally-built tar.gz.
#****************************************************************************
import os
import io
import types
import shutil
import tarfile
import tempfile
import subprocess
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.patch import (
    PatchSpec, PatchSet, PatchCapability,
    md5_file, apply_patchset, write_manifest, read_manifest, _reestablish,
)
from ivpm.pkg_types.package_git import PackageGit
from ivpm.pkg_types.package_http import PackageHttp

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch")
HAS_GIT = shutil.which("git") is not None


def mkspec(patch_name, strip=1, directory=None):
    p = os.path.join(DATA, patch_name)
    return PatchSpec(name=patch_name, source=patch_name, resolved_path=p,
                     md5=md5_file(p), strip=strip, directory=directory)


def read(path):
    with open(path) as fp:
        return fp.read()


def write(path, text):
    with open(path, "w") as fp:
        fp.write(text)


@unittest.skipUnless(HAS_GIT, "git not available")
class TestGitRestore(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pkg_dir = os.path.join(self.root, "somelib")
        shutil.copytree(os.path.join(DATA, "srctree"), self.pkg_dir)
        env = dict(os.environ,
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self._env = env
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")
        self.base = self._git("rev-parse", "HEAD").strip()
        self.pkg = PackageGit(name="somelib")
        self.pkg.src_type = "git"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.pkg_dir,
                           capture_output=True, text=True, env=self._env)
        return r.stdout

    def _apply_and_record(self):
        # Mirror a first editable apply: retain_base, apply, write the manifest
        # (with the git base provenance and a result[] naming the patched file).
        self.pkg.retain_base(self.pkg_dir, self.base, None)
        apply_patchset(self.pkg_dir, PatchSet((mkspec("fix.patch"),)), self.base, self.pkg)
        write_manifest(self.pkg_dir, self.base, "git",
                       PatchSet((mkspec("fix.patch"),)),
                       base_ref=self.pkg._base_ref,
                       result=[{"path": "hello.txt", "op": "modified", "sha256": "x"}])

    def test_capability_is_editable(self):
        self.assertEqual(self.pkg.patch_capability(), PatchCapability.EDITABLE)

    def test_retain_base_records_git_ref(self):
        self.pkg.retain_base(self.pkg_dir, self.base, None)
        self.assertEqual(self.pkg._base_ref, {"kind": "git", "version": self.base})

    def test_restore_after_apply(self):
        self._apply_and_record()
        # A metadata marker must survive the restore.
        os.makedirs(os.path.join(self.pkg_dir, ".ivpm"), exist_ok=True)
        write(os.path.join(self.pkg_dir, ".ivpm", "marker"), "keep")
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")

        ok = self.pkg.restore_pristine(self.pkg_dir, self.base, None)
        self.assertTrue(ok)
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello\n")
        # .ivpm/ preserved.
        self.assertEqual(read(os.path.join(self.pkg_dir, ".ivpm", "marker")), "keep")

    def test_restore_refuses_unrelated_edit(self):
        self._apply_and_record()
        # An edit OUTSIDE the recorded result[] -> unsafe -> refuse.
        write(os.path.join(self.pkg_dir, "ctx.txt"), "user was here\n")

        ok = self.pkg.restore_pristine(self.pkg_dir, self.base, None)
        self.assertFalse(ok)
        # Tree untouched -- both the patch and the user edit remain.
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")
        self.assertEqual(read(os.path.join(self.pkg_dir, "ctx.txt")), "user was here\n")

    def test_reestablish_round_trip(self):
        self._apply_and_record()
        ui = types.SimpleNamespace()
        _reestablish(ui, self.pkg, self.pkg_dir, self.base, PatchSet((mkspec("fix.patch"),)))
        # Restored to pristine then re-applied: patched content present...
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello patched\n")
        # ...and the manifest result[] was recomputed from the actual diff.
        m = read_manifest(self.pkg_dir)
        mods = [r for r in m["result"] if r["path"] == "hello.txt"]
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["op"], "modified")
        self.assertEqual(m["base"], {"kind": "git", "version": self.base})


def _build_targz(path, files):
    """Build a .tar.gz with a top-level dir (as real release archives have)."""
    with tarfile.open(path, "w:gz") as tf:
        for rel, content in files.items():
            data = content.encode()
            ti = tarfile.TarInfo("pkg-1.0/" + rel)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))


class TestArchiveRestore(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.archive = os.path.join(self.root, "pkg-1.0.tar.gz")
        _build_targz(self.archive, {"hello.txt": "hello\n", "ctx.txt": "ctx\n"})
        self.pkg_dir = os.path.join(self.root, "somelib")
        self.pkg = PackageHttp(name="somelib")
        self.pkg.src_type = ".tar.gz"
        # Materialize the pristine tree (strip-top-dir extraction).
        self.pkg._install(self.archive, self.pkg_dir)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_capability_is_editable(self):
        self.assertEqual(self.pkg.patch_capability(), PatchCapability.EDITABLE)

    def test_retain_writes_base_with_md5(self):
        self.pkg._pristine_archive = self.archive
        self.pkg.retain_base(self.pkg_dir, "v1", None)
        base = os.path.join(self.pkg_dir, ".ivpm", "base.tar.gz")
        self.assertTrue(os.path.isfile(base))
        self.assertEqual(self.pkg._base_ref["kind"], "archive")
        self.assertEqual(self.pkg._base_ref["md5"], md5_file(base))

    def test_restore_reextracts_byte_identical(self):
        self.pkg._pristine_archive = self.archive
        self.pkg.retain_base(self.pkg_dir, "v1", None)
        # Simulate a patch + a metadata marker.
        write(os.path.join(self.pkg_dir, "hello.txt"), "hello patched\n")
        write(os.path.join(self.pkg_dir, ".ivpm", "marker"), "keep")

        ok = self.pkg.restore_pristine(self.pkg_dir, "v1", None)
        self.assertTrue(ok)
        self.assertEqual(read(os.path.join(self.pkg_dir, "hello.txt")), "hello\n")
        # .ivpm/ (marker + retained base) preserved.
        self.assertEqual(read(os.path.join(self.pkg_dir, ".ivpm", "marker")), "keep")
        self.assertTrue(os.path.isfile(os.path.join(self.pkg_dir, ".ivpm", "base.tar.gz")))

    def test_restore_without_retained_base_returns_false(self):
        # No retain_base call -> nothing to restore from.
        self.assertFalse(self.pkg.restore_pristine(self.pkg_dir, "v1", None))


if __name__ == "__main__":
    unittest.main()
