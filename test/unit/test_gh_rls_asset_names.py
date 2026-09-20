"""Offline unit tests for gh-rls asset-name heuristics.

These exercise the pure name-parsing/selection helpers directly, with no
network access, so the naming conventions ivpm claims to support are pinned
down by tests rather than by whichever upstream project happened to be tried
by hand.

Regression focus: `linux-<flavor>-<arch>` names (Verible's
`...-linux-static-x86_64.tar.gz`) used to parse as arch `static`, which is not
a recognized arch, so `_select_linux_asset` returned None, `_has_binary_assets`
reported False, and ivpm silently downloaded the *source* tarball instead.
"""

import unittest

from ivpm.pkg_types.package_gh_rls import PackageGhRls


def _assets(*names):
    return [
        {"name": n, "browser_download_url": "https://example.com/dl/" + n}
        for n in names
    ]


VERIBLE_ASSETS = _assets(
    "verible-v0.0-4294-gc1d8f5e8-linux-static-arm64.tar.gz",
    "verible-v0.0-4294-gc1d8f5e8-linux-static-x86_64.tar.gz",
    "verible-v0.0-4294-gc1d8f5e8-macOS.tar.gz",
    "verible-v0.0-4294-gc1d8f5e8-win64.zip",
    "verible-v0.0-4294-gc1d8f5e8.tar.gz",
)


class TestParseLinuxGeneric(unittest.TestCase):

    def setUp(self):
        self.pkg = PackageGhRls("t")

    def check(self, name, expected):
        self.assertEqual(self.pkg._parse_linux_generic(name), expected, name)

    def test_arch_immediately_after_linux(self):
        # protobuf style -- the shape that already worked.
        self.check("protoc-33.2-linux-x86_64.zip", "x86_64")
        self.check("protoc-33.2-linux-aarch_64.zip", "aarch64")
        self.check("tool-1.0-linux_x86_64.tar.gz", "x86_64")
        self.check("tool-1.0-linux-arm64.tar.gz", "aarch64")
        self.check("tool-1.0-linux-amd64.tar.gz", "x86_64")
        self.check("tool-1.0-linux-x64.tar.gz", "x86_64")
        self.check("tool-1.0-linux-armv7l.tar.gz", "armv7l")

    def test_arch_after_a_build_flavor(self):
        # The regression: a flavor token sits between `linux` and the arch.
        self.check("verible-v0.0-4294-gc1d8f5e8-linux-static-x86_64.tar.gz", "x86_64")
        self.check("verible-v0.0-4294-gc1d8f5e8-linux-static-arm64.tar.gz", "aarch64")
        self.check("tool-1.0-linux-musl-aarch64.tar.gz", "aarch64")
        self.check("tool-1.0-linux-gnu-x86_64.tar.gz", "x86_64")

    def test_unknown_arch_falls_back_to_first_token(self):
        # No alias for these; historical "token right after linux" behavior is
        # what lets them still match _normalize_arch's passthrough.
        self.check("tool-1.0-linux-ppc64le.tar.gz", "ppc64le")
        self.check("tool-1.0-linux-riscv64.tar.gz", "riscv64")

    def test_non_linux_assets(self):
        self.check("verible-v0.0-4294-gc1d8f5e8-macOS.tar.gz", None)
        self.check("verible-v0.0-4294-gc1d8f5e8-win64.zip", None)
        self.check("verible-v0.0-4294-gc1d8f5e8.tar.gz", None)


class TestVeribleAssetSelection(unittest.TestCase):
    """End-to-end over the selection helpers, using Verible's real asset set."""

    def setUp(self):
        self.pkg = PackageGhRls("verible")

    def test_has_binary_assets(self):
        self.assertTrue(self.pkg._has_binary_assets(VERIBLE_ASSETS))

    def test_selects_x86_64(self):
        sel = self.pkg._select_linux_asset(VERIBLE_ASSETS, "x86_64", (2, 17))
        self.assertIsNotNone(sel)
        self.assertIn("linux-static-x86_64", sel["name"])

    def test_selects_aarch64(self):
        sel = self.pkg._select_linux_asset(VERIBLE_ASSETS, "aarch64", (2, 17))
        self.assertIsNotNone(sel)
        self.assertIn("linux-static-arm64", sel["name"])

    def test_source_tarball_is_not_selected_as_a_binary(self):
        for arch in ("x86_64", "aarch64"):
            sel = self.pkg._select_linux_asset(VERIBLE_ASSETS, arch, (2, 17))
            self.assertNotEqual(sel["name"], "verible-v0.0-4294-gc1d8f5e8.tar.gz")

    def test_macos_and_windows_still_select(self):
        mac = self.pkg._select_macos_asset(VERIBLE_ASSETS, "arm64")
        self.assertIsNotNone(mac)
        self.assertIn("macOS", mac["name"])
        win = self.pkg._select_windows_asset(VERIBLE_ASSETS, "x86_64")
        self.assertIsNotNone(win)
        self.assertIn("win64", win["name"])


class TestEdapackAssetSelection(unittest.TestCase):
    """The names verible-bin itself publishes must select cleanly too."""

    ASSETS = _assets(
        "verible-bin-linux-x86_64-0.0-4294-gc1d8f5e8.tar.gz",
        "verible-bin-linux-aarch64-0.0-4294-gc1d8f5e8.tar.gz",
        "verible-bin-macos-arm64-0.0-4294-gc1d8f5e8.tar.gz",
        "verible-bin-win64-x86_64-0.0-4294-gc1d8f5e8.tar.gz",
        "verible-bin-linux-x86_64-0.0-4294-gc1d8f5e8.tar.gz.sha256",
        "manifest.json",
    )

    def setUp(self):
        self.pkg = PackageGhRls("verible-bin")
        self.assets = [
            a for a in self.ASSETS
            if not self.pkg._is_checksum_or_sig(a["name"])
        ]

    def test_linux(self):
        for arch, want in (("x86_64", "linux-x86_64"), ("aarch64", "linux-aarch64")):
            sel = self.pkg._select_linux_asset(self.assets, arch, (2, 17))
            self.assertIsNotNone(sel, arch)
            self.assertIn(want, sel["name"])

    def test_macos(self):
        sel = self.pkg._select_macos_asset(self.assets, "arm64")
        self.assertIsNotNone(sel)
        self.assertIn("macos-arm64", sel["name"])

    def test_windows(self):
        sel = self.pkg._select_windows_asset(self.assets, "x86_64")
        self.assertIsNotNone(sel)
        self.assertIn("win64", sel["name"])


if __name__ == "__main__":
    unittest.main()
