"""
Tests for permission handling in PackageFile._install_zip.

zipfile records the unix mode in ZipInfo.external_attr but extract()/extractall()
do not apply it, so an extracted tool distribution came out non-executable --
protoc, verilator and every other gh-rls zip asset unpacked as unusable.

Coverage:
- the executable bit recorded in the archive is restored
- a plain data file keeps ordinary read permissions
- setuid/setgid/sticky bits from an archive are not honored
"""

import os
import shutil
import stat
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo

from ivpm.pkg_types.package_file import PackageFile


class TestZipPermissions(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.dest = os.path.join(self.root, "dest")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _pkg(self):
        pkg = PackageFile("sometool")
        pkg.src_type = ".zip"
        pkg.unpack = True
        return pkg

    def _mk_zip(self, entries):
        """entries: list of (name, content, mode) written with unix provenance."""
        path = os.path.join(self.root, "sometool.zip")
        with ZipFile(path, "w") as zf:
            for name, content, mode in entries:
                zi = ZipInfo(name)
                zi.create_system = 3          # unix
                zi.external_attr = mode << 16
                zf.writestr(zi, content)
        return path

    def test_executable_bit_restored(self):
        archive = self._mk_zip([("bin/sometool", "#!/bin/sh\necho hi\n", 0o100755)])
        self._pkg()._install(archive, self.dest)

        exe = os.path.join(self.dest, "bin", "sometool")
        self.assertTrue(os.path.isfile(exe))
        self.assertTrue(os.access(exe, os.X_OK), "executable bit not restored")
        self.assertEqual(0o755, os.stat(exe).st_mode & 0o777)

    def test_data_file_permissions(self):
        archive = self._mk_zip([("share/data.txt", "payload\n", 0o100644)])
        self._pkg()._install(archive, self.dest)

        data = os.path.join(self.dest, "share", "data.txt")
        self.assertEqual(0o644, os.stat(data).st_mode & 0o777)
        self.assertFalse(os.access(data, os.X_OK))

    def test_setuid_not_honored(self):
        archive = self._mk_zip([("bin/sneaky", "#!/bin/sh\n", 0o104755)])
        self._pkg()._install(archive, self.dest)

        exe = os.path.join(self.dest, "bin", "sneaky")
        mode = os.stat(exe).st_mode
        self.assertEqual(0o755, mode & 0o777)
        self.assertFalse(mode & stat.S_ISUID, "setuid bit honored from archive")


if __name__ == "__main__":
    unittest.main()
