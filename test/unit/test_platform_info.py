"""
Unit tests for ``ivpm.platform_info`` -- the probe backing the ``ivpm_*``
manifest variables.

Two contracts here are worth more than the rest:

* ``arch`` is ``arm64`` on every OS, including Linux, where the gh-rls asset
  matcher's private ``_normalize_arch`` deliberately says ``aarch64``.
* the libc *family* is established before any version number is trusted, so
  musl is never reported as a very old glibc.
"""
import unittest
from unittest import mock

from ivpm import platform_info


class _ProbeTestBase(unittest.TestCase):

    def setUp(self):
        platform_info._reset_probe_cache()

    def tearDown(self):
        platform_info._reset_probe_cache()

    def _probe(self, system, machine, libc_ver=("", ""),
               os_release=None, ldd=""):
        """Probe with the whole environment faked."""
        with mock.patch.object(platform_info.platform, "system",
                               return_value=system), \
             mock.patch.object(platform_info.platform, "machine",
                               return_value=machine), \
             mock.patch.object(platform_info.platform, "libc_ver",
                               return_value=libc_ver), \
             mock.patch.object(platform_info, "_read_os_release",
                               return_value=os_release or {}), \
             mock.patch.object(platform_info, "_ldd_version_text",
                               return_value=ldd):
            platform_info._reset_probe_cache()
            return platform_info.probe()


class TestOsArch(_ProbeTestBase):

    def test_darwin_arm64(self):
        pi = self._probe("Darwin", "arm64")
        self.assertEqual("macos", pi.os)
        self.assertEqual("arm64", pi.arch)

    def test_linux_aarch64_is_arm64(self):
        # NOT 'aarch64': ${{ivpm_arch}} is canonical across OSes.
        pi = self._probe("Linux", "aarch64", libc_ver=("glibc", "2.39"))
        self.assertEqual("linux", pi.os)
        self.assertEqual("arm64", pi.arch)

    def test_linux_amd64_is_x86_64(self):
        pi = self._probe("Linux", "amd64", libc_ver=("glibc", "2.39"))
        self.assertEqual("x86_64", pi.arch)

    def test_windows(self):
        pi = self._probe("Windows", "AMD64")
        self.assertEqual("windows", pi.os)
        self.assertEqual("x86_64", pi.arch)
        # libc/distro are Linux-only facts
        self.assertEqual("", pi.libc)
        self.assertEqual("", pi.distro)

    def test_unknown_machine_passes_through(self):
        pi = self._probe("Linux", "RISCV64", libc_ver=("glibc", "2.39"))
        self.assertEqual("riscv64", pi.arch)


class TestLibc(_ProbeTestBase):

    def test_glibc_from_libc_ver(self):
        pi = self._probe("Linux", "x86_64", libc_ver=("glibc", "2.39"))
        self.assertEqual("glibc", pi.libc)
        self.assertEqual("2.39", pi.libc_version)

    def test_glibc_from_ldd_fallback(self):
        pi = self._probe(
            "Linux", "x86_64", libc_ver=("", ""),
            ldd="ldd (Ubuntu GLIBC 2.39-0ubuntu8.3) 2.39\n")
        self.assertEqual("glibc", pi.libc)
        self.assertEqual("2.39", pi.libc_version)

    def test_musl_from_os_release(self):
        pi = self._probe(
            "Linux", "x86_64", libc_ver=("", ""),
            os_release={"ID": "alpine", "VERSION_ID": "3.20.0"},
            ldd="musl libc (x86_64)\nVersion 1.2.5\n")
        self.assertEqual("musl", pi.libc)
        self.assertEqual("1.2.5", pi.libc_version)

    def test_musl_from_ldd_text(self):
        # The regression: a family-blind (\d+)\.(\d+) over this text reports
        # glibc 1.2. Nothing here may say 'glibc'.
        pi = self._probe(
            "Linux", "x86_64", libc_ver=("", ""),
            ldd="musl libc (x86_64)\nVersion 1.2.4\n")
        self.assertEqual("musl", pi.libc)
        self.assertEqual("1.2.4", pi.libc_version)
        self.assertNotEqual("glibc", pi.libc)

    def test_musl_version_not_taken_from_arch_string(self):
        # No 'Version' line at all: still musl, and no version invented from
        # the '(x86_64)' text.
        pi = self._probe(
            "Linux", "x86_64", libc_ver=("", ""),
            ldd="musl libc (x86_64)\n")
        self.assertEqual("musl", pi.libc)
        self.assertEqual("", pi.libc_version)

    def test_ldd_absent(self):
        pi = self._probe("Linux", "x86_64", libc_ver=("", ""), ldd="")
        self.assertEqual("", pi.libc)
        self.assertEqual("", pi.libc_version)

    def test_ldd_raises(self):
        with mock.patch.object(platform_info.subprocess, "run",
                               side_effect=OSError("no ldd")):
            self.assertEqual("", platform_info._ldd_version_text())


class TestDistro(_ProbeTestBase):

    def test_distro_from_os_release(self):
        pi = self._probe(
            "Linux", "x86_64", libc_ver=("glibc", "2.39"),
            os_release={"ID": "ubuntu", "VERSION_ID": "24.04"})
        self.assertEqual("ubuntu", pi.distro)
        self.assertEqual("24.04", pi.distro_version)

    def test_distro_unknown_is_empty(self):
        pi = self._probe("Linux", "x86_64", libc_ver=("glibc", "2.39"))
        self.assertEqual("", pi.distro)
        self.assertEqual("", pi.distro_version)


class TestAsVariables(_ProbeTestBase):

    def test_all_values_are_str(self):
        for pi in (
                self._probe("Linux", "x86_64", libc_ver=("glibc", "2.39"),
                            os_release={"ID": "ubuntu", "VERSION_ID": "24.04"}),
                self._probe("Darwin", "arm64"),
                self._probe("Windows", "AMD64"),
                self._probe("Linux", "x86_64", libc_ver=("", ""))):
            vars_ = platform_info.as_variables(pi)
            for name, val in vars_.items():
                self.assertIsInstance(val, str, name)
                self.assertNotEqual("None", val, name)

    def test_variable_names(self):
        vars_ = platform_info.as_variables(self._probe("Darwin", "arm64"))
        self.assertEqual({
            "ivpm_os", "ivpm_arch", "ivpm_platform",
            "ivpm_libc", "ivpm_libc_version",
            "ivpm_libc_major", "ivpm_libc_minor",
            "ivpm_distro", "ivpm_distro_version"}, set(vars_.keys()))

    def test_libc_major_minor(self):
        pi = self._probe("Linux", "x86_64", libc_ver=("glibc", "2.39"))
        vars_ = platform_info.as_variables(pi)
        self.assertEqual("2", vars_["ivpm_libc_major"])
        self.assertEqual("39", vars_["ivpm_libc_minor"])

    def test_libc_major_minor_unknown(self):
        vars_ = platform_info.as_variables(self._probe("Darwin", "arm64"))
        self.assertEqual("", vars_["ivpm_libc_major"])
        self.assertEqual("", vars_["ivpm_libc_minor"])

    def test_platform_is_os_dash_arch(self):
        vars_ = platform_info.as_variables(self._probe("Darwin", "arm64"))
        self.assertEqual("macos-arm64", vars_["ivpm_platform"])


if __name__ == "__main__":
    unittest.main()
