"""
Tests for symlink and hard-link handling in PackageFile._install_tgz.

_install_tgz strips the archive's top-level wrapper directory from every
member name. A hard link's linkname is archive-root-relative and needs the
same strip; a symbolic link's target is relative to the link's own directory,
so both endpoints move together and the target must be left alone. Stripping
it shifted every relative target up one level, which made the filter='data'
sanitizer reject real source archives -- OpenROAD-flow-scripts and uv both
carry relative symlinks -- with LinkOutsideDestinationError, aborting the
whole update.

Coverage:
- a relative symlink that stays inside the archive survives extraction
- a symlink target pointing up out of the archive root is still rejected
- a hard link's archive-root-relative linkname is still stripped
"""

import os
import shutil
import tarfile
import tempfile
import unittest

from ivpm.pkg_types.package_file import PackageFile


class TestTgzSymlinks(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.src = os.path.join(self.root, "src")
        self.dest = os.path.join(self.root, "dest")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _pkg(self):
        pkg = PackageFile("somelib")
        pkg.src_type = ".tar.gz"
        pkg.unpack = True
        return pkg

    def _mk_tarball(self, build):
        """Build a tarball whose members all sit under a 'somelib-1.0/' root."""
        tree = os.path.join(self.src, "somelib-1.0")
        os.makedirs(tree)
        build(tree)
        tarball = os.path.join(self.root, "somelib.tar.gz")
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(tree, arcname="somelib-1.0")
        return tarball

    def test_relative_symlink_inside_archive(self):
        """The shape that broke real archives: deep link, target near the root."""
        def build(tree):
            # platforms/asap7/lef/cell.lef  <-  designs/asap7/wrapper/lef/cell.lef
            real_dir = os.path.join(tree, "platforms", "asap7", "lef")
            os.makedirs(real_dir)
            with open(os.path.join(real_dir, "cell.lef"), "w") as fp:
                fp.write("LEF\n")
            link_dir = os.path.join(tree, "designs", "asap7", "wrapper", "lef")
            os.makedirs(link_dir)
            os.symlink("../../../../platforms/asap7/lef/cell.lef",
                       os.path.join(link_dir, "cell.lef"))

        tarball = self._mk_tarball(build)
        self._pkg()._install(tarball, self.dest)

        link = os.path.join(self.dest, "designs", "asap7", "wrapper", "lef", "cell.lef")
        self.assertTrue(os.path.islink(link), "symlink not extracted")
        self.assertEqual("../../../../platforms/asap7/lef/cell.lef",
                         os.readlink(link),
                         "symlink target was rewritten")
        self.assertTrue(os.path.isfile(link), "symlink does not resolve")
        with open(link) as fp:
            self.assertEqual("LEF\n", fp.read())

    def test_escaping_symlink_still_rejected(self):
        """filter='data' must still refuse a target outside the destination."""
        def build(tree):
            os.makedirs(os.path.join(tree, "sub"))
            os.symlink("../../../../../etc/passwd",
                       os.path.join(tree, "sub", "escape"))

        tarball = self._mk_tarball(build)
        with self.assertRaises(tarfile.LinkOutsideDestinationError):
            self._pkg()._install(tarball, self.dest)

    def test_hard_link_root_relative_target_stripped(self):
        """A hard link's linkname is archive-root-relative: strip it."""
        def build(tree):
            with open(os.path.join(tree, "real.txt"), "w") as fp:
                fp.write("payload\n")
            os.makedirs(os.path.join(tree, "sub"))
            os.link(os.path.join(tree, "real.txt"),
                    os.path.join(tree, "sub", "hard.txt"))

        tarball = self._mk_tarball(build)
        self._pkg()._install(tarball, self.dest)

        hard = os.path.join(self.dest, "sub", "hard.txt")
        self.assertTrue(os.path.isfile(hard), "hard link not extracted")
        with open(hard) as fp:
            self.assertEqual("payload\n", fp.read())


if __name__ == "__main__":
    unittest.main()
