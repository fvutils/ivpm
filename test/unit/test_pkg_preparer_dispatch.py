"""Preparers wired into the update.

The load decision (P1/P2) and the prepare hook (P3) only pay off together: a
preparer creates the target directory before the fetch, and the package must
still be fetched into it. That interaction is what these tests pin down.

See pkg-prepare-design.md §4.4-4.7 / pkg-prepare-impl-plan.md P4.
"""
import os
import shutil

from .test_base import TestBase

from ivpm.load_plan import LoadAction
from ivpm.prepare import PackagePreparer, PrepareDenied, PrepareResult
from ivpm.prepare.pkg_preparer_rgy import PackagePreparerRgy


class _PreparerTestBase(TestBase):
    """Registers preparers on the real registry for the duration of a test."""

    def setUp(self):
        super().setUp()
        rgy = PackagePreparerRgy.inst()
        self._saved = list(rgy.preparers)
        self._saved_meta = dict(rgy._meta)

    def tearDown(self):
        rgy = PackagePreparerRgy.inst()
        rgy.preparers = self._saved
        rgy._meta = self._saved_meta
        return super().tearDown()

    def register(self, cls):
        PackagePreparerRgy.inst().addPreparer(cls, origin="test")

    def deps_dir(self):
        return os.path.join(self.testdir, "packages")

    def pkg_dir(self, name):
        return os.path.join(self.deps_dir(), name)

    def write_manifest(self, extra_with="", deps=None):
        deps = deps or [("leaf1", "leaf_proj1")]
        dep_txt = "".join(
            "                - name: %s\n"
            "                  url: file://${DATA_DIR}/%s\n"
            "                  src: dir\n"
            "                  link: false\n" % (n, d) for n, d in deps)
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: prep_root\n"
                    "%s"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % (extra_with, dep_txt))


class TestPrepareRunsBeforePopulate(_PreparerTestBase):

    def test_target_dir_is_empty_when_prepare_runs(self):
        """The contract: nothing has been written for the package yet."""
        observed = []

        class Probe(PackagePreparer):
            name = "probe"

            def prepare(self, req):
                observed.append({
                    "name": req.pkg.name,
                    "target": req.target_dir,
                    "exists": os.path.exists(req.target_dir),
                    "contents": (os.listdir(req.target_dir)
                                 if os.path.isdir(req.target_dir) else None),
                    "action": req.decision.action,
                    "scope_key": req.scope_key,
                })
                return PrepareResult.ok()

        self.register(Probe)
        self.write_manifest()
        self.ivpm_update(skip_venv=True)

        self.assertTrue(observed, "prepare() never ran")
        for rec in observed:
            self.assertIn(rec["contents"], (None, []),
                          "%s already had content at prepare time: %r"
                          % (rec["name"], rec["contents"]))
            self.assertIs(rec["action"], LoadAction.FETCH)

    def test_preparer_created_directory_does_not_suppress_the_fetch(self):
        """The whole point: prepare creates the dir, the package still loads.

        Before the load-decision work, an existing directory read as 'already
        loaded' and the fetch was silently skipped.
        """
        class Maker(PackagePreparer):
            name = "maker"

            def prepare(self, req):
                os.makedirs(req.target_dir, exist_ok=True)
                return PrepareResult.ok()

        self.register(Maker)
        self.write_manifest()
        self.ivpm_update(skip_venv=True)

        path = self.pkg_dir("leaf1")
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.listdir(path),
                        "package was not populated: prepare's directory "
                        "suppressed the fetch")

    def test_prepared_mode_is_inherited_by_fetched_content(self):
        """A directory created by prepare keeps its mode; content lands inside.

        The mechanism the whole design exists for, checked without needing a
        second Unix group: set the sticky-ish group bit pattern on the prepared
        directory and confirm the fetch wrote into *that* directory rather than
        replacing it.
        """
        import stat

        class Maker(PackagePreparer):
            name = "maker"

            def prepare(self, req):
                os.makedirs(req.target_dir, exist_ok=True)
                mode = os.stat(req.target_dir).st_mode
                os.chmod(req.target_dir, mode | stat.S_ISGID)
                return PrepareResult.ok()

        self.register(Maker)
        self.write_manifest()
        self.ivpm_update(skip_venv=True)

        path = self.pkg_dir("leaf1")
        self.assertTrue(os.listdir(path))
        self.assertTrue(os.stat(path).st_mode & stat.S_ISGID,
                        "the prepared directory was replaced, not populated")


class TestRefusal(_PreparerTestBase):

    def test_denial_aborts_the_update(self):
        class Refuser(PackagePreparer):
            name = "disk-policy"

            def prepare(self, req):
                return PrepareResult.deny(
                    "target %s is not writable" % req.target_dir,
                    hint="request write access")

        self.register(Refuser)
        self.write_manifest()

        with self.assertRaises(Exception) as ctx:
            self.ivpm_update(skip_venv=True)

        msg = str(ctx.exception)
        self.assertIn("disk-policy", msg)
        self.assertIn("leaf1", msg)
        self.assertIn("not writable", msg)

    def test_denial_leaves_the_package_unpopulated(self):
        class Refuser(PackagePreparer):
            name = "refuser"

            def prepare(self, req):
                return PrepareResult.deny("no")

        self.register(Refuser)
        self.write_manifest()
        try:
            self.ivpm_update(skip_venv=True)
        except Exception:
            pass
        path = self.pkg_dir("leaf1")
        self.assertFalse(os.path.isdir(path) and os.listdir(path))

    def test_session_start_denial_aborts_before_anything_is_written(self):
        class EarlyRefuser(PackagePreparer):
            name = "early"

            def on_session_start(self, update_info):
                raise PrepareDenied("<workspace>", [])

        self.register(EarlyRefuser)
        self.write_manifest()

        with self.assertRaises(PrepareDenied):
            self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.isdir(self.pkg_dir("leaf1")))


class TestConfigAndContext(_PreparerTestBase):

    def test_preparer_sees_its_own_with_block(self):
        seen = []

        class Cfg(PackagePreparer):
            name = "disk-policy"

            def prepare(self, req):
                seen.append(dict(req.config))
                return PrepareResult.ok()

        self.register(Cfg)
        self.write_manifest(extra_with=(
            "    with:\n"
            "        disk-policy:\n"
            "            default-group: eng\n"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(seen)
        self.assertEqual(seen[0], {"default-group": "eng"})

    def test_preparer_does_not_see_another_preparers_block(self):
        seen = []

        class Mine(PackagePreparer):
            name = "mine"

            def prepare(self, req):
                seen.append(dict(req.config))
                return PrepareResult.ok()

        class SomeoneElse(PackagePreparer):
            name = "someone-else"

        self.register(Mine)
        self.register(SomeoneElse)
        self.write_manifest(extra_with=(
            "    with:\n"
            "        someone-else:\n"
            "            key: value\n"))
        self.ivpm_update(skip_venv=True)

        self.assertTrue(seen)
        self.assertEqual(seen[0], {})

    def test_request_carries_scope_and_deps_dir(self):
        seen = []

        class Probe(PackagePreparer):
            name = "probe"

            def prepare(self, req):
                seen.append((req.scope_key, req.deps_dir, req.target_dir))
                return PrepareResult.ok()

        self.register(Probe)
        self.write_manifest()
        self.ivpm_update(skip_venv=True)

        scope_key, deps_dir, target = seen[0]
        self.assertEqual(scope_key, "leaf1")
        self.assertEqual(os.path.realpath(deps_dir),
                         os.path.realpath(self.deps_dir()))
        self.assertEqual(os.path.realpath(target),
                         os.path.realpath(self.pkg_dir("leaf1")))


class TestNestedScope(_PreparerTestBase):

    def test_nested_package_target_is_the_nested_path(self):
        seen = {}

        class Probe(PackagePreparer):
            name = "probe"

            def prepare(self, req):
                seen[req.scope_key] = req.target_dir
                return PrepareResult.ok()

        self.register(Probe)
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: prep_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "                - name: nested_toolB\n"
                    "                  url: file://${DATA_DIR}/nested_toolB\n"
                    "                  src: dir\n"
                    "                  link: false\n"
                    "                  deps-mode: nested\n")
        self.ivpm_update(skip_venv=True)

        nested = {k: v for k, v in seen.items() if "/" in k}
        self.assertTrue(nested, "no nested-scope package was prepared; saw %r"
                        % sorted(seen))
        for scope_key, target in nested.items():
            self.assertTrue(target.endswith(scope_key.replace("/", os.sep)),
                            "target %r is not the nested path for %r"
                            % (target, scope_key))


class TestReuseIsSkipped(_PreparerTestBase):

    def test_default_preparer_not_called_for_resident_packages(self):
        calls = []

        class Probe(PackagePreparer):
            name = "probe"

            def prepare(self, req):
                calls.append(req.pkg.name)
                return PrepareResult.ok()

        self.register(Probe)
        self.write_manifest()

        self.ivpm_update(skip_venv=True)
        first = len(calls)
        self.assertTrue(first)

        # Second update: everything is resident, so nothing is prepared.
        self.ivpm_update(skip_venv=True)
        self.assertEqual(len(calls), first,
                         "prepare ran for a package that was only reused")

    def test_always_preparer_is_called_for_resident_packages(self):
        actions = []

        class Always(PackagePreparer):
            name = "always"
            always = True

            def prepare(self, req):
                actions.append(req.decision.action)
                return PrepareResult.ok()

        self.register(Always)
        self.write_manifest()

        self.ivpm_update(skip_venv=True)
        self.ivpm_update(skip_venv=True)

        self.assertIn(LoadAction.FETCH, actions)
        self.assertIn(LoadAction.REUSE, actions)


class TestNoPreparersInstalled(_PreparerTestBase):
    """The default: an empty registry changes nothing."""

    def test_update_works_with_no_preparers(self):
        self.write_manifest()
        self.ivpm_update(skip_venv=True)
        self.assertTrue(os.listdir(self.pkg_dir("leaf1")))
