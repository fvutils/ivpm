import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.clone.clone_provider import (
    CloneProvider, CloneOption, ClaimStrength,
    build_parser_from_options, options_to_paraminfo,
)
from ivpm.clone.clone_provider_rgy import CloneProviderRgy
from ivpm.show.info_types import CloneProviderInfo
from ivpm.diagnostics import SrcLoaderError


def _mk(name, schemes=(), claim_map=None, options=None, is_default=False):
    """Build a fake provider class with the given identity/claims."""
    class _Fake(CloneProvider):
        @classmethod
        def provider_info(cls):
            return CloneProviderInfo(
                name=cls._name, description="fake %s" % cls._name,
                schemes=list(cls._schemes), is_default=cls._is_default)

        def schemes(self):
            return list(self._schemes)

        def claim(self, src):
            for pat, strength in self._claim_map.items():
                if pat in src:
                    return strength
            return ClaimStrength.NONE

        def options(self):
            return list(self._options)

        def clone(self, req):
            from ivpm.clone.clone_provider import CloneResult
            return CloneResult(ok=True)

    _Fake._name = name
    _Fake._schemes = list(schemes)
    _Fake._claim_map = claim_map or {}
    _Fake._options = options or []
    _Fake._is_default = is_default
    return _Fake()


class TestCloneProviderRgy(unittest.TestCase):

    # --- scheme / claim resolution ------------------------------------- #

    def test_scheme_match_wins(self):
        rgy = CloneProviderRgy()
        myvcs = _mk("myvcs", schemes=["myvcs"])
        # git-like fallback also present, would claim WEAK
        git = _mk("git", claim_map={"://": ClaimStrength.WEAK})
        rgy.register(git)
        rgy.register(myvcs)
        self.assertIs(rgy.resolve("myvcs://repo"), myvcs)

    def test_strong_beats_weak(self):
        rgy = CloneProviderRgy()
        git = _mk("git", claim_map={"://": ClaimStrength.WEAK})
        mine = _mk("mine", claim_map={"myserver": ClaimStrength.STRONG})
        rgy.register(git)
        rgy.register(mine)
        self.assertIs(rgy.resolve("https://myserver/x"), mine)

    def test_weak_fallback_used(self):
        rgy = CloneProviderRgy()
        git = _mk("git", claim_map={"://": ClaimStrength.WEAK})
        mine = _mk("mine", claim_map={"myserver": ClaimStrength.STRONG})
        rgy.register(git)
        rgy.register(mine)
        self.assertIs(rgy.resolve("https://github.com/a/b"), git)

    def test_two_strong_is_ambiguous(self):
        rgy = CloneProviderRgy()
        a = _mk("aprov", claim_map={"myserver": ClaimStrength.STRONG})
        b = _mk("bprov", claim_map={"myserver": ClaimStrength.STRONG})
        rgy.register(a)
        rgy.register(b)
        with self.assertRaises(SrcLoaderError) as cm:
            rgy.resolve("https://myserver/x")
        self.assertIn("aprov", str(cm.exception))
        self.assertIn("bprov", str(cm.exception))

    def test_unknown_scheme_falls_through_to_claim(self):
        rgy = CloneProviderRgy()
        git = _mk("git", claim_map={"://": ClaimStrength.WEAK})
        rgy.register(git)
        # 'https' scheme is owned by nobody -> claim step -> git WEAK
        self.assertIs(rgy.resolve("https://x/y"), git)

    def test_no_match_is_fatal(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk("myvcs", schemes=["myvcs"]))
        with self.assertRaises(SrcLoaderError):
            rgy.resolve("https://nobody/claims/this")

    # --- explicit --provider override ---------------------------------- #

    def test_forced_provider(self):
        rgy = CloneProviderRgy()
        git = _mk("git", claim_map={"://": ClaimStrength.WEAK})
        myvcs = _mk("myvcs", schemes=["myvcs"])
        rgy.register(git)
        rgy.register(myvcs)
        # git-shaped URL, but force myvcs
        self.assertIs(rgy.resolve("https://x/y", forced="myvcs"), myvcs)

    def test_forced_unknown_is_fatal(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk("git", claim_map={"://": ClaimStrength.WEAK}))
        with self.assertRaises(SrcLoaderError) as cm:
            rgy.resolve("https://x", forced="nope")
        self.assertIn("nope", str(cm.exception))

    # --- registration guards ------------------------------------------- #

    def test_duplicate_scheme_registration_fatal(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk("a", schemes=["dup"]))
        with self.assertRaises(Exception):
            rgy.register(_mk("b", schemes=["dup"]))

    def test_duplicate_name_registration_fatal(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk("same"))
        with self.assertRaises(Exception):
            rgy.register(_mk("same"))

    # --- provenance stamping ------------------------------------------- #

    def test_provenance_stamped_and_stored(self):
        rgy = CloneProviderRgy()
        rgy.register(_mk("plug", claim_map={"x": ClaimStrength.STRONG}),
                     provenance={"origin": "plugdist", "provider": "plugdist",
                                 "version": "1.2.0"})
        info = rgy.info_for("plug")
        self.assertEqual(info.provider, "plugdist")
        self.assertEqual(info.version, "1.2.0")
        self.assertEqual(info.origin, "plugdist")


class TestCloneOptionParsing(unittest.TestCase):

    def _opts(self):
        return [
            CloneOption(flags=["-branch"], dest="branch",
                        help="Branch to check out", required=True, metavar="VALUE"),
            CloneOption(flags=["-node"], dest="node",
                        help="Build node", default="head", metavar="VALUE"),
            CloneOption(flags=["--sparse"], dest="sparse",
                        help="Sparse checkout", is_flag=True),
        ]

    def test_build_parser_roundtrip(self):
        p = build_parser_from_options("ivpm clone myvcs", self._opts())
        ns = p.parse_args(["-branch", "abc", "-node", "xyz", "--sparse"])
        self.assertEqual(ns.branch, "abc")
        self.assertEqual(ns.node, "xyz")
        self.assertTrue(ns.sparse)

    def test_defaults_and_flag(self):
        p = build_parser_from_options("ivpm clone myvcs", self._opts())
        ns = p.parse_args(["-branch", "abc"])
        self.assertEqual(ns.node, "head")
        self.assertFalse(ns.sparse)

    def test_required_enforced(self):
        p = build_parser_from_options("ivpm clone myvcs", self._opts())
        with self.assertRaises(SystemExit):
            p.parse_args(["-node", "xyz"])

    def test_options_to_paraminfo_parity(self):
        params = options_to_paraminfo(self._opts())
        by_name = {pi.name: pi for pi in params}
        self.assertIn("-branch", by_name)
        self.assertTrue(by_name["-branch"].required)
        self.assertEqual(by_name["-node"].default, "head")
        self.assertEqual(by_name["--sparse"].type_hint, "bool")
        # switch default (False) is not surfaced as a spurious value
        self.assertIsNone(by_name["--sparse"].default)


if __name__ == "__main__":
    unittest.main()
