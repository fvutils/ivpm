"""
Unit tests for the Stage-2 ``src: ivpm.yaml`` dep-set factory source.

A factory dependency reads a referenced ivpm.yaml, selects one of its dep-sets,
and folds those packages into the consumer's dep-set -- without occupying a
packages-dir slot. These tests use **local** factory files (no network) and
drive the real ``PackageUpdater`` so the updater-recursion integration is
exercised end to end. Leaf packages are ``src: pypi`` (whose ``update()`` is a
no-op offline), so nothing is installed.
"""
import argparse
import json
import os
import shutil
import tempfile
import unittest

from ivpm.yamlsrc import SrcLoaderError
from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.package_updater import PackageUpdater
from ivpm.package_lock import write_lock, read_lock
from ivpm.pkg_types.package_ivpm_yaml import PackageIvpmYaml


class _StubHandler:
    """Minimal package handler: the updater only calls these two hooks."""
    def on_leaf_pre_load(self, pkg, update_info):
        pass

    def on_leaf_post_load(self, pkg, update_info):
        pass


class _FactoryTestBase(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ivpm-factory-")
        self.deps_dir = os.path.join(self._dir, "packages")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self._dir, name)
        with open(path, "w") as fp:
            fp.write(content)
        return path

    def _read_dep_set(self, consumer_path, dep_set="default"):
        with open(consumer_path) as fp:
            proj = IvpmYamlReader().read(fp, consumer_path)
        return proj.get_dep_set(dep_set)

    def _run_update(self, ds):
        updater = PackageUpdater(
            self.deps_dir, _StubHandler(),
            args=argparse.Namespace(jobs=1))
        all_pkgs = updater.update(ds)
        return updater, all_pkgs


class TestFactoryExpansion(_FactoryTestBase):

    def _basic_factory(self, dep_set_attr="\n          dep-set: core"):
        factory = self._write("tools.yaml",
            "package:\n"
            "  name: tools-factory\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: pyyaml\n"
            "          src: pypi\n"
            "        - name: jinja2\n"
            "          src: pypi\n")
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps:\n"
            "        - name: core-tools\n"
            "          src: ivpm.yaml\n"
            "          url: %s%s\n" % (factory, dep_set_attr))
        return factory, consumer

    def test_basic_expansion(self):
        """Case 1: factory's two leaves land in the consumer's resolved set,
        resolved_by the factory node; the factory itself is virtual."""
        factory, consumer = self._basic_factory()
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        self.assertIn("pyyaml", all_pkgs.keys())
        self.assertIn("jinja2", all_pkgs.keys())
        self.assertEqual(all_pkgs["pyyaml"].resolved_by, "core-tools")
        self.assertEqual(all_pkgs["jinja2"].resolved_by, "core-tools")

        factory_node = all_pkgs["core-tools"]
        self.assertTrue(getattr(factory_node, "virtual", False))
        self.assertEqual(factory_node.src_type, "ivpm.yaml")

    def test_no_packages_dir_entry(self):
        """Case 3: the factory creates no deps_dir/<name> dir and does not
        appear in the lock's normal packages map (only under ivpm_sources)."""
        factory, consumer = self._basic_factory()
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        self.assertFalse(os.path.exists(os.path.join(self.deps_dir, "core-tools")))

        write_lock(self.deps_dir, all_pkgs)
        lock = read_lock(os.path.join(self.deps_dir, "package-lock.json"))
        self.assertNotIn("core-tools", lock["packages"])
        self.assertIn("ivpm_sources", lock)
        self.assertIn(factory, lock["ivpm_sources"])
        # leaves are in the normal packages map
        self.assertIn("pyyaml", lock["packages"])

    def test_provenance_and_fingerprint(self):
        """Case 2 + 8: from_ivpm_source on each leaf; fingerprint in
        ivpm_sources, and it changes when the factory content changes."""
        factory, consumer = self._basic_factory()
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        origin = "%s#core" % factory
        self.assertEqual(all_pkgs["pyyaml"].from_ivpm_source, origin)
        self.assertEqual(all_pkgs["jinja2"].from_ivpm_source, origin)

        write_lock(self.deps_dir, all_pkgs)
        lock = read_lock(os.path.join(self.deps_dir, "package-lock.json"))
        self.assertEqual(lock["packages"]["pyyaml"]["from_ivpm_source"], origin)
        src_entry = lock["ivpm_sources"][factory]
        fp1 = src_entry["fingerprint"]
        self.assertTrue(fp1 and fp1.startswith("sha256:"))
        self.assertEqual(src_entry["dep_set"], "core")

        # Change factory content -> fingerprint changes
        with open(factory, "a") as f:
            f.write("        - name: extra\n          src: pypi\n")
        ds2 = self._read_dep_set(consumer)
        _, all_pkgs2 = self._run_update(ds2)
        fp2 = all_pkgs2["core-tools"].resolved_fingerprint
        self.assertNotEqual(fp1, fp2)

    def test_dep_set_default(self):
        """Case 4: omit dep-set: -> defaults to the consuming dep-set's name."""
        # Factory defines a dep-set named 'core'; consumer's dep-set is also
        # 'core', so the omitted dep-set defaults to it.
        factory = self._write("tools.yaml",
            "package:\n"
            "  name: tools-factory\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: pyyaml\n"
            "          src: pypi\n")
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: core-tools\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n" % factory)
        ds = self._read_dep_set(consumer, "core")
        updater, all_pkgs = self._run_update(ds)
        self.assertEqual(all_pkgs["core-tools"].dep_set, "core")
        self.assertIn("pyyaml", all_pkgs.keys())


class TestFactoryMultiDepSet(_FactoryTestBase):
    """Pulling several dep-sets from one factory. The supported idiom is one
    dependency entry per dep-set, each with a distinct name; the list form
    (``dep-set: [a, b]``) is rejected -- see test_dep_set_list_is_rejected."""

    def _factory(self):
        return self._write("tools.yaml",
            "package:\n"
            "  name: tools-factory\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: pyyaml\n"
            "          src: pypi\n"
            "    - name: extras\n"
            "      deps:\n"
            "        - name: jinja2\n"
            "          src: pypi\n"
            "    - name: dev\n"
            "      deps:\n"
            "        - name: pytest\n"
            "          src: pypi\n")

    def _consumer(self, factory, entries):
        """entries: list of (alias, dep_set) -> one factory dep entry each."""
        lines = ["package:",
                 "  name: consumer",
                 "  dep-sets:",
                 "    - name: default",
                 "      deps:"]
        for alias, ds in entries:
            lines += ["        - name: %s" % alias,
                      "          src: ivpm.yaml",
                      "          url: %s" % factory,
                      "          dep-set: %s" % ds]
        return self._write("ivpm.yaml", "\n".join(lines) + "\n")

    def test_multiple_dep_sets_merge(self):
        """One entry per dep-set folds the union of their leaves into the
        consumer; each leaf records the specific dep-set (and entry) it came
        from."""
        factory = self._factory()
        consumer = self._consumer(
            factory, [("t-core", "core"), ("t-extras", "extras"), ("t-dev", "dev")])
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        for leaf in ("pyyaml", "jinja2", "pytest"):
            self.assertIn(leaf, all_pkgs.keys())

        self.assertEqual(all_pkgs["pyyaml"].from_ivpm_source, "%s#core" % factory)
        self.assertEqual(all_pkgs["jinja2"].from_ivpm_source, "%s#extras" % factory)
        self.assertEqual(all_pkgs["pytest"].from_ivpm_source, "%s#dev" % factory)
        self.assertEqual(all_pkgs["pyyaml"].resolved_by, "t-core")
        self.assertEqual(all_pkgs["jinja2"].resolved_by, "t-extras")
        self.assertEqual(all_pkgs["pytest"].resolved_by, "t-dev")

    def test_leaf_provenance_recorded_in_lock(self):
        """Each folded leaf carries its originating dep-set in the lock's
        packages map -- durable per-leaf provenance, independent of the
        url-keyed ivpm_sources summary."""
        factory = self._factory()
        consumer = self._consumer(
            factory, [("t-core", "core"), ("t-extras", "extras")])
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        write_lock(self.deps_dir, all_pkgs)
        lock = read_lock(os.path.join(self.deps_dir, "package-lock.json"))
        self.assertEqual(lock["packages"]["pyyaml"]["from_ivpm_source"], "%s#core" % factory)
        self.assertEqual(lock["packages"]["jinja2"]["from_ivpm_source"], "%s#extras" % factory)

    def test_earlier_entry_wins_on_collision(self):
        """When two entries contribute a package of the same name, the
        earlier-listed entry wins (the updater keeps the first resolution)."""
        factory = self._write("tools.yaml",
            "package:\n"
            "  name: tools-factory\n"
            "  dep-sets:\n"
            "    - name: a\n"
            "      deps:\n"
            "        - name: shared\n"
            "          src: pypi\n"
            "    - name: b\n"
            "      deps:\n"
            "        - name: shared\n"
            "          src: pypi\n")
        consumer = self._consumer(factory, [("t-a", "a"), ("t-b", "b")])
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)
        # 't-a' is listed first, so its 'shared' wins.
        self.assertEqual(all_pkgs["shared"].from_ivpm_source, "%s#a" % factory)
        self.assertEqual(all_pkgs["shared"].resolved_by, "t-a")

    def test_dep_set_list_is_rejected(self):
        """The old list form (`dep-set: [a, b]`) is no longer supported: each
        entry must name exactly one dep-set."""
        factory = self._factory()
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps:\n"
            "        - name: core-tools\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n"
            "          dep-set: [core, extras]\n" % factory)
        with self.assertRaises(SrcLoaderError):
            self._read_dep_set(consumer)


class TestFactoryErrors(_FactoryTestBase):

    def test_missing_dep_set_is_fatal(self):
        """Case 5: factory does not contain the requested dep-set -> fatal."""
        factory = self._write("tools.yaml",
            "package:\n"
            "  name: tools-factory\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: pyyaml\n"
            "          src: pypi\n")
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps:\n"
            "        - name: core-tools\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n"
            "          dep-set: nonexistent\n" % factory)
        ds = self._read_dep_set(consumer)
        with self.assertRaises(SrcLoaderError):
            self._run_update(ds)

    def test_cycle_is_fatal(self):
        """Case 7: a factory that references itself -> fatal."""
        # a.yaml's 'default' dep-set contains a factory dep that points back at
        # a.yaml -> the chain guard fires on the second visit.
        a = os.path.join(self._dir, "a.yaml")
        with open(a, "w") as f:
            f.write(
                "package:\n"
                "  name: a-factory\n"
                "  dep-sets:\n"
                "    - name: default\n"
                "      deps:\n"
                "        - name: a-self\n"
                "          src: ivpm.yaml\n"
                "          url: %s\n"
                "          dep-set: default\n" % a)
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps:\n"
            "        - name: fa\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n"
            "          dep-set: default\n" % a)
        ds = self._read_dep_set(consumer)
        with self.assertRaises(SrcLoaderError) as ctx:
            self._run_update(ds)
        self.assertIn("Cyclic", str(ctx.exception))


class TestFactoryTransitive(_FactoryTestBase):

    def test_transitive_factory(self):
        """Case 6: a factory's dep-set contains a dep that is itself a factory;
        the grandchild leaves resolve."""
        grandchild = self._write("grand.yaml",
            "package:\n"
            "  name: grand-factory\n"
            "  dep-sets:\n"
            "    - name: g\n"
            "      deps:\n"
            "        - name: grandleaf\n"
            "          src: pypi\n")
        middle = self._write("mid.yaml",
            "package:\n"
            "  name: mid-factory\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: midleaf\n"
            "          src: pypi\n"
            "        - name: grand-tools\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n"
            "          dep-set: g\n" % grandchild)
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps:\n"
            "        - name: mid-tools\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n"
            "          dep-set: core\n" % middle)
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)
        self.assertIn("midleaf", all_pkgs.keys())
        self.assertIn("grandleaf", all_pkgs.keys())
        # provenance distinguishes the two factory layers
        self.assertEqual(all_pkgs["grandleaf"].from_ivpm_source,
                         "%s#g" % grandchild)


class TestFactoryNameCollision(_FactoryTestBase):
    """Regression: a factory dep whose alias name equals a package inside the
    dep-set it references. The factory node is virtual (installs nothing), so it
    must not shadow the real same-named package in the updater's name-keyed
    dedup set -- otherwise the real package is silently never fetched.

    Reproduces the real-world case of a `gcc-riscv` factory dep pointing at a
    dep-set that also contains a `gcc-riscv` release package.
    """

    def _colliding_factory(self):
        # Factory dep-set 'core' contains a leaf named 'gcc-riscv'...
        factory = self._write("tools.yaml",
            "package:\n"
            "  name: tools-factory\n"
            "  dep-sets:\n"
            "    - name: core\n"
            "      deps:\n"
            "        - name: gcc-riscv\n"
            "          src: pypi\n")
        # ...and the consumer's factory dep is *also* named 'gcc-riscv'.
        consumer = self._write("ivpm.yaml",
            "package:\n"
            "  name: consumer\n"
            "  dep-sets:\n"
            "    - name: default\n"
            "      deps:\n"
            "        - name: gcc-riscv\n"
            "          src: ivpm.yaml\n"
            "          url: %s\n"
            "          dep-set: core\n" % factory)
        return factory, consumer

    def test_colliding_leaf_is_still_resolved(self):
        """The real same-named leaf resolves and replaces the virtual factory
        node; before the fix it was de-duped away and never fetched."""
        factory, consumer = self._colliding_factory()
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        self.assertIn("gcc-riscv", all_pkgs.keys())
        node = all_pkgs["gcc-riscv"]
        # The surviving node must be the *real* leaf, not the virtual factory.
        self.assertFalse(getattr(node, "virtual", False),
                         "virtual factory shadowed the real same-named package")
        # It carries the folded-leaf provenance, proving it came from the dep-set.
        self.assertEqual(node.from_ivpm_source, "%s#core" % factory)
        self.assertEqual(node.resolved_by, "gcc-riscv")

    def test_colliding_leaf_in_lock_packages(self):
        """The real leaf lands in the lock's normal `packages` map (not left as
        only an `ivpm_sources` virtual entry)."""
        factory, consumer = self._colliding_factory()
        ds = self._read_dep_set(consumer)
        updater, all_pkgs = self._run_update(ds)

        write_lock(self.deps_dir, all_pkgs)
        lock = read_lock(os.path.join(self.deps_dir, "package-lock.json"))
        self.assertIn("gcc-riscv", lock["packages"])
        self.assertEqual(lock["packages"]["gcc-riscv"]["from_ivpm_source"],
                         "%s#core" % factory)


class TestFactoryLockMatch(_FactoryTestBase):

    def test_spec_matches_lock(self):
        """Case 10: spec_matches_lock detects url / dep-set changes."""
        pkg = PackageIvpmYaml("core-tools")
        pkg.url = "https://example.com/tools.yaml"
        pkg.dep_set = "core"
        entry = pkg.get_lock_entry()
        self.assertTrue(pkg.spec_matches_lock(entry))

        changed_url = dict(entry, url="https://example.com/other.yaml")
        self.assertFalse(pkg.spec_matches_lock(changed_url))

        changed_ds = dict(entry, dep_set="extras")
        self.assertFalse(pkg.spec_matches_lock(changed_ds))


if __name__ == "__main__":
    unittest.main()
