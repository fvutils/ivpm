#****************************************************************************
#* test_show_bom.py
#*
#* Tests for the documentation surface exposed through 'ivpm show':
#*   - prose carried into DepNode/DepGraph (from the manifest, not the lock)
#*   - the dep-set inheritance delta in 'show deps --json' / '--from'
#*   - 'ivpm show --schema' advertising the documentation keys
#*   - the new 'ivpm show bom' subcommand
#****************************************************************************
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_UNIT_DIR)
_ROOT_DIR = os.path.dirname(_TEST_DIR)
_SRC_DIR = os.path.join(_ROOT_DIR, "src")
_ENV = {**os.environ, "PYTHONPATH": _SRC_DIR}

sys.path.insert(0, _SRC_DIR)

from .test_base import venv_python


def _run(*args, cwd=None, check=True):
    cmd = [venv_python(), "-m", "ivpm"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, env=_ENV,
                            cwd=cwd or _ROOT_DIR)
    if check and result.returncode != 0:
        raise AssertionError(
            "Command %s failed (rc=%d):\nstdout: %s\nstderr: %s" % (
                cmd, result.returncode, result.stdout, result.stderr))
    return result.stdout, result.returncode, result.stderr


# A workspace with: a documented git dep (pinned, patched), a pypi dep with no
# prose at all, and a dep-set that inherits from another with one override.
ROOT_YAML = """
package:
  name: demo
  version: 1.0.0
  description: A demonstration workspace.
  doc: |
    Long-form prose about the project.

    Second paragraph.
  license: Apache-2.0
  homepage: https://demo.example
  documentation: https://docs.demo.example
  maintainers:
    - Alice <alice@example.com>
  dep-sets:
    - name: base
      description: The base set.
      deps:
        - name: somelib
          description: Vendor DPI shim.
          doc: |
            Pinned to a commit rather than a tag because upstream retags.
          url: https://example.com/somelib.git
          commit: a1b2c3d4e5f6
        - name: plainlib
          src: pypi
          version: "1.2"
    - name: sim
      description: Base plus the simulator VIP.
      doc: Use this one in CI.
      kind: collection
      uses: base
      deps:
        - name: somelib
          description: Vendor DPI shim (VIP-compatible build).
          url: https://example.com/somelib.git
          tag: v2.0
        - name: vip
          src: pypi
"""

LOCK = {
    "packages": {
        "somelib": {
            "src": "git",
            "resolved_by": "root",
            "dep_set": "base",
            "reproducible": True,
            "url": "https://example.com/somelib.git",
            "commit_requested": "a1b2c3d4e5f6",
            "commit_resolved": "a1b2c3d4e5f6789012345678",
            "cache": True,
            "patchset_id": "deadbeef",
            "patches": [{"name": "fix.patch", "source": "patches/fix.patch",
                         "md5": "0123456789abcdef", "strip": 1,
                         "directory": None}],
        },
        "plainlib": {
            "src": "pypi",
            "resolved_by": "root",
            "dep_set": "base",
            "reproducible": True,
            "version_requested": "1.2",
            "version_resolved": "1.2.3",
        },
        "locallib": {
            "src": "dir",
            "resolved_by": "root",
            "dep_set": "base",
            "reproducible": False,
            "path": "../locallib",
        },
    }
}


class _WorkspaceTest(unittest.TestCase):
    """Builds a hand-crafted workspace; no network, no 'ivpm update'."""

    ROOT_YAML = ROOT_YAML
    LOCK = LOCK
    # Packages whose materialized directory should carry upstream metadata
    UPSTREAM = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "ivpm.yaml"), "w") as f:
            f.write(textwrap.dedent(self.ROOT_YAML))
        self.deps_dir = os.path.join(self.tmp, "packages")
        os.makedirs(self.deps_dir, exist_ok=True)
        if self.LOCK is not None:
            with open(os.path.join(self.deps_dir, "package-lock.json"), "w") as f:
                json.dump(self.LOCK, f, indent=2)
        for name, files in self.UPSTREAM.items():
            d = os.path.join(self.deps_dir, name)
            os.makedirs(d, exist_ok=True)
            for fname, content in files.items():
                with open(os.path.join(d, fname), "w") as f:
                    f.write(textwrap.dedent(content))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _graph(self, dep_set=None):
        from ivpm.show.dep_loader import DepLoader
        return DepLoader(self.tmp, dep_set=dep_set).load()


class TestProseInDepGraph(_WorkspaceTest):
    """D1 -- prose reaches DepNode/DepGraph, and comes from the manifest."""

    UPSTREAM = {"somelib": {"ivpm.yaml": """
package:
  name: somelib
  license: MIT
  homepage: https://somelib.example
  documentation: https://docs.somelib.example
  dep-sets:
    - name: base
      deps: []
"""}}

    def test_root_prose(self):
        g = self._graph("base")
        self.assertEqual(g.description, "A demonstration workspace.")
        self.assertEqual(
            g.doc, "Long-form prose about the project.\n\nSecond paragraph.\n")

    def test_dep_prose(self):
        g = self._graph("base")
        nodes = {n.name: n for n in g.nodes}
        self.assertEqual(nodes["somelib"].description, "Vendor DPI shim.")
        self.assertIn("upstream retags", nodes["somelib"].doc)
        # A dependency with no prose stays None -- not "" and not the lock's.
        self.assertIsNone(nodes["plainlib"].description)
        self.assertIsNone(nodes["plainlib"].doc)

    def test_prose_not_taken_from_the_lock(self):
        """The lock records what you GOT; prose is what you DECLARED. Removing
        the lock must not remove the prose."""
        os.remove(os.path.join(self.deps_dir, "package-lock.json"))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            g = self._graph("base")
        nodes = {n.name: n for n in g.nodes}
        self.assertEqual(nodes["somelib"].description, "Vendor DPI shim.")

    def test_declared_spec_separate_from_resolved(self):
        g = self._graph("base")
        nodes = {n.name: n for n in g.nodes}
        self.assertEqual(nodes["somelib"].declared["commit"], "a1b2c3d4e5f6")
        self.assertEqual(nodes["somelib"].commit, "a1b2c3d4e5f6789012345678")

    def test_dep_set_info_delta(self):
        g = self._graph("sim")
        info = g.dep_set_info
        self.assertEqual(info.name, "sim")
        self.assertEqual(info.description, "Base plus the simulator VIP.")
        self.assertEqual(info.doc, "Use this one in CI.")
        self.assertEqual(info.kind, "collection")
        self.assertEqual(info.uses, ["base"])
        self.assertEqual(info.contains, ["plainlib", "somelib", "vip"])
        self.assertEqual(sorted(info.own), ["somelib", "vip"])
        self.assertEqual(info.inherited_from, {"plainlib": "base"})
        self.assertEqual(list(info.overrides), ["somelib"])
        self.assertEqual(info.overrides["somelib"].base, "base")
        self.assertEqual(info.overrides["somelib"].displaced["commit"],
                         "a1b2c3d4e5f6")


class TestShowDepsJson(_WorkspaceTest):
    """D1 -- the JSON surface is additive: new keys only."""

    def test_tree_json_carries_prose_and_delta(self):
        out, _, _ = _run("show", "deps", "--tree", "--json",
                         "-p", self.tmp, "-d", "sim")
        data = json.loads(out)
        self.assertEqual(data["description"], "A demonstration workspace.")
        self.assertIn("Second paragraph.", data["doc"])
        info = data["dep_set_info"]
        self.assertEqual(info["uses"], ["base"])
        self.assertEqual(info["inherited_from"], {"plainlib": "base"})
        self.assertEqual(info["overrides"]["somelib"]["base"], "base")

        # The prose reported is the OVERRIDING entry's, matching the package
        # that dep-set actually resolves to.
        somelib = next(d for d in data["deps"] if d["name"] == "somelib")
        self.assertEqual(somelib["description"],
                         "Vendor DPI shim (VIP-compatible build).")

    def test_flat_json_keys_are_additive(self):
        """Pre-existing consumers must still find every key they relied on."""
        out, _, _ = _run("show", "deps", "--json", "-p", self.tmp, "-d", "base")
        rows = json.loads(out)
        required = {"name", "src", "specifier", "shadowed", "also_requested_by",
                    "url", "branch", "tag", "commit", "version",
                    "version_resolved", "cache", "dep_set", "scope", "nested",
                    "cycle_elided"}
        for row in rows:
            self.assertTrue(required.issubset(row.keys()),
                            "missing: %s" % (required - row.keys()))
        self.assertIn("description", rows[0])


class TestShowSchema(unittest.TestCase):
    """D2 -- the documentation keys are discoverable via $schema completion."""

    @classmethod
    def setUpClass(cls):
        out, _, _ = _run("show", "--schema")
        cls.schema = json.loads(out)

    def _pkg(self):
        return self.schema["properties"]["package"]["properties"]

    def test_package_level_keys(self):
        props = self._pkg()
        for key in ("description", "doc", "license", "homepage",
                    "documentation", "maintainers"):
            self.assertIn(key, props)
        self.assertEqual(props["maintainers"]["type"], "array")

    def test_dep_set_level_keys(self):
        ds = self._pkg()["dep-sets"]["items"]["properties"]
        for key in ("description", "doc", "kind", "uses"):
            self.assertIn(key, ds)
        self.assertEqual(ds["kind"]["enum"], ["package", "collection"])

    def test_dependency_level_keys(self):
        dep = (self._pkg()["dep-sets"]["items"]["properties"]
               ["deps"]["items"]["properties"])
        self.assertIn("description", dep)
        self.assertIn("doc", dep)

    def test_pre_existing_keys_intact(self):
        props = self._pkg()
        self.assertIn("name", props)
        self.assertIn("version", props)
        self.assertIn("x-ivpm-sources", self.schema)


class TestShowBom(_WorkspaceTest):
    """D3 -- 'ivpm show bom', a projection over data that already exists."""

    UPSTREAM = {
        "somelib": {"ivpm.yaml": """
package:
  name: somelib
  license: MIT
  homepage: https://somelib.example
  documentation: https://docs.somelib.example
  dep-sets:
    - name: base
      deps: []
"""},
        "plainlib": {"pyproject.toml": """
[project]
name = "plainlib"
version = "1.2.3"
license = "BSD-3-Clause"
urls = { Homepage = "https://plainlib.example" }
"""},
    }

    def _bom(self, dep_set="base"):
        out, _, _ = _run("show", "bom", "--json", "-p", self.tmp, "-d", dep_set)
        return json.loads(out)

    def test_stable_shape(self):
        bom = self._bom()
        self.assertEqual(bom["project"], "demo")
        self.assertEqual(bom["version"], "1.0.0")
        self.assertEqual(bom["dep_set"], "base")
        self.assertEqual(bom["license"], "Apache-2.0")
        self.assertEqual(bom["homepage"], "https://demo.example")
        self.assertEqual(bom["documentation"], "https://docs.demo.example")
        self.assertEqual(bom["maintainers"], ["Alice <alice@example.com>"])
        self.assertTrue(bom["lock_available"])

        rows = {r["name"]: r for r in bom["packages"]}
        self.assertEqual(sorted(rows), ["plainlib", "somelib"])
        for row in rows.values():
            for key in ("name", "src", "description", "doc", "declared",
                        "version_resolved", "commit_resolved", "reproducible",
                        "cache", "patches", "patchset_id", "license",
                        "homepage", "documentation", "specifier", "dep_set",
                        "scope"):
                self.assertIn(key, row)

    def test_declared_vs_resolved(self):
        rows = {r["name"]: r for r in self._bom()["packages"]}
        somelib = rows["somelib"]
        self.assertEqual(somelib["declared"]["commit"], "a1b2c3d4e5f6")
        self.assertEqual(somelib["commit_resolved"], "a1b2c3d4e5f6789012345678")
        self.assertEqual(rows["plainlib"]["declared"]["version"], "1.2")
        self.assertEqual(rows["plainlib"]["version_resolved"], "1.2.3")

    def test_prose_reported(self):
        rows = {r["name"]: r for r in self._bom()["packages"]}
        self.assertEqual(rows["somelib"]["description"], "Vendor DPI shim.")
        self.assertIn("upstream retags", rows["somelib"]["doc"])

    def test_metadata_from_package_manifest_and_upstream(self):
        rows = {r["name"]: r for r in self._bom()["packages"]}
        # from the package's own ivpm.yaml
        self.assertEqual(rows["somelib"]["license"], "MIT")
        self.assertEqual(rows["somelib"]["documentation"],
                         "https://docs.somelib.example")
        # opportunistically, from its upstream pyproject.toml
        self.assertEqual(rows["plainlib"]["license"], "BSD-3-Clause")
        self.assertEqual(rows["plainlib"]["homepage"], "https://plainlib.example")

    def test_patched_dep_carries_fingerprints(self):
        rows = {r["name"]: r for r in self._bom()["packages"]}
        self.assertEqual(rows["somelib"]["patchset_id"], "deadbeef")
        self.assertEqual(rows["somelib"]["patches"][0]["md5"],
                         "0123456789abcdef")
        self.assertEqual(rows["plainlib"]["patches"], [])

    def test_reproducible_flags(self):
        rows = {r["name"]: r for r in self._bom()["packages"]}
        self.assertTrue(rows["somelib"]["reproducible"])
        self.assertTrue(rows["plainlib"]["reproducible"])

    def test_dir_source_is_not_reproducible(self):
        """A 'dir:' dependency cannot be pinned, and the BOM must say so."""
        with open(os.path.join(self.tmp, "ivpm.yaml"), "w") as f:
            f.write(textwrap.dedent("""
                package:
                  name: demo
                  dep-sets:
                    - name: base
                      deps:
                        - name: locallib
                          src: dir
                          url: ../locallib
                """))
        rows = {r["name"]: r for r in self._bom()["packages"]}
        self.assertIs(rows["locallib"]["reproducible"], False)

    def test_plain_output_renders(self):
        out, _, _ = _run("show", "bom", "--no-rich", "-p", self.tmp, "-d", "base")
        self.assertIn("demo", out)
        self.assertIn("somelib", out)
        self.assertIn("MIT", out)
        self.assertIn("Vendor DPI shim.", out)

    def test_output_to_file(self):
        dest = os.path.join(self.tmp, "bom.json")
        _run("show", "bom", "--json", "-p", self.tmp, "-d", "base", "-o", dest)
        with open(dest) as f:
            self.assertEqual(json.load(f)["project"], "demo")

    def test_no_lock_warns_but_still_reports(self):
        os.remove(os.path.join(self.deps_dir, "package-lock.json"))
        out, rc, err = _run("show", "bom", "--json", "--no-rich",
                            "-p", self.tmp, "-d", "base")
        self.assertEqual(rc, 0)
        self.assertIn("package-lock.json not found", err)
        bom = json.loads(out)
        self.assertFalse(bom["lock_available"])
        rows = {r["name"]: r for r in bom["packages"]}
        self.assertIsNone(rows["somelib"]["version_resolved"])
        self.assertEqual(rows["somelib"]["description"], "Vendor DPI shim.")

    def test_missing_project_errors_cleanly(self):
        empty = tempfile.mkdtemp()
        try:
            _, rc, err = _run("show", "bom", "-p", empty, check=False)
            self.assertEqual(rc, 1)
            self.assertIn("No ivpm.yaml", err)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class TestShowDepsCatalog(_WorkspaceTest):
    """D1 -- '--from' catalog reports the delta alongside 'contains'."""

    def test_catalog_json(self):
        manifest = os.path.join(self.tmp, "ivpm.yaml")
        out, _, _ = _run("show", "deps", "--from", manifest, "--json")
        cat = json.loads(out)
        self.assertIn("Second paragraph.", cat["doc"])
        sim = next(d for d in cat["dep_sets"] if d["name"] == "sim")
        self.assertEqual(sim["doc"], "Use this one in CI.")
        # 'contains' is still the full leaf set...
        self.assertEqual(sim["contains"], ["plainlib", "somelib", "vip"])
        # ...with the declared-vs-inherited breakdown now beside it
        self.assertEqual(sorted(sim["own"]), ["somelib", "vip"])
        self.assertEqual(sim["inherited_from"], {"plainlib": "base"})
        self.assertEqual(sim["overrides"]["somelib"]["base"], "base")


if __name__ == "__main__":
    unittest.main()
