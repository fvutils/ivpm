"""
Nested dependencies -- PR 4: materialization and cache invariants.

A cache hit normally materializes a package as a read-only symlink into the
shared cache. A package resolved with ``deps-mode: nested`` has to own its
directory so it can hold a deps-dir, so the resolver promotes it to a writable
copy after the fact (design §6.2, plan P1).

The invariant that matters: promoting changes only *this workspace's* layout.
The cache entry is untouched, still read-only, and still one entry per
(package, version) no matter how many workspaces nest it.

See nested-deps-impl-plan.md §5.
"""
import json
import os
import stat
import subprocess

from .test_base import TestBase

from ivpm.dep_materialize import promote_to_writable


class TestPromotePrimitive(TestBase):
    """promote_to_writable in isolation."""

    class _Pkg:
        def __init__(self, path):
            self.path = path
            self.name = "p"

    def test_no_op_on_a_real_directory(self):
        real = os.path.join(self.testdir, "real")
        os.makedirs(real)
        self.assertFalse(promote_to_writable(self._Pkg(real)))
        self.assertTrue(os.path.isdir(real))

    def test_no_op_on_a_missing_path(self):
        self.assertFalse(promote_to_writable(
            self._Pkg(os.path.join(self.testdir, "nope"))))

    def test_no_op_when_path_is_none(self):
        pkg = self._Pkg(None)
        self.assertFalse(promote_to_writable(pkg))

    def test_converts_a_symlink_to_a_writable_copy(self):
        target = os.path.join(self.testdir, "target")
        os.makedirs(os.path.join(target, "sub"))
        with open(os.path.join(target, "sub", "f.txt"), "w") as fp:
            fp.write("content")
        # Mimic a cache entry: read-only.
        os.chmod(os.path.join(target, "sub", "f.txt"), stat.S_IRUSR)

        link = os.path.join(self.testdir, "link")
        os.symlink(target, link)

        self.assertTrue(promote_to_writable(self._Pkg(link)))

        self.assertFalse(os.path.islink(link))
        self.assertTrue(os.path.isdir(link))
        copied = os.path.join(link, "sub", "f.txt")
        with open(copied) as fp:
            self.assertEqual(fp.read(), "content")
        # Write bits restored, so the copy can host a deps-dir.
        self.assertTrue(os.stat(copied).st_mode & stat.S_IWUSR)
        self.assertTrue(os.stat(link).st_mode & stat.S_IWUSR)
        # The source is untouched and still read-only.
        self.assertFalse(
            os.stat(os.path.join(target, "sub", "f.txt")).st_mode & stat.S_IWUSR)

    def test_is_idempotent(self):
        target = os.path.join(self.testdir, "target")
        os.makedirs(target)
        link = os.path.join(self.testdir, "link")
        os.symlink(target, link)

        self.assertTrue(promote_to_writable(self._Pkg(link)))
        before = os.stat(link).st_ino
        # Second call sees a real directory and does nothing.
        self.assertFalse(promote_to_writable(self._Pkg(link)))
        self.assertEqual(os.stat(link).st_ino, before)

    def test_leaves_no_staging_dir_behind(self):
        target = os.path.join(self.testdir, "target")
        os.makedirs(target)
        link = os.path.join(self.testdir, "link")
        os.symlink(target, link)
        promote_to_writable(self._Pkg(link))
        self.assertFalse(os.path.exists(link + ".ivpm-promote"))


class TestNestedCacheIntegration(TestBase):
    """A cached package used as a nested boundary."""

    def setUp(self):
        super().setUp()
        self.cache_dir = os.path.join(self.testdir, ".ivpm_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.environ["IVPM_CACHE"] = self.cache_dir

    def tearDown(self):
        os.environ.pop("IVPM_CACHE", None)
        super().tearDown()

    def _git_repo(self, name, ivpm_yaml, files=None):
        path = os.path.join(self.testdir, "repos", name)
        os.makedirs(path, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
        })
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

    def _cached_tree(self):
        """A cached git 'boundary' whose only dep is a local dir package."""
        boundary = self._git_repo(
            "boundary",
            "package:\n"
            "  name: boundary\n"
            "  deps-mode: nested\n"
            "  dep-sets:\n"
            "    - name: default-dev\n"
            "      deps:\n"
            "        - name: nested_libD\n"
            "          url: file://${DATA_DIR}/nested_libD\n"
            "          src: dir\n"
            "          link: false\n",
            files={"marker.txt": "boundary content\n"})

        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cache_nest_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: boundary\n"
                    "              url: file://%s\n"
                    "              src: git\n"
                    "              cache: true\n" % boundary)
        return boundary

    def _cache_entries(self, pkg="boundary"):
        d = os.path.join(self.cache_dir, pkg)
        if not os.path.isdir(d):
            return []
        return sorted(n for n in os.listdir(d) if not n.endswith(".meta.json"))

    def test_cached_boundary_becomes_a_writable_copy(self):
        self._cached_tree()
        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "boundary")
        # Promoted: a real directory, not the usual cache symlink.
        self.assertFalse(os.path.islink(pkg_dir))
        self.assertTrue(os.path.isdir(pkg_dir))

        # Content matches the cache entry, and is writable.
        marker = os.path.join(pkg_dir, "marker.txt")
        with open(marker) as fp:
            self.assertEqual(fp.read(), "boundary content\n")
        self.assertTrue(os.stat(marker).st_mode & stat.S_IWUSR)

        # It hosts its own deps-dir.
        self.assertTrue(os.path.isdir(
            os.path.join(pkg_dir, "packages", "nested_libD")))

    def test_cache_entry_is_unharmed(self):
        self._cached_tree()
        self.ivpm_update(skip_venv=True)

        entries = self._cache_entries()
        self.assertEqual(len(entries), 1, "expected exactly one cache entry")
        entry = os.path.join(self.cache_dir, "boundary", entries[0])

        # Still populated, still read-only, and NOT carrying the consumer's
        # nested deps-dir (nesting is a property of the workspace, not the
        # cached artifact).
        cached_marker = os.path.join(entry, "marker.txt")
        self.assertTrue(os.path.isfile(cached_marker))
        self.assertFalse(os.stat(cached_marker).st_mode & stat.S_IWUSR)
        self.assertFalse(os.path.exists(os.path.join(entry, "packages")))

    def test_one_cache_entry_whether_flat_or_nested(self):
        # Same package, same version, resolved flat here -- the cache key is a
        # function of source and version only, so still one entry.
        boundary = self._cached_tree()
        self.ivpm_update(skip_venv=True)
        first = self._cache_entries()

        # Re-resolve the same package with nesting turned off.
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cache_nest_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: boundary\n"
                    "              url: file://%s\n"
                    "              src: git\n"
                    "              cache: true\n"
                    "              deps-mode: flatten\n" % boundary)
        import shutil
        shutil.rmtree(os.path.join(self.testdir, "packages"))
        self.ivpm_update(skip_venv=True)

        self.assertEqual(self._cache_entries(), first)
        # Flat: back to the ordinary cache symlink.
        self.assertTrue(os.path.islink(
            os.path.join(self.testdir, "packages", "boundary")))
        self.assertTrue(os.path.isdir(
            os.path.join(self.testdir, "packages", "nested_libD")))

    def test_second_update_does_not_re_copy(self):
        self._cached_tree()
        self.ivpm_update(skip_venv=True)

        pkg_dir = os.path.join(self.testdir, "packages", "boundary")
        before = os.stat(os.path.join(pkg_dir, "marker.txt")).st_ino

        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.islink(pkg_dir))
        self.assertEqual(
            os.stat(os.path.join(pkg_dir, "marker.txt")).st_ino, before,
            "the boundary was re-copied on an unchanged re-run")

    def test_nested_deps_still_use_the_cache(self):
        # The boundary is copied; a *cacheable* package inside its scope is
        # still symlinked into the shared cache, in the nested deps-dir.
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
            "          src: git\n"
            "          cache: true\n" % inner)
        self.mkFile("ivpm.yaml",
                    "package:\n"
                    "    name: cache_nest_root\n"
                    "    dep-sets:\n"
                    "        - name: default-dev\n"
                    "          deps:\n"
                    "            - name: boundary\n"
                    "              url: file://%s\n"
                    "              src: git\n"
                    "              cache: true\n" % boundary)
        self.ivpm_update(skip_venv=True)

        inner_dir = os.path.join(
            self.testdir, "packages", "boundary", "packages", "inner")
        self.assertTrue(os.path.islink(inner_dir),
                        "a cached package in a nested scope should still link "
                        "into the shared cache")
        # ... and it links into the cache, not somewhere in the root deps-dir.
        self.assertTrue(
            os.path.realpath(inner_dir).startswith(
                os.path.realpath(self.cache_dir)))
        self.assertTrue(os.path.isfile(os.path.join(inner_dir, "inner.txt")))
        # Nothing leaked into the root deps-dir.
        self.assertFalse(os.path.exists(
            os.path.join(self.testdir, "packages", "inner")))

    def test_last_linked_still_advances_for_nested_cache_entries(self):
        self.test_nested_deps_still_use_the_cache()

        inner_dir = os.path.join(
            self.testdir, "packages", "boundary", "packages", "inner")
        version = os.path.basename(os.path.realpath(inner_dir))
        meta_path = os.path.join(self.cache_dir, "inner", version + ".meta.json")
        self.assertTrue(os.path.isfile(meta_path))

        import time
        with open(meta_path) as fp:
            meta = json.load(fp)
        meta["last_linked"] = time.time() - 10 * 24 * 60 * 60
        with open(meta_path, "w") as fp:
            json.dump(meta, fp)

        self.ivpm_update(skip_venv=True)

        with open(meta_path) as fp:
            refreshed = json.load(fp)
        self.assertGreater(refreshed["last_linked"], meta["last_linked"])
