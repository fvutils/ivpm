"""'ivpm show clone-preparers' -- the clone-time policy surface.

IVPM ships no clone preparers, so the empty case is the normal one and must
say something useful rather than printing nothing.

See "Contributing a Clone Preparer" in docs/source/extending_ivpm.rst.
"""
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr

from ivpm.clone.clone_preparer import ClonePreparer, ClonePrepareResult
from ivpm.clone.clone_preparer_rgy import (
    ClonePreparerRgy, ClonePreparerLoadError)
from ivpm.show.show_clone_preparers import ShowClonePreparers, _dispatch_order


class _Args:
    def __init__(self, **kw):
        self.name = None
        self.json = False
        self.no_rich = True
        for k, v in kw.items():
            setattr(self, k, v)


class _ShowClonePreparersBase(unittest.TestCase):

    def setUp(self):
        # A private, empty registry: never consult the installed entry points.
        self._saved_inst = ClonePreparerRgy._inst
        ClonePreparerRgy._inst = ClonePreparerRgy()

    def tearDown(self):
        ClonePreparerRgy._inst = self._saved_inst

    def register(self, cls, **kw):
        ClonePreparerRgy.inst().addPreparer(cls, **kw)

    def run_show(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ShowClonePreparers()(_Args(**kw))
        return buf.getvalue()


class _Check(ClonePreparer):
    name = "export-check"
    description = "Refuse export-restricted projects"
    order = 20

    def check(self, req):
        return ClonePrepareResult.ok()


class _Prep(ClonePreparer):
    name = "workarea-setup"
    description = "Create the workarea with the project group"
    order = 50

    def check(self, req):
        return ClonePrepareResult.ok()

    def prepare(self, req):
        return ClonePrepareResult.ok()


class TestEmpty(_ShowClonePreparersBase):

    def test_explains_rather_than_printing_nothing(self):
        out = self.run_show()
        self.assertIn("No clone preparers are registered", out)
        self.assertIn("ivpm.clone_preparers", out)

    def test_rich_output_explains_too(self):
        out = self.run_show(no_rich=False)
        self.assertIn("No clone preparers are registered", out)

    def test_json_is_an_empty_list(self):
        self.assertEqual(json.loads(self.run_show(json=True)), [])


class TestListing(_ShowClonePreparersBase):

    def test_lists_name_order_stages_and_provenance(self):
        self.register(_Prep, origin="acme.ext", version="1.0",
                      provider="acme-ivpm")
        out = self.run_show()
        self.assertIn("workarea-setup", out)
        self.assertIn("Create the workarea with the project group", out)
        self.assertIn("50", out)
        self.assertIn("check, prepare", out)
        self.assertIn("acme.ext", out)

    def test_rich_listing(self):
        self.register(_Check, origin="acme.ext")
        out = self.run_show(no_rich=False)
        self.assertIn("export-check", out)
        self.assertIn("acme.ext", out)

    def test_stages_column_reflects_overrides(self):
        self.register(_Check)
        self.register(_Prep)
        data = {i["name"]: i["stages"]
                for i in json.loads(self.run_show(json=True))}
        self.assertEqual(["check"], data["export-check"])
        self.assertEqual(["check", "prepare"], data["workarea-setup"])

    def test_listing_is_in_dispatch_order(self):
        """The displayed order must be the order they actually run in."""
        class Late(ClonePreparer):
            name, description, order = "zzz-late", "d", 90

        class Early(ClonePreparer):
            name, description, order = "aaa-early", "d", 10

        self.register(Late)
        self.register(Early)
        out = self.run_show()
        self.assertLess(out.index("aaa-early"), out.index("zzz-late"))
        names = [i["name"] for i in json.loads(self.run_show(json=True))]
        self.assertEqual(["aaa-early", "zzz-late"], names)

    def test_dispatch_order_matches_the_dispatcher(self):
        """_dispatch_order and ClonePreparerList.ordered() must agree."""
        from ivpm.clone.clone_preparer_list import ClonePreparerList

        class A(ClonePreparer):
            name, description, order = "aaa", "d", 50

        class B(ClonePreparer):
            name, description, order = "bbb", "d", 10

        class C(ClonePreparer):
            name, description, order = "ccc", "d", 50

        for cls in (A, C, B):
            self.register(cls)

        shown = [i.name for i in
                 _dispatch_order(ClonePreparerRgy.inst().preparer_infos())]
        dispatched = [type(p).name for p in
                      ClonePreparerList([A(), C(), B()]).ordered()]
        self.assertEqual(shown, dispatched)


class TestDetail(_ShowClonePreparersBase):

    def test_detail_for_a_named_preparer(self):
        self.register(_Prep, origin="acme.ext", version="2.1")
        out = self.run_show(name="workarea-setup")
        self.assertIn("workarea-setup", out)
        self.assertIn("50", out)
        self.assertIn("prepare (may create", out)
        self.assertIn("acme.ext", out)
        self.assertIn("2.1", out)

    def test_rich_detail(self):
        self.register(_Check)
        out = self.run_show(name="export-check", no_rich=False)
        self.assertIn("export-check", out)
        self.assertIn("check (read-only", out)

    def test_unknown_name_exits_nonzero(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            self.run_show(name="nope")
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertIn("nope", err.getvalue())

    def test_json_detail_round_trips(self):
        self.register(_Check)
        data = json.loads(self.run_show(name="export-check", json=True))
        self.assertEqual("export-check", data["name"])
        self.assertEqual(20, data["order"])
        self.assertEqual(["check"], data["stages"])


class TestLoadFailure(unittest.TestCase):
    """A broken plugin is reported, not a traceback; the clone itself treats
    the same failure as fatal (see test_clone_preparer_rgy)."""

    def setUp(self):
        self._saved_inst = ClonePreparerRgy._inst
        ClonePreparerRgy._inst = None
        self._saved_inst_fn = ClonePreparerRgy.__dict__["inst"]

        def _boom(cls):
            raise ClonePreparerLoadError("Failed to load clone preparer 'bad'")
        ClonePreparerRgy.inst = classmethod(_boom)

    def tearDown(self):
        ClonePreparerRgy.inst = self._saved_inst_fn
        ClonePreparerRgy._inst = self._saved_inst

    def test_show_reports_and_exits_nonzero(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as ctx:
            ShowClonePreparers()(_Args())
        self.assertEqual(1, ctx.exception.code)
        self.assertIn("Failed to load clone preparer 'bad'", err.getvalue())

    def test_show_all_still_renders(self):
        from ivpm.show.cmd_show import _show_all
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            _show_all(_Args(json=True))
        data = json.loads(out.getvalue())
        self.assertEqual([], data["clone_preparers"])
        self.assertIn("Failed to load clone preparer 'bad'", err.getvalue())

    def test_clone_epilog_omits_the_line(self):
        from ivpm.__main__ import _clone_providers_epilog
        epilog = _clone_providers_epilog() or ""
        self.assertNotIn("Clone preparers:", epilog)


class TestShowAllAndEpilog(_ShowClonePreparersBase):

    def test_show_all_json_has_preparer_keys(self):
        from ivpm.show.cmd_show import _show_all
        self.register(_Check)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _show_all(_Args(json=True))
        data = json.loads(buf.getvalue())
        self.assertIn("preparers", data)
        self.assertEqual(["export-check"],
                         [i["name"] for i in data["clone_preparers"]])

    def test_show_all_plain_has_sections(self):
        from ivpm.show.cmd_show import _show_all
        self.register(_Check)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _show_all(_Args())
        out = buf.getvalue()
        self.assertIn("=== Package Preparers ===", out)
        self.assertIn("=== Clone Preparers ===", out)
        self.assertIn("export-check", out)

    def test_clone_epilog_lists_preparers_in_dispatch_order(self):
        from ivpm.__main__ import _clone_providers_epilog
        self.register(_Prep)
        self.register(_Check)
        epilog = _clone_providers_epilog()
        self.assertIn("Clone preparers: export-check, workarea-setup "
                      "(see 'ivpm show clone-preparers')", epilog)

    def test_clone_epilog_silent_when_none(self):
        from ivpm.__main__ import _clone_providers_epilog
        self.assertNotIn("Clone preparers:", _clone_providers_epilog() or "")

    def test_parser_accepts_subcommand_and_alias(self):
        from ivpm.__main__ import get_parser
        parser = get_parser()
        for sub in ("clone-preparers", "clone-preparer"):
            args = parser.parse_args(["show", sub, "x", "--json", "--no-rich"])
            self.assertEqual(sub, args.show_cmd)
            self.assertEqual("x", args.name)
            self.assertTrue(args.json and args.no_rich)


if __name__ == "__main__":
    unittest.main()
