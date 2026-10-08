"""R3: the manifest helpers on ClonePrepareRequest -- load_root_package(),
selected_dep_sets() and config() -- read through req.read_file(), with no
checkout and nothing printed."""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.clone.clone_preparer import (
    ClonePreparer, ClonePrepareRequest, ClonePrepareResult, CloneDenied,
    ManifestLoadError)
from ivpm.clone.clone_preparer_list import ClonePreparerList
from ivpm.clone.clone_preparer_rgy import (
    ClonePreparerRgy, ClonePreparerLoadError)
from ivpm.proj_info import ProjInfo


class _Acme(ClonePreparer):
    name = "acme"


class _Other(ClonePreparer):
    name = "other"


def _register(*classes):
    rgy = ClonePreparerRgy()
    for c in classes:
        rgy.addPreparer(c)
    ClonePreparerRgy._inst = rgy
    return rgy


class _Source(object):
    """An in-memory source tree standing in for provider.read_source_file."""

    def __init__(self, files):
        self.files = dict(files)
        self.reads = []

    def __call__(self, path):
        self.reads.append(path)
        return self.files.get(path)


ROOT_WITH = """\
package:
  name: p
  vars:
    grp: eng
  with:
    acme:
      group: "${{grp}}"
      mode: "2770"
  dep-sets:
    - name: default-dev
      deps: []
    - name: release
      with:
        acme:
          mode: "2750"
      deps: []
    - name: secure
      with:
        acme:
          acl: strict
      deps: []
"""


class _Base(TestBase):

    def setUp(self):
        super().setUp()
        _register(_Acme, _Other)
        self.target = os.path.join(self.testdir, "ws")

    def tearDown(self):
        ClonePreparerRgy._inst = None
        super().tearDown()

    def _req(self, files=None, dep_set=None, cli_overrides=None,
             read_file=None):
        self.src = _Source(files or {})
        kw = {}
        if read_file is not None:
            kw["read_file"] = read_file
        elif files is not None:
            kw["read_file"] = self.src
        return ClonePrepareRequest(
            src="file:///repo", url="file:///repo", provider="git",
            target_dir=self.target, target_nonempty=False,
            branch=None, tag=None, revision=None,
            dep_set=dep_set, cli_overrides=cli_overrides or {}, args=None,
            **kw)

    @contextlib.contextmanager
    def _quiet(self):
        """Assert nothing reaches stdout or stderr inside the block."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield
        self.assertEqual("", out.getvalue())
        self.assertEqual("", err.getvalue())


class TestLoadRootPackage(_Base):

    def test_no_manifest(self):
        req = self._req({"README": "x"})
        self.assertIsNone(req.load_root_package())
        self.assertIsNone(req.selected_dep_sets())
        self.assertEqual({}, req.config("acme"))

    def test_provider_cannot_read(self):
        req = self._req()       # the base provider's read_file: always None
        self.assertIsNone(req.load_root_package())
        self.assertEqual({}, req.config("acme"))

    def test_nested_includes_merged(self):
        req = self._req({
            "ivpm.yaml": "package:\n  name: p\n  include: [cfg/a.yaml]\n"
                         "  dep-sets:\n    - name: main\n      deps: []\n",
            "cfg/a.yaml": "package:\n  include: [b.yaml]\n"
                          "  dep-sets:\n    - name: from-a\n      deps: []\n",
            "cfg/b.yaml": "package:\n"
                          "  dep-sets:\n    - name: from-b\n      deps: []\n",
        })
        with self._quiet():
            info = req.load_root_package()
        self.assertIsInstance(info, ProjInfo)
        self.assertEqual("p", info.name)
        self.assertEqual({"main", "from-a", "from-b"}, set(info.dep_set_m))
        # Paths are presented where the files will land after the clone.
        self.assertEqual(os.path.join(self.target, "ivpm.yaml"),
                         info.manifest_file)

    def test_missing_include(self):
        req = self._req({
            "ivpm.yaml": "package:\n  name: p\n  include: [nope.yaml]\n"})
        with self._quiet():
            with self.assertRaises(ManifestLoadError) as ctx:
                req.load_root_package()
        self.assertIn("nope.yaml", str(ctx.exception))
        self.assertIn("does not exist", str(ctx.exception))

    def test_include_escaping_the_source_tree(self):
        req = self._req({
            "ivpm.yaml": "package:\n  name: p\n  include: [../outside.yaml]\n"})
        with self._quiet():
            with self.assertRaises(ManifestLoadError):
                req.load_root_package()
        self.assertEqual(["ivpm.yaml"], self.src.reads)

    def test_absolute_include(self):
        outside = os.path.join(self.testdir, "outside.yaml")
        with open(outside, "w") as f:
            f.write("package:\n  dep-sets: []\n")
        req = self._req({
            "ivpm.yaml": "package:\n  name: p\n  include: [%s]\n" % outside})
        with self._quiet():
            with self.assertRaises(ManifestLoadError):
                req.load_root_package()

    def test_malformed_yaml(self):
        req = self._req({"ivpm.yaml": "package:\n  name: [unclosed\n"})
        with self._quiet():
            with self.assertRaises(ManifestLoadError) as ctx:
                req.load_root_package()
        self.assertIn("ivpm.yaml", str(ctx.exception))

    def test_failure_leaves_global_reporter_untouched(self):
        from ivpm.msg import get_reporter
        rep = get_reporter()
        n_err, n_warn = rep.error_count, rep.warning_count
        req = self._req({"ivpm.yaml": "package:\n  name: [unclosed\n"})
        with self.assertRaises(ManifestLoadError):
            req.load_root_package()
        self.assertIs(rep, get_reporter())
        self.assertEqual((n_err, n_warn), (rep.error_count, rep.warning_count))

    def test_cli_definitions_reach_variables(self):
        req = self._req({"ivpm.yaml": ROOT_WITH},
                        cli_overrides={"grp": "secure-eng"})
        self.assertEqual("secure-eng", req.config("acme")["group"])

    def test_loaded_once(self):
        req = self._req({
            "ivpm.yaml": "package:\n  name: p\n  include: [a.yaml]\n",
            "a.yaml": "package:\n  dep-sets: []\n"})
        first = req.load_root_package()
        req.load_root_package()
        req.config("acme")
        req.selected_dep_sets()
        self.assertIs(first, req.load_root_package())
        self.assertEqual(["ivpm.yaml", "a.yaml"], self.src.reads)

    def test_failure_memoized(self):
        req = self._req({"ivpm.yaml": "package:\n  name: [unclosed\n"})
        for _ in range(3):
            with self.assertRaises(ManifestLoadError):
                req.config("acme")
        self.assertEqual(["ivpm.yaml"], self.src.reads)

    def test_patch_files_read_from_source(self):
        manifest = (
            "package:\n  name: p\n  dep-sets:\n    - name: d\n      deps:\n"
            "        - name: lib\n          src: git\n"
            "          url: https://example.com/lib.git\n"
            "          patches: [patches/fix.patch]\n")
        req = self._req({"ivpm.yaml": manifest,
                         "patches/fix.patch": "--- a\n+++ b\n"})
        info = req.load_root_package()
        self.assertEqual(1, len(info.get_dep_set("d").packages["lib"].patches))

        req = self._req({"ivpm.yaml": manifest})
        with self._quiet():
            with self.assertRaises(ManifestLoadError) as ctx:
                req.load_root_package()
        self.assertIn("patch file not found", str(ctx.exception))


class TestSelectedDepSets(_Base):

    def test_first_dep_set(self):
        self.assertEqual(["default-dev"],
                         self._req({"ivpm.yaml": ROOT_WITH}).selected_dep_sets())

    def test_default_dep_set(self):
        m = ROOT_WITH.replace("  dep-sets:\n",
                              "  default-dep-set: release\n  dep-sets:\n", 1)
        self.assertEqual(["release"],
                         self._req({"ivpm.yaml": m}).selected_dep_sets())

    def test_cli_wins(self):
        m = ROOT_WITH.replace("  dep-sets:\n",
                              "  default-dep-set: release\n  dep-sets:\n", 1)
        req = self._req({"ivpm.yaml": m}, dep_set=["secure", "release"])
        self.assertEqual(["secure", "release"], req.selected_dep_sets())

    def test_unknown_cli_dep_set(self):
        req = self._req({"ivpm.yaml": ROOT_WITH}, dep_set=["nope"])
        with self._quiet():
            with self.assertRaises(ManifestLoadError) as ctx:
                req.selected_dep_sets()
        self.assertIn("nope", str(ctx.exception))
        self.assertIn("-d/--dep-set", str(ctx.exception))

    def test_manifest_without_dep_sets(self):
        req = self._req({"ivpm.yaml": "package:\n  name: p\n"})
        self.assertEqual(["default"], req.selected_dep_sets())


class TestConfig(_Base):

    def test_package_level(self):
        self.assertEqual({"group": "eng", "mode": "2770"},
                         self._req({"ivpm.yaml": ROOT_WITH}).config("acme"))

    def test_dep_set_overlay(self):
        req = self._req({"ivpm.yaml": ROOT_WITH}, dep_set=["release"])
        self.assertEqual({"group": "eng", "mode": "2750"}, req.config("acme"))

    def test_several_dep_sets_merge_in_order(self):
        req = self._req({"ivpm.yaml": ROOT_WITH},
                        dep_set=["release", "secure"])
        self.assertEqual({"group": "eng", "mode": "2750", "acl": "strict"},
                         req.config("acme"))

    def test_absent_block(self):
        self.assertEqual({}, self._req({"ivpm.yaml": ROOT_WITH}).config("other"))

    def test_result_is_a_copy(self):
        req = self._req({"ivpm.yaml": ROOT_WITH})
        req.config("acme")["group"] = "x"
        self.assertEqual("eng", req.config("acme")["group"])

    def test_no_name_outside_dispatch(self):
        with self.assertRaises(ValueError):
            self._req({"ivpm.yaml": ROOT_WITH}).config()

    def test_unregistered_key_is_rejected(self):
        # The same rule the post-clone update applies: a 'with:' key nobody
        # claims is a typo.
        _register(_Other)
        req = self._req({"ivpm.yaml": ROOT_WITH})
        with self._quiet():
            with self.assertRaises(ManifestLoadError) as ctx:
                req.config("acme")
        self.assertIn("Unknown key 'acme'", str(ctx.exception))


class TestDispatch(_Base):
    """Within ClonePreparerList.run(), config() with no argument is the calling
    preparer's own block, and the manifest is still parsed once."""

    def test_each_preparer_gets_its_own_block(self):
        m = ROOT_WITH.replace("  with:\n", "  with:\n    other:\n      x: 1\n", 1)
        seen = {}

        class Acme(ClonePreparer):
            name = "acme"

            def check(self, req):
                seen["acme.check"] = req.config()

            def prepare(self, req):
                seen["acme.prepare"] = req.config()

        class Other(ClonePreparer):
            name = "other"

            def applies(self, req):
                seen["other.applies"] = req.config()
                return True

            def check(self, req):
                seen["other.check"] = req.config()

        req = self._req({"ivpm.yaml": m}, dep_set=["release"])
        ClonePreparerList([Acme(), Other()]).run(req)
        acme = {"group": "eng", "mode": "2750"}
        self.assertEqual({"acme.check": acme, "acme.prepare": acme,
                          "other.applies": {"x": 1}, "other.check": {"x": 1}},
                         seen)
        self.assertEqual(["ivpm.yaml"], self.src.reads)

    def test_unhandled_load_error_refuses(self):
        class Acme(ClonePreparer):
            name = "acme"

            def check(self, req):
                req.config()

        req = self._req({"ivpm.yaml": "package:\n  name: [unclosed\n"})
        with self.assertRaises(CloneDenied) as ctx:
            ClonePreparerList([Acme()]).run(req)
        self.assertIn("ManifestLoadError", ctx.exception.format())

    def test_load_error_turned_into_warn(self):
        class Acme(ClonePreparer):
            name = "acme"

            def check(self, req):
                try:
                    req.config()
                except ManifestLoadError as e:
                    return ClonePrepareResult.warn("no policy: %s" % e)

        req = self._req({"ivpm.yaml": "package:\n  name: [unclosed\n"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ClonePreparerList([Acme()]).run(req)
        self.assertIn("acme: no policy", err.getvalue())


class TestLocalReaderAcceptsClonePreparerKeys(_Base):
    """The post-clone update reads the same manifest locally; a clone
    preparer's 'with:' block must be valid there too."""

    def _write(self, text):
        os.makedirs(self.target)
        with open(os.path.join(self.target, "ivpm.yaml"), "w") as f:
            f.write(text)

    def test_registered_key_accepted(self):
        self._write(ROOT_WITH)
        info = ProjInfo.mkFromProj(self.target)
        self.assertEqual({"group": "eng", "mode": "2770"},
                         info.handler_configs["acme"])

    def test_broken_clone_preparer_plugin_does_not_break_reading(self):
        self._write("package:\n  name: p\n  with:\n    python: {}\n")
        with mock.patch.object(
                ClonePreparerRgy, "inst",
                side_effect=ClonePreparerLoadError("boom")):
            self.assertEqual("p", ProjInfo.mkFromProj(self.target).name)


if __name__ == "__main__":
    unittest.main()
