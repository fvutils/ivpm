"""'ivpm show preparers' -- the policy surface.

IVPM ships no preparers, so the empty case is the normal one and must say
something useful rather than printing nothing.

See pkg-prepare-impl-plan.md P5.
"""
import io
import json
import unittest
from contextlib import redirect_stdout

from ivpm.prepare import PackagePreparer
from ivpm.prepare.pkg_preparer_rgy import PackagePreparerRgy
from ivpm.show.show_preparers import ShowPreparers, _dispatch_order


class _Args:
    def __init__(self, **kw):
        self.name = None
        self.json = False
        self.no_rich = True
        for k, v in kw.items():
            setattr(self, k, v)


class _ShowPreparersBase(unittest.TestCase):

    def setUp(self):
        rgy = PackagePreparerRgy.inst()
        self._saved = list(rgy.preparers)
        self._saved_meta = dict(rgy._meta)

    def tearDown(self):
        rgy = PackagePreparerRgy.inst()
        rgy.preparers = self._saved
        rgy._meta = self._saved_meta

    def register(self, cls, **kw):
        PackagePreparerRgy.inst().addPreparer(cls, **kw)

    def run_show(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ShowPreparers()(_Args(**kw))
        return buf.getvalue()


class TestEmpty(_ShowPreparersBase):

    def setUp(self):
        super().setUp()
        PackagePreparerRgy.inst().preparers = []

    def test_explains_rather_than_printing_nothing(self):
        out = self.run_show()
        self.assertIn("No package preparers are registered", out)
        self.assertIn("ivpm.pkg_preparers", out)

    def test_json_is_an_empty_list(self):
        self.assertEqual(json.loads(self.run_show(json=True)), [])


class TestListing(_ShowPreparersBase):

    def test_lists_name_order_and_provenance(self):
        class P(PackagePreparer):
            name = "disk-policy"
            description = "Apply site group policy"
            order = 20

        self.register(P, origin="acme.ext", version="1.0", provider="acme-ivpm")
        out = self.run_show()
        self.assertIn("disk-policy", out)
        self.assertIn("Apply site group policy", out)
        self.assertIn("20", out)
        self.assertIn("acme.ext", out)

    def test_scope_column_reflects_always(self):
        class OnPopulate(PackagePreparer):
            name = "on-populate"
            description = "d"

        class Always(PackagePreparer):
            name = "always-one"
            description = "d"
            always = True

        self.register(OnPopulate)
        self.register(Always)
        out = self.run_show()
        self.assertIn("on populate", out)
        self.assertIn("all packages", out)

    def test_listing_is_in_dispatch_order(self):
        """The displayed order must be the order they actually run in."""
        class Late(PackagePreparer):
            name, description, order = "zzz-late", "d", 90

        class Early(PackagePreparer):
            name, description, order = "aaa-early", "d", 10

        self.register(Late)
        self.register(Early)
        out = self.run_show()
        self.assertLess(out.index("aaa-early"), out.index("zzz-late"))

    def test_dispatch_order_matches_the_dispatcher(self):
        """_dispatch_order and PreparerList.ordered() must agree."""
        from ivpm.prepare import PreparerList

        class A(PackagePreparer):
            name, description, order = "aaa", "d", 50

        class B(PackagePreparer):
            name, description, order = "bbb", "d", 10

        class C(PackagePreparer):
            name, description, order = "ccc", "d", 50

        for cls in (A, C, B):
            self.register(cls)

        shown = [i.name for i in
                 _dispatch_order(PackagePreparerRgy.inst().preparer_infos())]
        dispatched = [type(p).name for p in
                      PreparerList([A(), C(), B()]).ordered()]
        self.assertEqual(shown, dispatched)


class TestDetail(_ShowPreparersBase):

    def test_detail_for_a_named_preparer(self):
        class P(PackagePreparer):
            name = "disk-policy"
            description = "Apply site group policy"
            order = 20
            always = True

        self.register(P)
        out = self.run_show(name="disk-policy")
        self.assertIn("disk-policy", out)
        self.assertIn("20", out)
        self.assertIn("every package", out)

    def test_unknown_name_exits_nonzero(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_show(name="nope")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_json_detail_round_trips(self):
        class P(PackagePreparer):
            name = "p"
            description = "d"

        self.register(P)
        data = json.loads(self.run_show(name="p", json=True))
        self.assertEqual(data["name"], "p")
        self.assertEqual(data["order"], 100)
        self.assertFalse(data["always"])


if __name__ == "__main__":
    unittest.main()
