import os
import tempfile
import unittest

from ivpm.handlers.envrc_sections import (
    ENVRC_HEADER, patch_envrc_section, remove_envrc_section)

_BEGIN = "# --- ivpm:x begin ---"
_END   = "# --- ivpm:x end ---"


class TestEnvrcSections(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.deps_dir = self._tmp.name
        self.path = os.path.join(self.deps_dir, "packages.envrc")

    def tearDown(self):
        self._tmp.cleanup()

    def _read(self):
        with open(self.path) as fp:
            return fp.read()

    def _write(self, content):
        with open(self.path, "w") as fp:
            fp.write(content)

    def test_creates_missing_file(self):
        patch_envrc_section(self.deps_dir, _BEGIN, _END, "source_env ./x\n")
        content = self._read()
        self.assertTrue(content.startswith(ENVRC_HEADER))
        self.assertIn("%s\nsource_env ./x\n%s\n" % (_BEGIN, _END), content)

    def test_replace_is_idempotent(self):
        self._write(ENVRC_HEADER + "export IVPM_PACKAGES=/p\n")
        patch_envrc_section(self.deps_dir, _BEGIN, _END, "source_env ./a\n")
        patch_envrc_section(self.deps_dir, _BEGIN, _END, "source_env ./b\n")
        content = self._read()
        self.assertEqual(content.count(_BEGIN), 1)
        self.assertNotIn("./a", content)
        self.assertIn("./b", content)

    def test_remove_restores_original(self):
        base = ENVRC_HEADER + "export IVPM_PACKAGES=/p\n"
        self._write(base)
        patch_envrc_section(self.deps_dir, _BEGIN, _END, "source_env ./x\n")
        remove_envrc_section(self.deps_dir, _BEGIN, _END)
        self.assertEqual(self._read(), base)

    def test_remove_keeps_adjacent_line(self):
        self._write("a\n%s\nsource_env ./x\n%s\nb\n" % (_BEGIN, _END))
        remove_envrc_section(self.deps_dir, _BEGIN, _END)
        self.assertEqual(self._read(), "a\nb\n")

    def test_remove_missing_file_is_noop(self):
        remove_envrc_section(self.deps_dir, _BEGIN, _END)
        self.assertFalse(os.path.exists(self.path))
