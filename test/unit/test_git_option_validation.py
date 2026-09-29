"""Git dependency options must be combined legally.

branch/tag/commit each select what to check out, and nothing used to check
them against each other: ``branch`` + ``tag`` silently cloned the branch and
ignored the tag. These tests pin down which combinations the reader accepts.
"""
import io
import unittest

from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.pkg_types.package_git import PackageGit


def _parse_dep(dep_lines):
    text = (
        "package:\n"
        "  name: mypkg\n"
        "  dep-sets:\n"
        "    - name: default\n"
        "      deps:\n"
        "        - name: foo\n"
        "          url: https://github.com/x/y.git\n"
        + "".join("          %s\n" % line for line in dep_lines))
    info = IvpmYamlReader().read(io.StringIO(text), "ivpm.yaml")
    return info.dep_set_m["default"].packages["foo"]


class TestGitOptionValidation(unittest.TestCase):

    def _assert_fatal(self, dep_lines, *fragments):
        with self.assertRaises(Exception) as ctx:
            _parse_dep(dep_lines)
        msg = str(ctx.exception)
        for frag in fragments:
            self.assertIn(frag, msg)
        # Errors point at the offending ivpm.yaml entry.
        self.assertIn("ivpm.yaml:", msg)

    # -- legal ----------------------------------------------------------- #

    def test_single_selectors_accepted(self):
        for line in ("branch: main", "tag: v1.0", "commit: abc1234"):
            pkg = _parse_dep([line])
            self.assertIsInstance(pkg, PackageGit)

    def test_branch_and_commit_accepted(self):
        pkg = _parse_dep(["branch: main", "commit: abc1234"])
        self.assertEqual("main", pkg.branch)
        self.assertEqual("abc1234", pkg.commit)

    def test_cache_with_depth_1_accepted(self):
        # Redundant (cached packages are always depth 1), and documented.
        pkg = _parse_dep(["cache: true", "depth: 1"])
        self.assertEqual(1, pkg.depth)

    def test_uncached_depth_accepted(self):
        pkg = _parse_dep(["cache: false", "depth: 10"])
        self.assertEqual(10, pkg.depth)

    def test_consistent_transport_options_accepted(self):
        _parse_dep(["ssh: false", "anonymous: true"])
        _parse_dep(["ssh: true", "anonymous: false"])

    def test_lock_entry_with_null_keys_accepted(self):
        # `ivpm sync` replays package-lock entries, which carry every key,
        # through process_options with None for the unset ones.
        pkg = PackageGit("foo")
        pkg.process_options({
            "url": "https://github.com/x/y.git", "src": "git",
            "branch": None, "tag": "v1.0", "commit_requested": None,
            "cache": None}, None)
        self.assertEqual("v1.0", pkg.tag)

    # -- ref selectors --------------------------------------------------- #

    def test_branch_and_tag_rejected(self):
        self._assert_fatal(["branch: main", "tag: v1.0"],
                           "'branch'", "'tag'", "mutually exclusive")

    def test_tag_and_commit_rejected(self):
        self._assert_fatal(["tag: v1.0", "commit: abc1234"],
                           "'tag'", "'commit'", "mutually exclusive")

    def test_all_three_rejected(self):
        self._assert_fatal(["branch: main", "tag: v1.0", "commit: abc1234"],
                           "mutually exclusive")

    def test_numeric_tag_rejected_with_quoting_hint(self):
        # YAML reads 1.10 as the float 1.1 -- already the wrong tag.
        self._assert_fatal(["tag: 1.10"], "'tag' must be a string", "Quote")

    def test_numeric_commit_rejected(self):
        self._assert_fatal(["commit: 1234567"], "'commit' must be a string")

    def test_empty_branch_rejected(self):
        self._assert_fatal(['branch: ""'], "'branch' is empty")

    # -- depth ----------------------------------------------------------- #

    def test_non_positive_depth_rejected(self):
        self._assert_fatal(["depth: 0"], "positive integer")
        self._assert_fatal(["depth: -1"], "positive integer")

    def test_non_integer_depth_rejected(self):
        self._assert_fatal(["depth: shallow"], "positive integer")
        self._assert_fatal(["depth: true"], "positive integer")

    def test_cache_with_deeper_depth_rejected(self):
        self._assert_fatal(["cache: true", "depth: 10"],
                           "'depth' (10)", "cache: true")

    # -- transport ------------------------------------------------------- #

    def test_ssh_and_anonymous_rejected(self):
        self._assert_fatal(["ssh: true", "anonymous: true"],
                           "'ssh: true'", "'anonymous: true'")


if __name__ == "__main__":
    unittest.main()
