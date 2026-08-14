"""
Nested dependencies -- PR 5: package-lock.json v2.

v2 keys the ``packages`` map by *scope path* rather than by bare package name,
so a nested workspace can record two versions of one package. For a flat
workspace a scope path IS the bare name, so the only visible difference from v1
is the version number and an explicit ``name`` field.

See nested-deps-design.md §7.3 and nested-deps-impl-plan.md §6.
"""
import json
import os

from .test_base import TestBase

from ivpm.package_lock import (
    LOCK_VERSION, SUPPORTED_LOCK_VERSIONS, IvpmLockReader, read_lock)


def _dep(name, data_pkg, extra=""):
    return ("                    - name: %s\n"
            "                      url: file://${DATA_DIR}/%s\n"
            "                      src: dir\n"
            "                      link: false\n%s" % (name, data_pkg, extra))


class TestLockV2(TestBase):

    def _root(self, deps, package_extra=""):
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: nested_root\n"
                    "%s"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "%s" % (package_extra, deps))

    def _lock(self):
        with open(os.path.join(self.testdir, "packages",
                               "package-lock.json")) as fp:
            return json.load(fp)

    # -- flat: shape-compatible with v1 ------------------------------------

    def test_flat_lock_keys_are_bare_names(self):
        self._root(_dep("nested_libC", "nested_libC"))
        self.ivpm_update(skip_venv=True)

        lock = self._lock()
        self.assertEqual(lock["ivpm_lock_version"], 2)
        self.assertEqual(sorted(lock["packages"].keys()),
                         ["nested_libC", "nested_libD"])
        for key, entry in lock["packages"].items():
            self.assertEqual(entry["name"], key)
            # No scope field on a root-scope entry.
            self.assertNotIn("scope", entry)
            self.assertNotIn("deps_mode", entry)

    def test_flat_lock_differs_from_v1_only_by_version_and_name(self):
        self._root(_dep("nested_libC", "nested_libC"))
        self.ivpm_update(skip_venv=True)

        for entry in self._lock()["packages"].values():
            extra = set(entry) - {
                "src", "resolved_by", "dep_set", "reproducible", "path", "name"}
            self.assertEqual(extra, set(),
                             "unexpected new v2 fields on a flat entry: %s" % extra)

    # -- nested ------------------------------------------------------------

    def test_nested_keys_are_scope_paths(self):
        self._root(_dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        packages = self._lock()["packages"]
        self.assertIn("nested_toolB", packages)
        self.assertIn("nested_toolB/packages/nested_libA", packages)
        self.assertIn("nested_toolB/packages/nested_libC", packages)
        self.assertIn(
            "nested_toolB/packages/nested_libC/packages/nested_libD", packages)

    def test_nested_entries_carry_name_and_scope(self):
        self._root(_dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        entry = self._lock()["packages"]["nested_toolB/packages/nested_libA"]
        self.assertEqual(entry["name"], "nested_libA")
        self.assertEqual(entry["scope"], "nested_toolB/packages")

    def test_boundary_records_its_effective_mode(self):
        self._root(_dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        packages = self._lock()["packages"]
        # toolB opened a scope -> boundary.
        self.assertEqual(packages["nested_toolB"]["deps_mode"], "nested")
        # libC inherited nested and opened one too.
        self.assertEqual(
            packages["nested_toolB/packages/nested_libC"]["deps_mode"], "nested")
        # libA has no deps, so it never opened a scope.
        self.assertNotIn(
            "deps_mode", packages["nested_toolB/packages/nested_libA"])

    def test_two_versions_of_one_package_both_appear(self):
        # The whole point: flat, name-keyed v1 could only record one of these.
        self._root(_dep("nested_libA", "nested_libA_v2")
                   + _dep("nested_toolB", "nested_toolB"))
        self.ivpm_update(skip_venv=True)

        packages = self._lock()["packages"]
        self.assertIn("nested_libA", packages)
        self.assertIn("nested_toolB/packages/nested_libA", packages)
        self.assertEqual(packages["nested_libA"]["name"], "nested_libA")
        self.assertEqual(
            packages["nested_toolB/packages/nested_libA"]["name"], "nested_libA")
        # ... and they point at different sources.
        self.assertNotEqual(
            packages["nested_libA"]["path"],
            packages["nested_toolB/packages/nested_libA"]["path"])

    def test_cycle_elided_round_trips(self):
        self._root(_dep("nested_cyc_a", "nested_cyc_a"),
                   package_extra="    deps-mode: nested\n")
        self.ivpm_update(skip_venv=True)

        packages = self._lock()["packages"]
        elided = [k for k, e in packages.items() if "cycle_elided" in e]
        self.assertTrue(elided, "no entry recorded cycle_elided")
        key = elided[0]
        self.assertEqual(packages[key]["name"], "nested_cyc_a")
        # It names the enclosing scope that already provides the package.
        self.assertIsInstance(packages[key]["cycle_elided"], str)


class TestLockReaderCompat(TestBase):

    def _write_lock(self, version, packages):
        path = os.path.join(self.testdir, "package-lock.json")
        with open(path, "w") as fp:
            json.dump({"ivpm_lock_version": version,
                       "generated": "2020-01-01T00:00:00+00:00",
                       "packages": packages}, fp)
        return path

    def test_v1_is_still_supported(self):
        self.assertIn(1, SUPPORTED_LOCK_VERSIONS)
        self.assertIn(LOCK_VERSION, SUPPORTED_LOCK_VERSIONS)

    def test_v1_lock_reads_with_the_key_as_the_name(self):
        path = self._write_lock(1, {
            "pkg_a": {"src": "git", "url": "https://example.invalid/a.git",
                      "commit_resolved": "abc", "resolved_by": "root",
                      "dep_set": "default-dev"},
        })
        pkgs = IvpmLockReader(path).build_packages_info()
        self.assertEqual(sorted(pkgs.keys()), ["pkg_a"])
        self.assertEqual(pkgs["pkg_a"].name, "pkg_a")
        self.assertEqual(pkgs["pkg_a"].commit, "abc")

    def test_v1_lock_has_no_scope_pins(self):
        path = self._write_lock(1, {
            "pkg_a": {"src": "git", "url": "https://example.invalid/a.git",
                      "commit_resolved": "abc"},
        })
        self.assertEqual(IvpmLockReader(path).scope_pins(), {})

    def test_v2_root_entries_seed_reproduction(self):
        path = self._write_lock(2, {
            "pkg_a": {"name": "pkg_a", "src": "git",
                      "url": "https://example.invalid/a.git",
                      "commit_resolved": "abc"},
            "pkg_a/packages/inner": {
                "name": "inner", "scope": "pkg_a/packages", "src": "git",
                "url": "https://example.invalid/inner.git",
                "commit_resolved": "def"},
        })
        reader = IvpmLockReader(path)
        pkgs = reader.build_packages_info()
        # Only the root-scope entry seeds the queue; the nested one is rebuilt
        # from pkg_a's own manifest.
        self.assertEqual(sorted(pkgs.keys()), ["pkg_a"])

        pins = reader.scope_pins()
        self.assertEqual(sorted(pins.keys()), ["pkg_a/packages/inner"])
        self.assertEqual(pins["pkg_a/packages/inner"]["commit_resolved"], "def")

    def test_unsupported_version_still_rejected(self):
        path = self._write_lock(999, {})
        with self.assertRaises(ValueError) as ctx:
            read_lock(path)
        self.assertIn("not supported", str(ctx.exception))


class TestNestedReproduction(TestBase):
    """--lock-file over a nested workspace.

    Uses git packages: ``src: dir`` packages are recorded ``reproducible:
    false`` by design, so they are not a valid subject for this test.
    """

    def _git_repo(self, name, ivpm_yaml, files=None):
        path = os.path.join(self.testdir, "repos", name)
        os.makedirs(path, exist_ok=True)
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
                    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"})
        import subprocess
        subprocess.check_call(["git", "init", "-b", "main"], cwd=path)
        subprocess.check_call(["git", "config", "user.email", "t@example.com"], cwd=path)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=path)
        with open(os.path.join(path, "ivpm.yaml"), "w") as fp:
            fp.write(ivpm_yaml)
        for rel, content in (files or {}).items():
            with open(os.path.join(path, rel), "w") as fp:
                fp.write(content)
        subprocess.check_call(["git", "add", "-A"], cwd=path)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path, env=env)
        return path

    def _build(self):
        inner = self._git_repo(
            "inner",
            "package:\n  name: inner\n  dep-sets:\n"
            "    - name: default-dev\n      deps: []\n",
            files={"inner.txt": "inner\n"})
        boundary = self._git_repo(
            "boundary",
            "package:\n"
            "  name: boundary\n"
            "  deps-mode: nested\n"
            "  dep-sets:\n"
            "    - name: default-dev\n"
            "      deps:\n"
            "        - name: inner\n"
            "          url: file://%s\n"
            "          src: git\n" % inner)
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: repro_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: boundary\n"
                    "              url: file://%s\n"
                    "              src: git\n" % boundary)

    def test_lock_file_reproduces_a_nested_workspace(self):
        self._build()
        self.ivpm_update(skip_venv=True)

        lock_path = os.path.join(self.testdir, "packages", "package-lock.json")
        with open(lock_path) as fp:
            original = json.load(fp)["packages"]
        self.assertIn("boundary/packages/inner", original)
        locked_commit = original["boundary/packages/inner"]["commit_resolved"]
        self.assertTrue(locked_commit)

        import shutil
        saved = os.path.join(self.testdir, "saved-lock.json")
        shutil.copy(lock_path, saved)
        shutil.rmtree(os.path.join(self.testdir, "packages"))

        from ivpm.project_ops import ProjectOps

        class Args:
            anonymous_git = None
        ProjectOps(self.testdir, Args()).update(
            dep_set="default-dev", skip_venv=True, args=Args(),
            lock_file=saved)

        # Same shape on disk ...
        self.assertTrue(os.path.isdir(os.path.join(
            self.testdir, "packages", "boundary", "packages", "inner")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.testdir, "packages", "boundary", "packages", "inner",
            "inner.txt")))

        # ... the same lock keys, and the nested package on the same commit.
        with open(lock_path) as fp:
            reproduced = json.load(fp)["packages"]
        self.assertEqual(sorted(reproduced.keys()), sorted(original.keys()))
        self.assertEqual(
            reproduced["boundary/packages/inner"]["commit_resolved"],
            locked_commit)
