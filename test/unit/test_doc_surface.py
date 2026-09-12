"""
Tests for the documentation surface: 'description:' and 'doc:' at the package,
dep-set and dependency levels, plus env/path-set descriptions and project
metadata.

Two invariants hold across the whole feature and are asserted here:

1. Every key is optional -- a manifest that adopts none of them parses exactly
   as before.
2. The keys are inert -- nothing here influences resolution, fetching, caching
   or the lock file.
"""
import io
import json
import os
import unittest

from .test_base import TestBase
from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.env_spec import EnvSpec


def _parse(yaml_text, name="<test>"):
    """Parse an ivpm.yaml string and return the ProjInfo."""
    return IvpmYamlReader().read(io.StringIO(yaml_text), name)


# Every source type that can appear on a dependency entry, with a minimal
# otherwise-valid entry body for each.
_SOURCES = {
    "git":    ["url: https://github.com/org/lib.git"],
    "pypi":   ["src: pypi"],
    "dir":    ["src: dir", "url: ../lib"],
    "http":   ["src: http", "url: https://example.com/lib.tgz"],
    "module": ["src: module", "module: lib/1.0"],
    "gh-rls": ["src: gh-rls", "url: https://github.com/org/lib"],
}


def _dep_manifest(src_lines, extra_lines=()):
    """Build a one-dependency manifest from a list of dep-entry lines."""
    body = "\n".join("          " + ln
                     for ln in list(src_lines) + list(extra_lines))
    return """
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: lib
%s
""" % body


class TestDepDescription(unittest.TestCase):
    """A1 -- 'description:' on a dependency entry."""

    def test_accepted_on_every_source(self):
        for src, body in _SOURCES.items():
            with self.subTest(src=src):
                proj = _parse(_dep_manifest(
                    body, ["description: A one-line summary."]))
                pkg = proj.get_dep_set("default").packages["lib"]
                self.assertEqual(pkg.description, "A one-line summary.")

    def test_absent_leaves_none(self):
        for src, body in _SOURCES.items():
            with self.subTest(src=src):
                proj = _parse(_dep_manifest(body))
                pkg = proj.get_dep_set("default").packages["lib"]
                self.assertIsNone(pkg.description)
                self.assertIsNone(pkg.doc)

    def test_unknown_key_still_errors(self):
        """Validation was extended, not weakened: an unknown key still fails,
        and 'description' now appears in the reported valid-tag list."""
        with self.assertRaises(Exception) as ctx:
            _parse(_dep_manifest(_SOURCES["git"], ["descriptoin: typo"]))
        msg = str(ctx.exception)
        self.assertIn("descriptoin", msg)
        self.assertIn("description", msg)


class TestDocKey(unittest.TestCase):
    """A2 -- 'doc:' at all three levels, preserved verbatim."""

    BODY = ("Pinned to a commit rather than a tag because upstream retags.\n"
            "\n"
            "The patch restores ``--std=c++17`` compatibility; see ISSUE-4412.\n")

    def test_dependency_level(self):
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: lib
          url: https://github.com/org/lib.git
          doc: |
            Pinned to a commit rather than a tag because upstream retags.

            The patch restores ``--std=c++17`` compatibility; see ISSUE-4412.
""")
        pkg = proj.get_dep_set("default").packages["lib"]
        self.assertEqual(pkg.doc, self.BODY)

    def test_package_level(self):
        proj = _parse("""
package:
  name: p
  doc: |
    Pinned to a commit rather than a tag because upstream retags.

    The patch restores ``--std=c++17`` compatibility; see ISSUE-4412.
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        self.assertEqual(proj.doc, self.BODY)

    def test_dep_set_level(self):
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: default
      doc: |
        Pinned to a commit rather than a tag because upstream retags.

        The patch restores ``--std=c++17`` compatibility; see ISSUE-4412.
      deps:
        - name: lib
          src: pypi
""")
        self.assertEqual(proj.get_dep_set("default").doc, self.BODY)

    def test_absent_leaves_none(self):
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        self.assertIsNone(proj.doc)
        self.assertIsNone(proj.get_dep_set("default").doc)

    def test_markup_is_not_interpreted(self):
        """ivpm stores 'doc' opaquely: the dialect is the renderer's business."""
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
          doc: "# Heading\\n\\n<b>raw html</b> :ref:`x` ${not_a_var}"
""")
        pkg = proj.get_dep_set("default").packages["lib"]
        self.assertEqual(pkg.doc,
                         "# Heading\n\n<b>raw html</b> :ref:`x` ${not_a_var}")

    def test_writer_is_not_in_the_path(self):
        """IvpmYamlWriter reconstructs nothing that could drop the doc keys.

        It writes from the legacy 'deps'/'dev-deps' attributes that ProjInfo no
        longer has, and is reached only from 'ivpm snapshot'. Pinned so that a
        future revival of the writer is forced to revisit the doc keys.
        """
        from ivpm.proj_info import ProjInfo
        self.assertFalse(hasattr(ProjInfo(is_src=True), "deps"))


class TestEnvDescription(unittest.TestCase):
    """C1 -- 'description:' on an env directive."""

    def test_description_carried_on_env_spec(self):
        proj = _parse("""
package:
  name: p
  with:
    env:
    - name: DESIGN_ROOT
      description: Root of the RTL tree; consumed by the filelist generator.
      value: /rtl
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        spec = proj.env_settings[0]
        self.assertEqual(spec.var, "DESIGN_ROOT")
        self.assertEqual(spec.val, "/rtl")
        self.assertEqual(spec.act, EnvSpec.Act.Set)
        self.assertEqual(
            spec.description,
            "Root of the RTL tree; consumed by the filelist generator.")

    def test_description_absent_and_inert(self):
        """The description never reaches what direnv is handed."""
        proj = _parse("""
package:
  name: p
  with:
    env:
    - name: A
      value: 1
    - name: B
      description: documented
      value: 1
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        a, b = proj.env_settings
        self.assertIsNone(a.description)
        self.assertEqual(b.description, "documented")
        self.assertEqual(a.as_direnv().replace("A", "X"),
                         b.as_direnv().replace("B", "X"))


class TestPathSetDescription(unittest.TestCase):
    """C1 -- 'description:' on a path-set. Needs a guard, not just a field."""

    MANIFEST = """
package:
  name: p
  paths:
    systemverilog:
      description: Include directories published to downstream consumers.
      incdirs: [ rtl/include ]
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
"""

    def test_description_captured(self):
        proj = _parse(self.MANIFEST)
        self.assertEqual(
            proj.path_descriptions["systemverilog"],
            "Include directories published to downstream consumers.")

    def test_not_treated_as_a_path_kind(self):
        """Without the guard, the string is iterated one CHARACTER at a time,
        each character becoming a 'path' -- silently, with no error."""
        proj = _parse(self.MANIFEST)
        sv = proj.paths["systemverilog"]
        self.assertEqual(list(sv.keys()), ["incdirs"])
        self.assertNotIn("description", sv)
        self.assertEqual(len(sv["incdirs"]), 1)
        self.assertTrue(sv["incdirs"][0].endswith("rtl/include"))

    def test_absent_leaves_empty(self):
        proj = _parse("""
package:
  name: p
  paths:
    systemverilog:
      incdirs: [ rtl/include ]
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        self.assertEqual(proj.path_descriptions, {})


class TestProjectMetadata(unittest.TestCase):
    """C2 -- license / homepage / documentation / maintainers."""

    def test_all_keys_parsed(self):
        proj = _parse("""
package:
  name: p
  license: Apache-2.0
  homepage: https://example.com
  documentation: https://docs.example.com/p
  maintainers:
    - Alice <alice@example.com>
    - Bob <bob@example.com>
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        self.assertEqual(proj.license, "Apache-2.0")
        self.assertEqual(proj.homepage, "https://example.com")
        self.assertEqual(proj.documentation, "https://docs.example.com/p")
        self.assertEqual(proj.maintainers,
                         ["Alice <alice@example.com>", "Bob <bob@example.com>"])

    def test_absent_keys_leave_defaults(self):
        proj = _parse("""
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        self.assertIsNone(proj.license)
        self.assertIsNone(proj.homepage)
        self.assertIsNone(proj.documentation)
        self.assertEqual(proj.maintainers, [])

    def test_maintainers_must_be_a_list(self):
        with self.assertRaises(Exception) as ctx:
            _parse("""
package:
  name: p
  maintainers: Alice
  dep-sets:
    - name: default
      deps:
        - name: lib
          src: pypi
""")
        self.assertIn("maintainers", str(ctx.exception))


class TestUpstreamMetadata(TestBase):
    """C2 -- opportunistic population. The manifest always wins."""

    def _write(self, relpath, content):
        self.mkFile(relpath, content)
        return os.path.join(self.testdir, os.path.dirname(relpath))

    def test_pyproject_metadata_read(self):
        from ivpm.upstream_meta import read_upstream_metadata
        d = self._write("pp/pyproject.toml", """
[project]
name = "up"
version = "2.1.0"
description = "From pyproject."
license = "MIT"
urls = { Homepage = "https://up.example", Documentation = "https://docs.up.example" }
""")
        meta = read_upstream_metadata(d)
        self.assertEqual(meta["description"], "From pyproject.")
        self.assertEqual(meta["version"], "2.1.0")
        self.assertEqual(meta["license"], "MIT")
        self.assertEqual(meta["homepage"], "https://up.example")
        self.assertEqual(meta["documentation"], "https://docs.up.example")

    def test_package_json_metadata_read(self):
        from ivpm.upstream_meta import read_upstream_metadata
        d = self._write("pj/package.json", json.dumps({
            "name": "up", "version": "3.0.0", "description": "From npm.",
            "license": "BSD-3-Clause", "homepage": "https://npm.example",
        }))
        meta = read_upstream_metadata(d)
        self.assertEqual(meta["license"], "BSD-3-Clause")
        self.assertEqual(meta["homepage"], "https://npm.example")

    def test_manifest_beats_upstream(self):
        from ivpm.upstream_meta import package_metadata
        self.mkFile("both/pyproject.toml", """
[project]
name = "up"
version = "2.1.0"
description = "From pyproject."
license = "MIT"
urls = { Homepage = "https://up.example" }
""")
        d = self._write("both/ivpm.yaml", """
package:
  name: up
  version: 9.9.9
  license: Apache-2.0
  dep-sets:
    - name: default
      deps: []
""")
        meta = package_metadata(d)
        self.assertEqual(meta["license"], "Apache-2.0")   # manifest wins
        self.assertEqual(meta["version"], "9.9.9")        # manifest wins
        # not restated in the manifest -> upstream fills it in
        self.assertEqual(meta["homepage"], "https://up.example")
        self.assertEqual(meta["description"], "From pyproject.")

    def test_missing_dir_is_empty(self):
        from ivpm.upstream_meta import read_upstream_metadata, package_metadata
        missing = os.path.join(self.testdir, "nope")
        self.assertEqual(read_upstream_metadata(missing), {})
        self.assertEqual(package_metadata(missing), {})

    def test_malformed_upstream_manifest_is_not_fatal(self):
        from ivpm.upstream_meta import read_upstream_metadata
        d = self._write("bad/pyproject.toml", "this is not [ valid toml")
        self.assertEqual(read_upstream_metadata(d), {})


class TestDocKeysAreInert(unittest.TestCase):
    """Invariant 2: adding the keys changes nothing about what is resolved."""

    WITHOUT = """
package:
  name: p
  dep-sets:
    - name: default
      deps:
        - name: lib
          url: https://github.com/org/lib.git
          branch: main
"""
    WITH = """
package:
  name: p
  description: A project.
  doc: Some prose.
  license: Apache-2.0
  homepage: https://example.com
  documentation: https://docs.example.com
  maintainers: [ Alice ]
  dep-sets:
    - name: default
      description: The default set.
      doc: More prose.
      deps:
        - name: lib
          description: A library.
          doc: Even more prose.
          url: https://github.com/org/lib.git
          branch: main
"""

    def test_resolution_identical(self):
        from ivpm.package_lock import _entry_from_pkg

        bare = _parse(self.WITHOUT).get_dep_set("default").packages["lib"]
        documented = _parse(self.WITH).get_dep_set("default").packages["lib"]

        self.assertEqual(bare.url, documented.url)
        self.assertEqual(bare.branch, documented.branch)
        self.assertEqual(bare.dep_set, documented.dep_set)
        # The lock records what you got, never what you declared as prose.
        entry = _entry_from_pkg(documented)
        self.assertEqual(_entry_from_pkg(bare), entry)
        for key in ("description", "doc", "license", "homepage",
                    "documentation", "maintainers"):
            self.assertNotIn(key, entry)


class TestUpdateUnaffectedByDocKeys(TestBase):
    """The whole-plan regression: 'ivpm update' a real workspace with and
    without the documentation keys, and diff what lands.

    If package-lock.json or the packages/ tree moves, a documentation key has
    become load-bearing and invariant 2 is broken.
    """

    # The two manifests differ ONLY in the documentation keys: every
    # functional directive (the env setting, the path-set, the dependency and
    # its spec) is present in both, so anything that moves between the runs is
    # attributable to the prose alone.
    BARE = """
package:
  name: doc_surface_regression
  paths:
    systemverilog:
      incdirs: [ rtl/include ]
  with:
    env:
    - name: DESIGN_ROOT
      value: /rtl
  dep-sets:
    - name: default-dev
      deps:
        - name: nested_libD
          url: file://${DATA_DIR}/nested_libD
          src: dir
"""

    DOCUMENTED = """
package:
  name: doc_surface_regression
  description: A regression fixture.
  doc: |
    Prose about the fixture.

    A second paragraph.
  license: Apache-2.0
  homepage: https://example.com
  documentation: https://docs.example.com
  maintainers: [ Alice ]
  paths:
    systemverilog:
      description: Published include dirs.
      incdirs: [ rtl/include ]
  with:
    env:
    - name: DESIGN_ROOT
      description: Root of the RTL tree.
      value: /rtl
  dep-sets:
    - name: default-dev
      description: The dev set.
      doc: Use this one in CI.
      deps:
        - name: nested_libD
          description: A local fixture package.
          doc: |
            Kept local so the test needs no network.
          url: file://${DATA_DIR}/nested_libD
          src: dir
"""

    def _update(self, manifest):
        self.mkFile("ivpm.yaml", manifest)
        self.ivpm_update(dep_set="default-dev", skip_venv=True)
        lock_path = os.path.join(self.testdir, "packages", "package-lock.json")
        with open(lock_path) as fp:
            lock = json.load(fp)
        # 'generated' is a wall-clock stamp and 'sha256' is taken over a body
        # that includes it, so both differ between any two runs.
        lock.pop("generated", None)
        lock.pop("sha256", None)
        packages = os.path.join(self.testdir, "packages")
        tree = {}
        for root, _, files in os.walk(packages):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.testdir)
                # Perf records are named for the wall-clock time and pid of
                # the run that wrote them; only their presence is comparable.
                if "/.ivpm/perf-" in rel:
                    rel = "packages/.ivpm/perf-*.json"
                    tree[rel] = None
                    continue
                with open(full, "rb") as fp:
                    tree[rel] = fp.read()
        # package-lock.json is compared separately, with its volatile keys
        # stripped; drop it here so the raw bytes don't re-introduce them.
        tree.pop("packages/package-lock.json", None)
        return lock, tree

    def test_lock_and_tree_identical(self):
        bare_lock, bare_tree = self._update(self.BARE)
        # setUp's teardown of the run dir happens per-test, so re-create the
        # workspace in place for the second run.
        import shutil
        shutil.rmtree(os.path.join(self.testdir, "packages"))
        doc_lock, doc_tree = self._update(self.DOCUMENTED)

        self.assertEqual(bare_lock, doc_lock)
        self.assertEqual(sorted(bare_tree), sorted(doc_tree))
        self.assertEqual(bare_tree, doc_tree)

        # ...and nothing in the lock so much as mentions the prose.
        blob = json.dumps(doc_lock)
        for prose in ("A regression fixture.", "Prose about the fixture.",
                      "A local fixture package.", "Apache-2.0",
                      "Use this one in CI.", "Published include dirs."):
            self.assertNotIn(prose, blob)


if __name__ == "__main__":
    unittest.main()
