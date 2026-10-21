"""Pure unit tests of submodule configuration and identity (git_submodules.py).

No repositories, no network: spec parsing and validation, the canonical form
and spec id that the cache key and lock entry are built from, the
``git submodule update`` command line, lock records and drift, the deps-source
identity check, and the status summary.  End-to-end behavior against real
repositories is in test_git_submodules.py.
"""
import types
import unittest
from unittest.mock import patch

from ivpm import git_submodules as gsm
from ivpm.git_submodules import (
    SubmoduleSpec, SubmoduleSpecError, SubmoduleRecord, parse_spec,
    spec_from_lock, normalize_path, update_command, is_shallow_miss,
    lock_record, FetchResult, failure_message)
from ivpm.package_lock import _spec_matches_lock, _entry_from_pkg
from ivpm.pkg_status import PkgVcsStatus, SubmoduleStatus
from ivpm.pkg_types.package_git import PackageGit, _with_progress, _is_remote_url


class TestParseShortForms(unittest.TestCase):

    def test_true_enables_everything_recursively(self):
        s = parse_spec(True)
        self.assertTrue(s.enabled)
        self.assertTrue(s.recursive)
        self.assertIsNone(s.paths)
        self.assertIsNone(s.depth)
        self.assertTrue(s.required)
        self.assertFalse(s.legacy)
        self.assertEqual(s.origin, "explicit")

    def test_false_is_off(self):
        s = parse_spec(False)
        self.assertFalse(s.enabled)
        self.assertIsNone(s.spec_id(True))
        self.assertEqual(s.cache_suffix(), "")

    def test_null_is_rejected(self):
        # `submodules:` with no value parses to None: almost certainly an
        # unfinished edit, so it must not silently mean either default.
        with self.assertRaises(SubmoduleSpecError):
            parse_spec(None)

    def test_scalars_other_than_bool_are_rejected(self):
        for v in ("true", "yes", 1, 0, ["ext/a"], 1.5):
            with self.subTest(v=v):
                with self.assertRaises(SubmoduleSpecError):
                    parse_spec(v)


class TestParseLongForm(unittest.TestCase):

    def test_defaults_match_the_short_form(self):
        self.assertEqual(parse_spec({}).canonical(True),
                         parse_spec(True).canonical(True))
        self.assertEqual(parse_spec({}).spec_id(False),
                         parse_spec(True).spec_id(False))

    def test_all_keys(self):
        s = parse_spec({"recursive": False, "paths": ["b", "a"], "depth": 3,
                        "jobs": 8, "required": False})
        self.assertFalse(s.recursive)
        self.assertEqual(s.paths, ("a", "b"))
        self.assertEqual(s.depth, 3)
        self.assertEqual(s.jobs, 8)
        self.assertFalse(s.required)

    def test_unknown_key_is_fatal_and_named(self):
        with self.assertRaises(SubmoduleSpecError) as cm:
            parse_spec({"recursve": True})
        self.assertIn("recursve", str(cm.exception))
        self.assertIn("recursive", str(cm.exception))  # lists the valid keys

    def test_bool_keys_must_be_bools(self):
        for key in ("recursive", "required"):
            for v in ("yes", 1, None):
                with self.subTest(key=key, v=v):
                    with self.assertRaises(SubmoduleSpecError):
                        parse_spec({key: v})

    def test_depth_values(self):
        self.assertEqual(parse_spec({"depth": 1}).depth, 1)
        self.assertEqual(parse_spec({"depth": "recommended"}).depth,
                         "recommended")
        self.assertEqual(parse_spec({"depth": " Recommended "}).depth,
                         "recommended")
        for bad in (0, -1, True, "full", "1", 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(SubmoduleSpecError):
                    parse_spec({"depth": bad})

    def test_jobs_values(self):
        self.assertEqual(parse_spec({"jobs": 1}).jobs, 1)
        for bad in (0, -2, True, "4"):
            with self.subTest(bad=bad):
                with self.assertRaises(SubmoduleSpecError):
                    parse_spec({"jobs": bad})

    def test_paths_string_is_one_path(self):
        self.assertEqual(parse_spec({"paths": "ext/mid"}).paths, ("ext/mid",))

    def test_paths_are_normalized_sorted_and_deduplicated(self):
        s = parse_spec({"paths": ["./ext/mid/", "a", "ext//mid", "a"]})
        self.assertEqual(s.paths, ("a", "ext/mid"))

    def test_bad_paths(self):
        for bad in ([], [""], ["/abs/p"], ["../up"], ["a/../.."], ["."], [3]):
            with self.subTest(bad=bad):
                with self.assertRaises(SubmoduleSpecError):
                    parse_spec({"paths": bad})


class TestNormalizePath(unittest.TestCase):

    def test_spellings(self):
        self.assertEqual(normalize_path("ext/mid"), "ext/mid")
        self.assertEqual(normalize_path("./ext/mid/"), "ext/mid")
        self.assertEqual(normalize_path("ext\\mid"), "ext/mid")
        self.assertEqual(normalize_path("a/b/../c"), "a/c")


class TestIdentity(unittest.TestCase):

    def test_depth_defaults_by_cache_mode(self):
        s = parse_spec(True)
        self.assertEqual(s.resolved_depth(cached=True), 1)
        self.assertIsNone(s.resolved_depth(cached=False))
        self.assertIsNone(SubmoduleSpec(enabled=True, depth="full")
                          .resolved_depth(True))
        self.assertEqual(parse_spec({"depth": 5}).resolved_depth(True), 5)

    def test_id_changes_with_content_affecting_fields(self):
        base = parse_spec(True).spec_id(True)
        for other in ({"recursive": False}, {"paths": ["a"]}, {"depth": 2},
                      {"depth": "recommended"}):
            with self.subTest(other=other):
                self.assertNotEqual(parse_spec(other).spec_id(True), base)

    def test_id_ignores_jobs_and_required(self):
        base = parse_spec(True).spec_id(True)
        self.assertEqual(parse_spec({"jobs": 7}).spec_id(True), base)
        self.assertEqual(parse_spec({"required": False}).spec_id(True), base)

    def test_id_depends_on_cache_mode_only_through_depth(self):
        # Default depth: 1 cached, full editable -- different trees.
        self.assertNotEqual(parse_spec(True).spec_id(True),
                            parse_spec(True).spec_id(False))
        # An explicit depth is the same either way.
        self.assertEqual(parse_spec({"depth": 1}).spec_id(True),
                         parse_spec({"depth": 1}).spec_id(False))

    def test_id_shape(self):
        sid = parse_spec(True).spec_id(True)
        self.assertTrue(sid.startswith("sm1-"))
        self.assertEqual(len(sid), len("sm1-") + 16)

    def test_cache_suffix(self):
        s = parse_spec(True)
        self.assertEqual(s.cache_suffix(), "_sm_" + s.spec_id(True)[4:])
        self.assertEqual(gsm.LEGACY.cache_suffix(), "")
        self.assertEqual(gsm.OFF.cache_suffix(), "")

    def test_legacy_canonical_is_full_depth_recursive(self):
        self.assertEqual(gsm.LEGACY.canonical(True),
                         {"enabled": True, "recursive": True, "paths": None,
                          "depth": None})

    def test_describe(self):
        self.assertEqual(gsm.OFF.describe(), "off")
        self.assertEqual(gsm.LEGACY.describe(), "legacy")
        self.assertEqual(parse_spec(True).describe(), "recursive")
        self.assertEqual(parse_spec({"recursive": False, "paths": ["a", "b"],
                                     "depth": 1, "required": False}).describe(),
                         "top-level,paths:2,depth:1,optional")


class TestImplicitDefault(unittest.TestCase):

    def test_legacy(self):
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"):
            self.assertIs(gsm.implicit_spec(), gsm.LEGACY)

    def test_off(self):
        with patch.object(gsm, "IMPLICIT_DEFAULT", "off"):
            self.assertIs(gsm.implicit_spec(), gsm.OFF)

    def test_legacy_is_optional(self):
        # The old code tolerated every failure, so the implicit legacy spec
        # warns rather than fails.
        self.assertFalse(gsm.LEGACY.required)


class TestLockSpecRoundTrip(unittest.TestCase):

    def test_round_trip_keeps_identity(self):
        for spec, cached in ((parse_spec(True), True),
                             (parse_spec(True), False),
                             (parse_spec({"paths": ["x"], "depth": 2}), False),
                             (parse_spec({"depth": "recommended"}), True),
                             (parse_spec({"recursive": False}), True),
                             (gsm.LEGACY, True)):
            with self.subTest(spec=spec, cached=cached):
                back = spec_from_lock({"spec": spec.to_lock(cached)})
                self.assertEqual(back.spec_id(cached), spec.spec_id(cached))
                self.assertEqual(back.cache_suffix(cached),
                                 spec.cache_suffix(cached))
                self.assertEqual(back.legacy, spec.legacy)

    def test_required_and_jobs_survive(self):
        spec = parse_spec({"required": False, "jobs": 3})
        back = spec_from_lock({"spec": spec.to_lock(True)})
        self.assertFalse(back.required)
        self.assertEqual(back.jobs, 3)

    def test_off_round_trip(self):
        back = spec_from_lock({"spec": gsm.OFF.to_lock(True), "modules": []})
        self.assertFalse(back.enabled)

    def test_missing_record_is_legacy(self):
        s = spec_from_lock(None)
        self.assertTrue(s.legacy)
        self.assertTrue(s.enabled)
        self.assertFalse(s.required)
        self.assertEqual(s.origin, "lock-legacy")

    def test_is_lock_record(self):
        self.assertTrue(gsm.is_lock_record({"spec": {}, "modules": []}))
        self.assertFalse(gsm.is_lock_record({"paths": ["a"]}))
        self.assertFalse(gsm.is_lock_record(True))


class TestUpdateCommand(unittest.TestCase):

    def test_default(self):
        self.assertEqual(update_command(parse_spec(True), False, 1),
                         ["git", "submodule", "update", "--init", "--recursive"])

    def test_cached_default_is_shallow(self):
        cmd = update_command(parse_spec(True), True, 1)
        self.assertIn("--depth", cmd)
        self.assertEqual(cmd[cmd.index("--depth") + 1], "1")

    def test_recommended(self):
        cmd = update_command(parse_spec({"depth": "recommended"}), True, 1)
        self.assertIn("--recommend-shallow", cmd)
        self.assertNotIn("--depth", cmd)

    def test_not_recursive(self):
        self.assertNotIn("--recursive",
                         update_command(parse_spec({"recursive": False}), False, 1))

    def test_jobs_default_is_bounded(self):
        self.assertNotIn("--jobs", update_command(parse_spec(True), False, 1))
        cmd = update_command(parse_spec(True), False, 3)
        self.assertEqual(cmd[cmd.index("--jobs") + 1], "3")
        cmd = update_command(parse_spec(True), False, 40)
        self.assertEqual(cmd[cmd.index("--jobs") + 1], str(gsm.DEFAULT_MAX_JOBS))

    def test_explicit_jobs(self):
        cmd = update_command(parse_spec({"jobs": 9}), False, 2)
        self.assertEqual(cmd[cmd.index("--jobs") + 1], "9")

    def test_paths_follow_a_separator(self):
        cmd = update_command(parse_spec(True), False, 2, ["a", "b"])
        self.assertEqual(cmd[-3:], ["--", "a", "b"])

    def test_explicit_depth_override(self):
        cmd = update_command(parse_spec(True), True, 1, depth=None)
        self.assertNotIn("--depth", cmd)

    def test_progress_goes_before_the_separator(self):
        self.assertEqual(_with_progress(["git", "submodule", "update", "--",
                                         "a"]),
                         ["git", "submodule", "update", "--progress", "--", "a"])
        self.assertEqual(_with_progress(["git", "clone", "u"]),
                         ["git", "clone", "u", "--progress"])


class TestShallowMiss(unittest.TestCase):

    def test_recognized(self):
        for err in (
                "fatal: Fetched in submodule path 'x', but it did not contain "
                "abc. Direct fetching of that commit failed.",
                "error: Server does not allow request for unadvertised object abc",
                "fatal: remote error: upload-pack: not our ref abc"):
            with self.subTest(err=err):
                self.assertTrue(is_shallow_miss(err))

    def test_not_recognized(self):
        for err in ("", None, "fatal: repository 'x' not found",
                    "Permission denied (publickey)."):
            with self.subTest(err=err):
                self.assertFalse(is_shallow_miss(err))


class TestRecord(unittest.TestCase):

    def _rec(self, **kw):
        d = dict(path="ext/a", name="a", parent="", url="u", commit="c" * 40,
                 state="ok", in_scope=True)
        d.update(kw)
        return SubmoduleRecord(**d)

    def test_lock_status(self):
        self.assertEqual(self._rec().lock_status(), "ok")
        self.assertEqual(self._rec(state="uninitialized").lock_status(), "failed")
        self.assertEqual(self._rec(state="moved").lock_status(), "moved")
        self.assertEqual(self._rec(state="conflict").lock_status(), "conflict")
        self.assertEqual(self._rec(in_scope=False, state="uninitialized")
                         .lock_status(), "not-requested")
        self.assertEqual(self._rec(update="none", in_scope=False).lock_status(),
                         "update-none")

    def test_to_lock(self):
        d = self._rec(shallow=True).to_lock()
        self.assertEqual(d["initialized"], True)
        self.assertTrue(d["shallow"])
        d = self._rec(state="uninitialized", in_scope=False).to_lock()
        self.assertFalse(d["initialized"])
        self.assertNotIn("shallow", d)

    def test_needs_work(self):
        self.assertFalse(self._rec().needs_work)
        self.assertTrue(self._rec(state="moved").needs_work)
        self.assertFalse(self._rec(state="moved", in_scope=False).needs_work)

    def test_missing_skips_children_of_moved_parents(self):
        recs = [self._rec(path="a", state="moved"),
                self._rec(path="a/b", parent="a", state="uninitialized"),
                self._rec(path="c", state="uninitialized"),
                self._rec(path="d", state="ok"),
                self._rec(path="d/e", parent="d", state="uninitialized"),
                self._rec(path="f", state="uninitialized", in_scope=False)]
        self.assertEqual([r.path for r in gsm.missing(recs)], ["c", "d/e"])

    def test_lock_record_shape(self):
        spec = parse_spec(True)
        rec = lock_record(spec, [self._rec()], cached=True)
        self.assertEqual(rec["spec_id"], spec.spec_id(True))
        self.assertEqual(rec["spec"]["depth"], 1)
        self.assertEqual(rec["modules"][0]["path"], "ext/a")
        off = lock_record(gsm.OFF, [], cached=True)
        self.assertEqual(off, {"spec": {"enabled": False}, "modules": []})


class TestFailureMessage(unittest.TestCase):

    def _result(self, err, url="https://gitlab.example.com/ip/mid.git",
                state="uninitialized"):
        rec = SubmoduleRecord(path="ext/mid", name="mid", parent="", url=url,
                              commit="a" * 40, state=state, in_scope=True)
        return FetchResult(ok=False, spec=parse_spec(True), problems=[rec],
                           stderr=err, rc=1)

    def test_names_path_url_and_git_output(self):
        msg = failure_message("foo", "abc1234def", self._result(
            "fatal: repository 'x' not found"))
        self.assertTrue(msg.startswith("Failed to initialize submodules of foo "
                                       "(abc1234)"))
        self.assertIn("ext/mid -> https://gitlab.example.com/ip/mid.git", msg)
        self.assertIn("git reported:", msg)
        self.assertIn("not found", msg)

    def test_other_host_auth_hint(self):
        msg = failure_message("foo", None, self._result(
            "fatal: could not read Username for 'https://gitlab.example.com': "
            "terminal prompts disabled"),
            superproject_url="https://github.com/org/foo.git")
        self.assertIn("submodule host gitlab.example.com differs from foo's "
                      "host github.com", msg)

    def test_same_host_has_no_host_hint(self):
        msg = failure_message("foo", None, self._result(
            "fatal: Authentication failed",
            url="https://github.com/org/mid.git"),
            superproject_url="https://github.com/org/foo.git")
        self.assertNotIn("differs from", msg)

    def test_file_protocol_hint(self):
        msg = failure_message("foo", None, self._result(
            "fatal: transport 'file' not allowed", url="file:///r/mid"))
        self.assertIn("protocol.file.allow", msg)

    def test_moved_submodule_hint(self):
        msg = failure_message("foo", None, self._result("", state="moved"))
        self.assertIn("would not serve the pinned commit", msg)

    def test_git_output_is_tailed(self):
        err = "\n".join("line %d" % i for i in range(30))
        msg = failure_message("foo", None, self._result(err))
        self.assertIn("line 29", msg)
        self.assertNotIn("line 10\n", msg)


class TestRemoteUrl(unittest.TestCase):

    def test_classification(self):
        for u in ("https://h/o/r.git", "ssh://git@h/o/r", "git@h:o/r.git",
                  "git://h/o/r"):
            self.assertTrue(_is_remote_url(u), u)
        for u in ("../mid.git", "./x", "/abs/repo", "file:///abs/repo",
                  "C:/repo"):
            self.assertFalse(_is_remote_url(u), u)


def _git_pkg(**kw):
    p = PackageGit("foo")
    p.url = "https://github.com/org/foo.git"
    p.src_type = "git"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


class TestProcessOptions(unittest.TestCase):

    def _mk(self, opts):
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        d = dict(url="https://github.com/org/foo.git")
        d.update(opts)
        return PkgTypeRgy.inst().mkPackage("git", "foo", d, None)

    def test_manifest_value(self):
        p = self._mk({"submodules": {"paths": ["a"]}})
        self.assertEqual(p.submodule_spec().paths, ("a",))
        self.assertEqual(p.submodule_spec().origin, "explicit")

    def test_absent_is_implicit(self):
        p = self._mk({})
        self.assertIsNone(p.submodules_spec)
        self.assertEqual(p.submodule_spec().origin, "implicit")

    def test_lock_record_value(self):
        spec = parse_spec({"depth": 2})
        p = self._mk({"commit_resolved": "a" * 40,
                      "submodules": lock_record(spec, [], cached=False)})
        self.assertEqual(p.submodule_spec().spec_id(False), spec.spec_id(False))
        self.assertEqual(p.submodule_spec().origin, "lock")

    def test_legacy_lock_entry(self):
        # A v1/v2 lock entry: resolved fields, no 'submodules'.
        p = self._mk({"commit_resolved": "a" * 40, "commit_requested": None})
        self.assertTrue(p.submodule_spec().legacy)
        self.assertEqual(p.submodule_spec().origin, "lock-legacy")

    def test_bad_value_is_fatal(self):
        with self.assertRaises(Exception) as cm:
            self._mk({"submodules": {"bogus": 1}})
        self.assertIn("bogus", str(cm.exception))

    def test_cache_true_rejects_deep_submodules(self):
        with self.assertRaises(Exception) as cm:
            self._mk({"cache": True, "submodules": {"depth": 5}})
        self.assertIn("submodules.depth", str(cm.exception))

    def test_cache_true_accepts_shallow_values(self):
        for d in (1, "recommended"):
            p = self._mk({"cache": True, "submodules": {"depth": d}})
            self.assertEqual(p.submodule_spec().depth, d)

    def test_cache_false_accepts_any_depth(self):
        p = self._mk({"cache": False, "submodules": {"depth": 50}})
        self.assertEqual(p.submodule_spec().depth, 50)

    def test_submodules_is_a_git_dep_key(self):
        self.assertIn("submodules", PackageGit.dep_keys())


class TestLockEntryAndDrift(unittest.TestCase):

    def _entry(self, pkg, records=()):
        pkg._sm_records = list(records)
        return _entry_from_pkg(pkg)

    def test_entry_records_depth_and_submodules(self):
        p = _git_pkg(cache=False, depth=3)
        p.submodules_spec = parse_spec(True)
        e = self._entry(p)
        self.assertEqual(e["depth"], 3)
        self.assertEqual(e["submodules"]["spec"]["enabled"], True)
        self.assertIn("spec_id", e["submodules"])

    def test_entry_without_tree_still_carries_the_key(self):
        # v3 git entries always carry 'submodules', so an absent key keeps
        # meaning "written before the setting existed".
        p = _git_pkg(cache=True)
        p.path = "/nonexistent/foo"
        self.assertEqual(_entry_from_pkg(p)["submodules"]["modules"], [])

    def test_cached_spec_change_is_drift(self):
        locked = _git_pkg(cache=True)
        locked.submodules_spec = parse_spec(True)
        entry = self._entry(locked)
        same = _git_pkg(cache=True)
        same.submodules_spec = parse_spec({"jobs": 2})      # jobs: not content
        self.assertTrue(_spec_matches_lock(same, entry))
        changed = _git_pkg(cache=True)
        changed.submodules_spec = parse_spec({"recursive": False})
        self.assertFalse(_spec_matches_lock(changed, entry))
        off = _git_pkg(cache=True)
        off.submodules_spec = parse_spec(False)
        self.assertFalse(_spec_matches_lock(off, entry))

    def test_editable_spec_change_is_not_drift(self):
        # Reconciled in place on the resident path instead of re-cloned.
        locked = _git_pkg(cache=False)
        locked.submodules_spec = parse_spec(False)
        entry = self._entry(locked)
        changed = _git_pkg(cache=False)
        changed.submodules_spec = parse_spec(True)
        self.assertTrue(_spec_matches_lock(changed, entry))

    def test_depth_change_is_not_drift(self):
        entry = self._entry(_git_pkg(cache=False, depth=1))
        self.assertTrue(_spec_matches_lock(_git_pkg(cache=False, depth=5), entry))

    def test_legacy_lock_matches_implicit_legacy(self):
        entry = self._entry(_git_pkg(cache=True))
        del entry["submodules"]                    # a v2 entry
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"):
            self.assertTrue(_spec_matches_lock(_git_pkg(cache=True), entry))

    def test_legacy_lock_vs_explicit_spec_is_drift(self):
        entry = self._entry(_git_pkg(cache=True))
        del entry["submodules"]
        p = _git_pkg(cache=True)
        p.submodules_spec = parse_spec(True)
        self.assertFalse(_spec_matches_lock(p, entry))


class TestCacheVersions(unittest.TestCase):

    SHA = "a" * 40

    def test_enabled(self):
        p = _git_pkg(cache=True)
        p.submodules_spec = parse_spec(True)
        v = p._cache_versions(self.SHA)
        self.assertEqual(v, [self.SHA + p.submodule_spec().cache_suffix()])
        self.assertTrue(v[0].startswith(self.SHA + "_sm_"))

    def test_legacy_keeps_the_bare_key(self):
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"):
            self.assertEqual(_git_pkg(cache=True)._cache_versions(self.SHA),
                             [self.SHA])

    def test_off_tries_bare_then_off_key(self):
        p = _git_pkg(cache=True)
        p.submodules_spec = parse_spec(False)
        self.assertEqual(p._cache_versions(self.SHA),
                         [self.SHA, self.SHA + "_sm_off"])

    def test_off_store_key_depends_on_gitmodules(self):
        import os
        import tempfile
        p = _git_pkg(cache=True)
        p.submodules_spec = parse_spec(False)
        d = tempfile.mkdtemp()
        self.assertEqual(p._cache_store_version(self.SHA, d), self.SHA)
        open(os.path.join(d, ".gitmodules"), "w").close()
        self.assertEqual(p._cache_store_version(self.SHA, d),
                         self.SHA + "_sm_off")

    def test_patched_base_carries_the_suffix(self):
        p = _git_pkg(cache=True)
        p.submodules_spec = parse_spec(True)
        self.assertEqual(p.cache_base_version(self.SHA),
                         self.SHA + p.submodule_spec().cache_suffix())
        p.submodules_spec = parse_spec(False)
        self.assertEqual(p.cache_base_version(self.SHA), self.SHA + "_sm_off")

    def test_patched_off_and_legacy_never_share_a_key(self):
        # Legacy (and earlier releases) store a patched variant with every
        # submodule under the bare base; an off variant has none of them.
        off = _git_pkg(cache=True)
        off.submodules_spec = parse_spec(False)
        with patch.object(gsm, "IMPLICIT_DEFAULT", "legacy"):
            legacy = _git_pkg(cache=True)
            self.assertEqual(legacy.cache_base_version(self.SHA), self.SHA)
            self.assertNotEqual(off.cache_base_version(self.SHA),
                                legacy.cache_base_version(self.SHA))

    def test_keys_are_plain_cache_names(self):
        from ivpm.cache_names import is_plain_key
        p = _git_pkg(cache=True)
        p.submodules_spec = parse_spec(True)
        self.assertTrue(is_plain_key(p._cache_versions(self.SHA)[0]))
        self.assertTrue(is_plain_key(self.SHA + "_sm_off"))


class TestDepsSourceIdentity(unittest.TestCase):
    """A parent's copy satisfies this package only if it holds the submodules
    this package asks for."""

    SHA = "b" * 40

    def _parent(self, modules, spec=None):
        spec = spec or parse_spec(True)
        return {"src": "git", "commit_resolved": self.SHA,
                "submodules": {"spec": spec.to_lock(True), "modules": modules}}

    def _mod(self, path, ok=True):
        return {"path": path, "initialized": ok,
                "status": "ok" if ok else "not-requested"}

    def _pkg(self, spec):
        p = _git_pkg(cache=True, resolved_commit=self.SHA)
        p.submodules_spec = spec
        return p

    def test_off_and_legacy_defer(self):
        self.assertIsNone(self._pkg(parse_spec(False)).matches_lock_entry({}))
        self.assertIsNone(self._pkg(gsm.LEGACY).matches_lock_entry({}))

    def test_legacy_rejects_a_parent_without_its_submodules(self):
        parent = {"src": "git", "commit_resolved": self.SHA,
                  "submodules": {"spec": {"enabled": False},
                                 "modules": [self._mod("a", ok=False)]}}
        self.assertFalse(self._pkg(gsm.LEGACY).matches_lock_entry(parent))
        # ...but a parent with no submodules at all is fine.
        parent["submodules"]["modules"] = []
        self.assertIsNone(self._pkg(gsm.LEGACY).matches_lock_entry(parent))

    def test_all_initialized_matches(self):
        p = self._pkg(parse_spec(True))
        self.assertTrue(p.matches_lock_entry(self._parent(
            [self._mod("a"), self._mod("a/b")])))

    def test_missing_nested_fails_recursive(self):
        p = self._pkg(parse_spec(True))
        self.assertFalse(p.matches_lock_entry(self._parent(
            [self._mod("a"), self._mod("a/b", ok=False)])))

    def test_missing_nested_ok_when_not_recursive(self):
        p = self._pkg(parse_spec({"recursive": False}))
        self.assertTrue(p.matches_lock_entry(self._parent(
            [self._mod("a"), self._mod("a/b", ok=False)])))

    def test_paths_subset(self):
        p = self._pkg(parse_spec({"paths": ["a"]}))
        self.assertTrue(p.matches_lock_entry(self._parent(
            [self._mod("a"), self._mod("c", ok=False)])))
        p = self._pkg(parse_spec({"paths": ["c"]}))
        self.assertFalse(p.matches_lock_entry(self._parent(
            [self._mod("a"), self._mod("c", ok=False)])))

    def test_update_none_is_ignored(self):
        p = self._pkg(parse_spec(True))
        self.assertTrue(p.matches_lock_entry(self._parent(
            [{"path": "a", "initialized": False, "status": "update-none"}])))

    def test_commit_mismatch_and_legacy_parent(self):
        p = self._pkg(parse_spec(True))
        e = self._parent([])
        e["commit_resolved"] = "c" * 40
        self.assertFalse(p.matches_lock_entry(e))
        legacy = {"src": "git", "commit_resolved": self.SHA}
        self.assertFalse(p.matches_lock_entry(legacy))


class TestStatusSummary(unittest.TestCase):

    def _st(self, subs):
        return PkgVcsStatus(name="p", src_type="git", path="/p", vcs="git",
                            submodules=subs)

    def test_none(self):
        self.assertEqual(self._st([]).submodule_summary(), "")

    def test_all_ok(self):
        st = self._st([SubmoduleStatus("a", "ok", in_scope=True),
                       SubmoduleStatus("b", "ok", in_scope=True)])
        self.assertEqual(st.submodule_summary(), "submodules 2/2")
        self.assertEqual(st.submodule_problems, [])

    def test_problems_are_grouped(self):
        st = self._st([SubmoduleStatus("a", "ok", in_scope=True),
                       SubmoduleStatus("b", "uninitialized", in_scope=True),
                       SubmoduleStatus("c", "moved", in_scope=True),
                       SubmoduleStatus("d", "ok", in_scope=True, dirty=True)])
        s = st.submodule_summary()
        self.assertTrue(s.startswith("submodules 2/4 ("), s)
        self.assertIn("1 uninitialized: b", s)
        self.assertIn("1 moved: c", s)
        self.assertIn("1 dirty: d", s)
        self.assertEqual([m.path for m in st.submodule_problems], ["b", "c", "d"])

    def test_not_requested(self):
        st = self._st([SubmoduleStatus("a", "not-requested")])
        self.assertIn("not requested", st.submodule_summary())
        self.assertEqual(st.submodule_problems, [])


if __name__ == "__main__":
    unittest.main()
