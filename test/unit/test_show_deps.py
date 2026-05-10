#****************************************************************************
#* test_show_deps.py
#*
#* Tests for 'ivpm show deps' — dep_loader, renderers, and CLI integration.
#****************************************************************************
import dataclasses
import json
import os
import sys
import tempfile
import textwrap
import unittest
import warnings

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_UNIT_DIR  = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR  = os.path.dirname(_UNIT_DIR)
_ROOT_DIR  = os.path.dirname(_TEST_DIR)
_SRC_DIR   = os.path.join(_ROOT_DIR, "src")
_PYTHON    = os.path.join(_ROOT_DIR, "packages", "python", "bin", "python3")
_ENV       = {**os.environ, "PYTHONPATH": _SRC_DIR}

sys.path.insert(0, _SRC_DIR)


def _run(*args, cwd=None, check=True):
    import subprocess
    cmd = [_PYTHON, "-m", "ivpm"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, env=_ENV,
                            cwd=cwd or _ROOT_DIR)
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command {cmd} failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout, result.returncode, result.stderr


# ---------------------------------------------------------------------------
# Workspace builder helper
# ---------------------------------------------------------------------------

def _make_workspace(tmp, root_yaml, lock=None, sub_pkgs=None):
    """
    root_yaml: str — contents of <tmp>/ivpm.yaml
    lock: dict | None — written as <tmp>/packages/package-lock.json
    sub_pkgs: {pkg_name: yaml_str} — <tmp>/packages/<pkg_name>/ivpm.yaml
    """
    with open(os.path.join(tmp, "ivpm.yaml"), "w") as f:
        f.write(textwrap.dedent(root_yaml))

    if lock is not None:
        pkg_dir = os.path.join(tmp, "packages")
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "package-lock.json"), "w") as f:
            json.dump(lock, f, indent=2)

    if sub_pkgs:
        for pkg_name, yaml_str in sub_pkgs.items():
            pkg_dir = os.path.join(tmp, "packages", pkg_name)
            os.makedirs(pkg_dir, exist_ok=True)
            with open(os.path.join(pkg_dir, "ivpm.yaml"), "w") as f:
                f.write(textwrap.dedent(yaml_str))


# ---------------------------------------------------------------------------
# TestDepLoader
# ---------------------------------------------------------------------------

class TestDepLoader(unittest.TestCase):
    """Unit tests for dep_loader.DepLoader — no network, hand-crafted workspaces."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, dep_set=None):
        from ivpm.show.dep_loader import DepLoader
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            return DepLoader(self.tmp, dep_set=dep_set).load()

    # ------------------------------------------------------------------

    def test_single_dep_no_lock(self):
        """Root with one pypi dep, no lock file → lock_available=False, dep present."""
        _make_workspace(self.tmp, """
            package:
              name: myproject
              dep-sets:
              - name: default
                deps:
                - name: pyyaml
                  src: pypi
        """)
        graph = self._load()
        self.assertFalse(graph.lock_available)
        self.assertEqual(graph.project, "myproject")
        names = [n.name for n in graph.nodes]
        self.assertIn("pyyaml", names)

    def test_single_dep_with_lock(self):
        """Lock file present → lock_available=True, resolved fields populated."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "pyyaml": {
                    "src": "pypi",
                    "resolved_by": "root",
                    "dep_set": "default",
                    "version_requested": ">=6.0",
                    "version_resolved": "6.0.1",
                    "reproducible": True,
                }
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: myproject
              dep-sets:
              - name: default
                deps:
                - name: pyyaml
                  src: pypi
        """, lock=lock)
        graph = self._load()
        self.assertTrue(graph.lock_available)
        node = next(n for n in graph.nodes if n.name == "pyyaml")
        self.assertEqual(node.src, "pypi")
        self.assertEqual(node.version_resolved, "6.0.1")
        self.assertEqual(node.specifier, "root")

    def test_first_specifier_wins(self):
        """Root declares bar; sub-project foo also declares bar → bar.specifier == 'root'."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "foo": {"src": "git", "resolved_by": "root", "dep_set": "default",
                        "url": "https://github.com/org/foo.git", "reproducible": True},
                "bar": {"src": "pypi", "resolved_by": "root", "dep_set": "default",
                        "version_resolved": "1.0", "reproducible": True},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: foo
                  url: https://github.com/org/foo.git
                - name: bar
                  src: pypi
        """, lock=lock, sub_pkgs={
            "foo": """
                package:
                  name: foo
                  dep-sets:
                  - name: default
                    deps:
                    - name: bar
                      src: pypi
            """
        })
        graph = self._load()
        bar = next((n for n in graph.nodes if n.name == "bar"), None)
        self.assertIsNotNone(bar)
        self.assertEqual(bar.specifier, "root")

    def test_also_requested_by(self):
        """bar is declared by root AND foo → bar.also_requested_by contains 'foo'."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "foo": {"src": "git", "resolved_by": "root", "dep_set": "default",
                        "url": "https://github.com/org/foo.git", "reproducible": True},
                "bar": {"src": "pypi", "resolved_by": "root", "dep_set": "default",
                        "version_resolved": "1.0", "reproducible": True},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: foo
                  url: https://github.com/org/foo.git
                - name: bar
                  src: pypi
        """, lock=lock, sub_pkgs={
            "foo": """
                package:
                  name: foo
                  dep-sets:
                  - name: default
                    deps:
                    - name: bar
                      src: pypi
            """
        })
        graph = self._load()
        bar = next(n for n in graph.nodes if n.name == "bar")
        self.assertIn("foo", bar.also_requested_by)

    def test_shadowed_in_tree(self):
        """In tree mode, foo's dep list contains a shadowed bar node."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "foo": {"src": "git", "resolved_by": "root", "dep_set": "default",
                        "url": "https://github.com/org/foo.git", "reproducible": True},
                "bar": {"src": "pypi", "resolved_by": "root", "dep_set": "default",
                        "version_resolved": "1.0", "reproducible": True},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: foo
                  url: https://github.com/org/foo.git
                - name: bar
                  src: pypi
        """, lock=lock, sub_pkgs={
            "foo": """
                package:
                  name: foo
                  dep-sets:
                  - name: default
                    deps:
                    - name: bar
                      src: pypi
            """
        })
        graph = self._load()
        foo = next(n for n in graph.nodes if n.name == "foo")
        # foo has a sub-dep bar that is shadowed by root
        bar_child = next((c for c in foo.deps if c.name == "bar"), None)
        self.assertIsNotNone(bar_child, "foo should have bar as a sub-dep")
        self.assertTrue(bar_child.shadowed)

    def test_transitive_dep(self):
        """Root → foo → baz: baz present in flat graph with specifier == 'foo'."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "foo": {"src": "git", "resolved_by": "root", "dep_set": "default",
                        "url": "https://github.com/org/foo.git", "reproducible": True},
                "baz": {"src": "pypi", "resolved_by": "foo", "dep_set": "default",
                        "version_resolved": "2.0", "reproducible": True},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: foo
                  url: https://github.com/org/foo.git
        """, lock=lock, sub_pkgs={
            "foo": """
                package:
                  name: foo
                  dep-sets:
                  - name: default
                    deps:
                    - name: baz
                      src: pypi
            """
        })
        graph = self._load()

        def _find(nodes, name):
            for n in nodes:
                if n.name == name and not n.shadowed:
                    return n
                found = _find(n.deps, name)
                if found:
                    return found
            return None

        baz = _find(graph.nodes, "baz")
        self.assertIsNotNone(baz, "baz should appear in the graph")
        self.assertEqual(baz.specifier, "foo")

    def test_no_sub_ivpm_yaml(self):
        """Package with no ivpm.yaml in packages/ is treated as a leaf — no crash."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "foo": {"src": "git", "resolved_by": "root", "dep_set": "default",
                        "url": "https://github.com/org/foo.git", "reproducible": True},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: foo
                  url: https://github.com/org/foo.git
        """, lock=lock)
        # Create packages/foo dir but no ivpm.yaml inside it
        os.makedirs(os.path.join(self.tmp, "packages", "foo"), exist_ok=True)
        graph = self._load()
        foo = next(n for n in graph.nodes if n.name == "foo")
        self.assertEqual(foo.deps, [])

    def test_dep_set_selection(self):
        """Package with dep_set 'ci' in lock → that dep-set's deps are used."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "foo": {"src": "git", "resolved_by": "root", "dep_set": "ci",
                        "url": "https://github.com/org/foo.git", "reproducible": True},
                "baz": {"src": "pypi", "resolved_by": "foo", "dep_set": "default",
                        "version_resolved": "1.0", "reproducible": True},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: foo
                  url: https://github.com/org/foo.git
        """, lock=lock, sub_pkgs={
            "foo": """
                package:
                  name: foo
                  dep-sets:
                  - name: default
                    deps:
                    - name: other
                      src: pypi
                  - name: ci
                    deps:
                    - name: baz
                      src: pypi
            """
        })
        graph = self._load()

        def _find(nodes, name):
            for n in nodes:
                if n.name == name:
                    return n
                found = _find(n.deps, name)
                if found:
                    return found
            return None

        baz = _find(graph.nodes, "baz")
        self.assertIsNotNone(baz, "baz (from 'ci' dep-set) should be in graph")
        other = _find(graph.nodes, "other")
        self.assertIsNone(other, "'other' (from 'default' dep-set) should not be in graph")

    def test_cycle_guard(self):
        """Circular dir deps (A → B → A) → shadowed leaf, no infinite loop."""
        lock = {
            "ivpm_lock_version": 1,
            "packages": {
                "pkgA": {"src": "dir", "resolved_by": "root", "dep_set": "default",
                         "path": "/some/pkgA", "reproducible": False},
                "pkgB": {"src": "dir", "resolved_by": "pkgA", "dep_set": "default",
                         "path": "/some/pkgB", "reproducible": False},
            }
        }
        _make_workspace(self.tmp, """
            package:
              name: root
              dep-sets:
              - name: default
                deps:
                - name: pkgA
                  src: dir
        """, lock=lock, sub_pkgs={
            "pkgA": """
                package:
                  name: pkgA
                  dep-sets:
                  - name: default
                    deps:
                    - name: pkgB
                      src: dir
            """,
            "pkgB": """
                package:
                  name: pkgB
                  dep-sets:
                  - name: default
                    deps:
                    - name: pkgA
                      src: dir
            """,
        })
        # Must not raise or hang
        graph = self._load()
        self.assertGreater(len(graph.nodes), 0)

    def test_missing_packages_dir(self):
        """No packages/ directory → loads without crash, lock_available=False."""
        _make_workspace(self.tmp, """
            package:
              name: myproject
              dep-sets:
              - name: default
                deps:
                - name: pyyaml
                  src: pypi
        """)
        graph = self._load()
        self.assertFalse(graph.lock_available)
        self.assertEqual(graph.project, "myproject")


# ---------------------------------------------------------------------------
# TestDepNodeLabels
# ---------------------------------------------------------------------------

class TestDepNodeLabels(unittest.TestCase):
    """Unit tests for DepNode.version_label() and ref_url_label()."""

    def _node(self, **kwargs):
        from ivpm.show.dep_info import DepNode
        return DepNode(name="pkg", src="git", specifier="root", **kwargs)

    def test_version_label_resolved_preferred(self):
        """version_resolved takes precedence over version."""
        n = self._node(version=">=6.0", version_resolved="6.0.1")
        self.assertEqual(n.version_label(), "6.0.1")

    def test_version_label_falls_back_to_requested(self):
        """version used when version_resolved is absent."""
        n = self._node(version=">=6.0")
        self.assertEqual(n.version_label(), ">=6.0")

    def test_version_label_empty_when_absent(self):
        """Empty string returned when no version information is present."""
        n = self._node()
        self.assertEqual(n.version_label(), "")

    def test_ref_url_label_git_with_commit(self):
        """Git package shows stripped URL and short commit hash."""
        n = self._node(
            url="https://github.com/org/foo.git",
            commit="abc1234def5678",
        )
        label = n.ref_url_label()
        self.assertIn("github.com/org/foo.git", label)
        self.assertIn("abc1234", label)
        self.assertNotIn("abc1234def5678", label)   # must be truncated to 8 chars

    def test_ref_url_label_git_commit_preferred_over_branch(self):
        """Commit hash takes priority over branch in ref_url_label."""
        n = self._node(
            url="https://github.com/org/foo.git",
            branch="main",
            commit="abc1234def5678",
        )
        label = n.ref_url_label()
        self.assertIn("abc1234", label)
        self.assertNotIn("main", label)

    def test_ref_url_label_git_branch_when_no_commit(self):
        """Branch shown when no commit is available."""
        n = self._node(
            url="https://github.com/org/foo.git",
            branch="main",
        )
        label = n.ref_url_label()
        self.assertIn("github.com/org/foo.git", label)
        self.assertIn("main", label)

    def test_ref_url_label_pypi_empty(self):
        """Pypi packages have no URL/ref — label must be empty."""
        from ivpm.show.dep_info import DepNode
        n = DepNode(name="bar", src="pypi", specifier="root",
                    version_resolved="6.0.1")
        self.assertEqual(n.ref_url_label(), "")

    def test_ref_url_label_no_version_in_label(self):
        """version_resolved must NOT appear inside ref_url_label."""
        n = self._node(
            url="https://github.com/org/foo.git",
            branch="main",
            version_resolved="v1.2.3",
        )
        label = n.ref_url_label()
        self.assertNotIn("v1.2.3", label)

    def test_ref_url_label_tag_used_when_no_commit(self):
        """Tag is shown when there is no commit hash."""
        n = self._node(
            url="https://github.com/org/foo.git",
            tag="v2.0.0",
        )
        label = n.ref_url_label()
        self.assertIn("v2.0.0", label)

    def test_ref_url_label_strips_https_prefix(self):
        """HTTPS prefix is stripped from the URL."""
        n = self._node(url="https://github.com/org/foo.git")
        label = n.ref_url_label()
        self.assertNotIn("https://", label)

    def test_version_label_and_ref_url_label_both_set(self):
        """A gh-rls package can have both a version and a URL."""
        from ivpm.show.dep_info import DepNode
        n = DepNode(name="tool", src="gh-rls", specifier="root",
                    url="https://github.com/org/tool/releases/v1.2.3",
                    version_resolved="v1.2.3")
        self.assertEqual(n.version_label(), "v1.2.3")
        self.assertIn("github.com", n.ref_url_label())


# ---------------------------------------------------------------------------
# TestShowDepsRenderers
# ---------------------------------------------------------------------------

class TestShowDepsRenderers(unittest.TestCase):
    """Unit tests for renderer functions — synthetic DepGraph, no I/O."""

    def _make_graph(self):
        from ivpm.show.dep_info import DepNode, DepGraph
        foo = DepNode(
            name="foo", src="git", specifier="root",
            url="https://github.com/org/foo.git", branch="main",
            commit="abc1234def5678",
            deps=[
                DepNode(name="bar", src="pypi", specifier="root",
                        version="1.2.3", shadowed=True),
            ]
        )
        bar = DepNode(
            name="bar", src="pypi", specifier="root",
            version="1.2.3", also_requested_by=["foo"],
        )
        return DepGraph(project="myproject", version="0.1.0",
                        dep_set="default", nodes=[foo, bar])

    # ------------------------------------------------------------------

    def test_flat_json_schema(self):
        from ivpm.show.show_deps import _flat_json
        data = json.loads(_flat_json(self._make_graph()))
        self.assertIsInstance(data, list)
        for entry in data:
            self.assertIn("name", entry)
            self.assertIn("src", entry)
            self.assertIn("specifier", entry)
            self.assertIn("shadowed", entry)

    def test_flat_json_sorted(self):
        from ivpm.show.show_deps import _flat_json
        data = json.loads(_flat_json(self._make_graph()))
        names = [d["name"] for d in data]
        self.assertEqual(names, sorted(names))

    def test_flat_json_shadowed_excluded(self):
        """Shadowed nodes (bar under foo) must not appear as top-level entries."""
        from ivpm.show.show_deps import _flat_json
        data = json.loads(_flat_json(self._make_graph()))
        # bar appears at root level (not shadowed) — exactly once
        bar_entries = [d for d in data if d["name"] == "bar"]
        self.assertEqual(len(bar_entries), 1)
        self.assertFalse(bar_entries[0]["shadowed"])

    def test_tree_json_nested(self):
        from ivpm.show.show_deps import _tree_json
        data = json.loads(_tree_json(self._make_graph()))
        self.assertIn("project", data)
        self.assertIn("dep_set", data)
        self.assertIn("deps", data)
        foo = next(d for d in data["deps"] if d["name"] == "foo")
        # foo should have bar as a sub-dep (shadowed)
        self.assertTrue(len(foo["deps"]) > 0)
        bar_child = foo["deps"][0]
        self.assertEqual(bar_child["name"], "bar")
        self.assertTrue(bar_child["shadowed"])

    def test_detail_json_fields(self):
        from ivpm.show.show_deps import _detail_json
        from ivpm.show.dep_info import DepNode
        node = DepNode(name="foo", src="git", specifier="root",
                       url="https://github.com/org/foo.git", commit="abc123")
        data = json.loads(_detail_json(node))
        self.assertEqual(data["name"], "foo")
        self.assertEqual(data["src"], "git")
        self.assertIn("url", data)
        self.assertIn("deps", data)

    def test_plain_flat_columns(self, ):
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_flat
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_flat(self._make_graph())
        out = buf.getvalue()
        self.assertIn("bar", out)
        self.assertIn("foo", out)
        self.assertIn("root", out)     # specifier column
        self.assertIn("pypi", out)
        self.assertIn("git", out)

    def test_plain_flat_header_has_version_column(self):
        """Flat header must include a distinct 'Version' column."""
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_flat
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_flat(self._make_graph())
        header = buf.getvalue().split("\n")[0]
        self.assertIn("Version", header)
        self.assertIn("URL", header)

    def test_plain_flat_pypi_version_shown(self):
        """bar (pypi, version=1.2.3) must appear in flat output."""
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_flat
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_flat(self._make_graph())
        out = buf.getvalue()
        self.assertIn("1.2.3", out)

    def test_plain_flat_git_commit_shown(self):
        """foo (git, commit=abc1234def5678) must show its short commit hash."""
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_flat
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_flat(self._make_graph())
        out = buf.getvalue()
        self.assertIn("abc1234", out)   # first 8 chars of commit

    def test_plain_flat_git_url_shown(self):
        """foo (git) must show its URL in the URL/Ref column."""
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_flat
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_flat(self._make_graph())
        out = buf.getvalue()
        self.assertIn("github.com/org/foo.git", out)

    def test_plain_tree_version_shown(self):
        """bar (pypi, version=1.2.3) must appear in tree output."""
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_tree
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_tree(self._make_graph())
        out = buf.getvalue()
        self.assertIn("1.2.3", out)

    def test_plain_tree_git_commit_shown(self):
        """foo (git, commit) must show commit hash in tree output."""
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_tree
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_tree(self._make_graph())
        out = buf.getvalue()
        self.assertIn("abc1234", out)

    def test_plain_tree_connectors(self):
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_tree
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_tree(self._make_graph())
        out = buf.getvalue()
        self.assertTrue("├──" in out or "└──" in out,
                        "Expected tree connectors in output")
        self.assertIn("myproject", out)

    def test_plain_detail(self):
        import io
        from contextlib import redirect_stdout
        from ivpm.show.show_deps import _plain_detail
        from ivpm.show.dep_info import DepNode
        node = DepNode(name="foo", src="git", specifier="root",
                       url="https://github.com/org/foo.git",
                       branch="main", commit="abc123")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _plain_detail(node)
        out = buf.getvalue()
        self.assertIn("foo", out)
        self.assertIn("git", out)
        self.assertIn("https://github.com/org/foo.git", out)


# ---------------------------------------------------------------------------
# TestShowDepsCLI
# ---------------------------------------------------------------------------

_LOCK_PATH = os.path.join(_ROOT_DIR, "packages", "package-lock.json")
_LOCK_EXISTS = os.path.isfile(_LOCK_PATH)


@unittest.skipUnless(
    _LOCK_EXISTS,
    "workspace not populated — run 'ivpm update' first"
)
class TestShowDepsCLI(unittest.TestCase):
    """CLI integration tests running against the real ivpm project workspace."""

    # ------------------------------------------------------------------
    # Flat list (default)
    # ------------------------------------------------------------------

    def test_flat_default_exits_0(self):
        _, rc, _ = _run("show", "deps", "--no-rich")
        self.assertEqual(rc, 0)

    def test_flat_contains_headers(self):
        out, _, _ = _run("show", "deps", "--no-rich")
        self.assertIn("Package", out)
        self.assertIn("Src", out)
        self.assertIn("Declared by", out)
        self.assertIn("Version", out)
        self.assertIn("URL", out)

    def test_flat_lists_known_packages(self):
        out, _, _ = _run("show", "deps", "--no-rich")
        # ivpm itself depends on these
        self.assertIn("pyyaml", out)
        self.assertIn("toposort", out)

    def test_flat_json_valid(self):
        out, rc, _ = _run("show", "deps", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)
        for entry in data:
            self.assertIn("name", entry)
            self.assertIn("src", entry)
            self.assertIn("specifier", entry)

    def test_flat_json_sorted(self):
        out, _, _ = _run("show", "deps", "--json")
        data = json.loads(out)
        names = [d["name"] for d in data]
        self.assertEqual(names, sorted(names))

    # ------------------------------------------------------------------
    # Tree view
    # ------------------------------------------------------------------

    def test_tree_exits_0(self):
        _, rc, _ = _run("show", "deps", "--tree", "--no-rich")
        self.assertEqual(rc, 0)

    def test_tree_contains_root(self):
        out, _, _ = _run("show", "deps", "--tree", "--no-rich")
        self.assertIn("ivpm", out)

    def test_tree_json_valid(self):
        out, rc, _ = _run("show", "deps", "--tree", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("project", data)
        self.assertIn("dep_set", data)
        self.assertIn("deps", data)
        self.assertIsInstance(data["deps"], list)

    def test_tree_json_nested(self):
        out, _, _ = _run("show", "deps", "--tree", "--json")
        data = json.loads(out)
        # At least the top-level deps list is non-empty
        self.assertGreater(len(data["deps"]), 0)

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------

    def test_detail_known_pkg(self):
        out, rc, _ = _run("show", "deps", "pyyaml", "--no-rich")
        self.assertEqual(rc, 0)
        self.assertIn("pyyaml", out)
        self.assertIn("pypi", out)

    def test_detail_json(self):
        out, rc, _ = _run("show", "deps", "pyyaml", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["name"], "pyyaml")
        self.assertIn("src", data)
        self.assertIn("specifier", data)

    def test_detail_unknown_exits_1(self):
        _, rc, _ = _run("show", "deps", "does-not-exist-xyz", check=False)
        self.assertEqual(rc, 1)

    def test_detail_unknown_message(self):
        _, _, err = _run("show", "deps", "does-not-exist-xyz", check=False)
        self.assertIn("not found", err)

    def test_tree_and_name_mutually_exclusive(self):
        _, rc, err = _run("show", "deps", "--tree", "pyyaml", check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("mutually exclusive", err)

    def test_no_ivpm_yaml_exits_1(self):
        _, rc, err = _run("show", "deps", "-p", "/tmp", check=False)
        self.assertEqual(rc, 1)
        self.assertIn("ivpm.yaml", err)

    def test_dep_set_flag(self):
        out, rc, _ = _run("show", "deps", "-d", "default", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)
