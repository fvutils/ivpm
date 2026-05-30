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
