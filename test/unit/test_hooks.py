"""
Tests for project hooks ('package: hooks:' in ivpm.yaml).

- Parsing: the entry format, dedenting, error locations, root-only reading
- Include merge: local hooks first, duplicate names across files
- Runner: PackageHandlerHooks against a stand-in venv (no real update)

End-to-end tests (a real 'ivpm update') are in test_hooks_e2e.py.
"""
import io
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import MagicMock

from ivpm.yamlsrc import SrcLoaderError
from ivpm.ivpm_yaml_reader import IvpmYamlReader


def _parse(body, is_root=True, name="ivpm.yaml"):
    """Parse a manifest whose 'package:' body is *body* (indented by 2)."""
    text = "package:\n  name: demo\n" + textwrap.indent(
        textwrap.dedent(body), "  ")
    return IvpmYamlReader().read(io.StringIO(text), name, is_root=is_root)


class TestHookParsing(unittest.TestCase):

    def assertFatal(self, body, *fragments, line=None):
        with self.assertRaises(SrcLoaderError) as cm:
            _parse(body)
        msg = str(cm.exception)
        for f in fragments:
            self.assertIn(f, msg)
        if line is not None:
            self.assertIn("ivpm.yaml:%d:" % line, msg)
        return msg

    def test_no_hooks(self):
        self.assertEqual(_parse("dep-sets: []\n").hooks, [])

    def test_minimal(self):
        pi = _parse("""\
            hooks:
              - event: post-update
                python: |
                  from setuptools import setup
                  setup(script_args=["egg_info", "-e", "src"])
            """)
        self.assertEqual(len(pi.hooks), 1)
        h = pi.hooks[0]
        self.assertEqual(h.events, ["post-update"])
        self.assertEqual(h.kind, "python")
        self.assertEqual(h.source,
                         'from setuptools import setup\n'
                         'setup(script_args=["egg_info", "-e", "src"])')
        self.assertIsNone(h.name)
        self.assertEqual(h.label("post-update", 1), "post-update hook #1")

    def test_event_list_and_name(self):
        pi = _parse("""\
            hooks:
              - name: egg-info
                event: [post-update]
                python: x = 1
            """)
        h = pi.hooks[0]
        self.assertEqual(h.events, ["post-update"])
        self.assertEqual(h.name, "egg-info")
        self.assertEqual(h.label("post-update", 1), "egg-info")

    def test_order_preserved(self):
        pi = _parse("""\
            hooks:
              - {name: a, event: post-update, python: "x = 1"}
              - {name: b, event: post-update, python: "x = 2"}
              - {name: c, event: post-update, python: "x = 3"}
            """)
        self.assertEqual([h.name for h in pi.hooks], ["a", "b", "c"])

    # --- dedenting -------------------------------------------------------

    def test_dedent_quoted_string(self):
        pi = _parse("""\
            hooks:
              - event: post-update
                python: "  import os\\n  print(1)\\n"
            """)
        self.assertEqual(pi.hooks[0].source, "import os\nprint(1)")

    def test_dedent_over_indented_block(self):
        pi = _parse("""\
            hooks:
              - event: post-update
                python: |4
                      import os
                      print(1)
            """)
        self.assertEqual(pi.hooks[0].source, "import os\nprint(1)")

    def test_dedent_strips_blank_lines(self):
        pi = _parse("""\
            hooks:
              - event: post-update
                python: "\\n\\n  x = 1\\n\\n"
            """)
        self.assertEqual(pi.hooks[0].source, "x = 1")

    def test_dedent_keeps_relative_indentation(self):
        pi = _parse("""\
            hooks:
              - event: post-update
                python: "  if True:\\n      x = 1\\n  y = 2"
            """)
        self.assertEqual(pi.hooks[0].source, "if True:\n    x = 1\ny = 2")

    # --- variables --------------------------------------------------------

    def test_variable_substitution(self):
        pi = _parse("""\
            vars:
              egg_base: src
            hooks:
              - event: post-update
                python: |
                  base = "${{egg_base}}"
                  literal = "$${{x}}"
            """)
        self.assertEqual(pi.hooks[0].source,
                         'base = "src"\nliteral = "${{x}}"')

    # --- root only --------------------------------------------------------

    def test_dependency_hooks_ignored(self):
        pi = _parse("""\
            hooks:
              - event: post-update
                python: x = 1
            """, is_root=False)
        self.assertEqual(pi.hooks, [])

    def test_dependency_malformed_hooks_ignored(self):
        pi = _parse("""\
            hooks:
              - event: no-such-event
                bogus: 1
            """, is_root=False)
        self.assertEqual(pi.hooks, [])

    # --- errors -----------------------------------------------------------

    def test_err_not_a_list(self):
        self.assertFatal("""\
            hooks:
              event: post-update
            """, "must be a list")

    def test_err_entry_not_mapping(self):
        self.assertFatal("""\
            hooks:
              - post-update
            """, "Hook entry must be a mapping")

    def test_err_missing_event(self):
        self.assertFatal("""\
            hooks:
              - python: x = 1
            """, "missing 'event'", line=4)

    def test_err_unknown_event(self):
        self.assertFatal("""\
            hooks:
              - event: post-updat
                python: x = 1
            """, "Unknown hook event 'post-updat'",
            "Did you mean 'post-update'", line=4)

    def test_err_empty_event_list(self):
        self.assertFatal("""\
            hooks:
              - event: []
                python: x = 1
            """, "list is empty")

    def test_err_no_kind(self):
        self.assertFatal("""\
            hooks:
              - event: post-update
            """, "exactly one of: python")

    def test_err_unknown_key(self):
        self.assertFatal("""\
            hooks:
              - event: post-update
                python: x = 1
                cwd: src
            """, "Unknown key 'cwd'", line=6)

    def test_err_python_not_string(self):
        self.assertFatal("""\
            hooks:
              - event: post-update
                python: [a, b]
            """, "must be a string of Python source", line=5)

    def test_err_empty_source(self):
        self.assertFatal("""\
            hooks:
              - event: post-update
                python: "  \\n  "
            """, "source is empty")

    def test_err_inconsistent_indentation(self):
        self.assertFatal("""\
            hooks:
              - event: post-update
                python: "    x = 1\\n  y = 2"
            """, "inconsistent indentation", line=5)

    def test_err_bad_name(self):
        self.assertFatal("""\
            hooks:
              - name: "has space"
                event: post-update
                python: x = 1
            """, "Invalid hook name 'has space'", line=4)

    def test_err_duplicate_name(self):
        msg = self.assertFatal("""\
            hooks:
              - name: a
                event: post-update
                python: x = 1
              - name: a
                event: post-update
                python: x = 2
            """, "Duplicate hook 'a'", "previously defined @ ivpm.yaml:4:")
        self.assertIn("ivpm.yaml:7:", msg)


class TestHookInclude(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ivpm-hooks-")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self._dir, name)
        with open(path, "w") as fp:
            fp.write(textwrap.dedent(content))
        return path

    def _read(self):
        path = os.path.join(self._dir, "ivpm.yaml")
        with open(path) as fp:
            return IvpmYamlReader().read(fp, path, is_root=True)

    _ROOT = ("package:\n"
             "  name: demo\n"
             "  include: [ivpm.hooks.yaml]\n")

    def _root(self, extra=""):
        """The root manifest, plus *extra* lines at package level."""
        return self._ROOT + extra

    def test_adopted_from_include(self):
        self._write("ivpm.hooks.yaml", """\
            package:
              hooks:
                - {name: inc, event: post-update, python: "x = 1"}
            """)
        self._write("ivpm.yaml", self._root())
        self.assertEqual([h.name for h in self._read().hooks], ["inc"])

    def test_local_first_then_include(self):
        self._write("ivpm.hooks.yaml", """\
            package:
              hooks:
                - {name: inc, event: post-update, python: "x = 1"}
            """)
        self._write("ivpm.yaml", self._root(
            "  hooks:\n"
            "    - {name: local, event: post-update, python: \"x = 2\"}\n"))
        self.assertEqual([h.name for h in self._read().hooks],
                         ["local", "inc"])

    def test_duplicate_across_files(self):
        self._write("ivpm.hooks.yaml", """\
            package:
              hooks:
                - {name: dup, event: post-update, python: "x = 1"}
            """)
        self._write("ivpm.yaml", self._root(
            "  hooks:\n"
            "    - {name: dup, event: post-update, python: \"x = 2\"}\n"))
        with self.assertRaises(SrcLoaderError) as cm:
            self._read()
        msg = str(cm.exception)
        self.assertIn("Duplicate hook 'dup'", msg)
        self.assertIn("ivpm.hooks.yaml", msg)
        self.assertIn("ivpm.yaml", msg)


class TestHookRunner(unittest.TestCase):
    """PackageHandlerHooks against a stand-in venv: deps/python/bin/python is a
    symlink to the interpreter running the tests."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ivpm-hooks-")
        self.project = os.path.join(self._dir, "proj")
        self.deps = os.path.join(self.project, "packages")
        os.makedirs(self.project)
        self.out = os.path.join(self._dir, "out.txt")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _make_venv(self):
        bindir = os.path.join(self.deps, "python", "bin")
        os.makedirs(bindir)
        os.symlink(sys.executable, os.path.join(bindir, "python"))

    def _hooks(self, body):
        return _parse(body).hooks

    def _update_info(self, hooks, no_hooks=False, skip_venv=False,
                     python_config=None):
        from ivpm.project_ops_info import ProjectUpdateInfo
        args = MagicMock()
        args.no_hooks = no_hooks
        args.py_skip_install = False
        args.py_uv = False
        args.py_pip = False
        return ProjectUpdateInfo(args, self.deps,
                                 project_dir=self.project,
                                 skip_venv=skip_venv,
                                 python_config=python_config,
                                 suppress_output=True,
                                 hooks=hooks)

    def _run(self, ui):
        from ivpm.handlers.package_handler_hooks import PackageHandlerHooks
        PackageHandlerHooks().on_root_post_load(ui)

    def _appender(self, text):
        # A hook that appends *text* to self.out
        return "open(%r, 'a').write(%r)" % (self.out, text)

    def _read_out(self):
        with open(self.out) as fp:
            return fp.read()

    def test_cwd_and_env(self):
        self._make_venv()
        hooks = self._hooks("""\
            hooks:
              - event: post-update
                python: |
                  import os, json
                  json.dump({"cwd": os.getcwd(),
                             "venv": os.environ.get("VIRTUAL_ENV"),
                             "path0": os.environ["PATH"].split(os.pathsep)[0],
                             "proj": os.environ.get("IVPM_PROJECT"),
                             "pkgs": os.environ.get("IVPM_PACKAGES")},
                            open(%r, "w"))
            """ % self.out)
        self._run(self._update_info(hooks))
        import json
        with open(self.out) as fp:
            d = json.load(fp)
        venv = os.path.join(self.deps, "python")
        self.assertEqual(os.path.realpath(d["cwd"]), os.path.realpath(self.project))
        self.assertEqual(d["venv"], venv)
        self.assertEqual(d["path0"], os.path.join(venv, "bin"))
        self.assertEqual(d["proj"], self.project)
        self.assertEqual(d["pkgs"], self.deps)

    def test_declared_order(self):
        self._make_venv()
        hooks = self._hooks("""\
            hooks:
              - {event: post-update, python: "%s"}
              - {event: post-update, python: "%s"}
            """ % (self._appender("1"), self._appender("2")))
        self._run(self._update_info(hooks))
        self.assertEqual(self._read_out(), "12")

    def test_failure_stops_and_reports(self):
        from ivpm.handlers.package_handler import HandlerFatalError
        self._make_venv()
        hooks = self._hooks("""\
            hooks:
              - name: boom
                event: post-update
                python: |
                  print("about to fail")
                  raise SystemExit(3)
              - {event: post-update, python: "%s"}
            """ % self._appender("ran"))
        with self.assertRaises(HandlerFatalError) as cm:
            self._run(self._update_info(hooks))
        msg = str(cm.exception)
        self.assertIn("hook 'boom' (post-update", msg)
        self.assertIn("exit code 3", msg)
        self.assertIn("about to fail", msg)
        self.assertFalse(os.path.exists(self.out))

    def test_no_hooks_flag(self):
        self._make_venv()
        hooks = self._hooks("""\
            hooks:
              - {event: post-update, python: "%s"}
            """ % self._appender("ran"))
        self._run(self._update_info(hooks, no_hooks=True))
        self.assertFalse(os.path.exists(self.out))

    def test_no_venv_venv_disabled(self):
        from ivpm.handlers.package_handler import HandlerFatalError
        from ivpm.proj_info import PythonConfig, VenvMode
        hooks = self._hooks("""\
            hooks:
              - {name: egg, event: post-update, python: "x = 1"}
            """)
        cfg = PythonConfig()
        cfg.venv = VenvMode.SKIP
        with self.assertRaises(HandlerFatalError) as cm:
            self._run(self._update_info(hooks, python_config=cfg))
        msg = str(cm.exception)
        self.assertIn("hook 'egg'", msg)
        self.assertIn("venv: false", msg)

    def test_existing_venv_used_when_install_skipped(self):
        self._make_venv()
        hooks = self._hooks("""\
            hooks:
              - {event: post-update, python: "%s"}
            """ % self._appender("ran"))
        self._run(self._update_info(hooks, skip_venv=True))
        self.assertEqual(self._read_out(), "ran")

    def test_no_hooks_declared(self):
        # No venv and no hooks: nothing to do, and no error
        self._run(self._update_info([]))


if __name__ == "__main__":
    unittest.main()
