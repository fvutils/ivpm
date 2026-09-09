"""
Unit tests for ``ivpm.variables``: platform builtins, ``match`` evaluation,
precedence, and the derived-variable rules.

The highest-value cases here are the ones that fail *silently* if the
implementation is wrong:

* ``TestPersistence`` -- a derived variable that reaches ``ivpm.json`` will
  beat the match on a different machine, with no error anywhere.
* ``TestInertness`` -- a mapping that merely looks like a match node, and a
  package that uses no derived variable at all, must behave exactly as before.
"""
import io
import json
import os
import unittest
from unittest import mock

from ivpm import platform_info
from ivpm.variables import (
    get_used_vars, parse_definitions, resolve_variables)
from ivpm.yamlsrc import SrcLoaderError, load as yaml_load


def _fake_platform(os_name="linux", arch="x86_64", libc="glibc",
                   libc_version="2.39", distro="ubuntu",
                   distro_version="24.04"):
    """Patch ``platform_info.probe`` to report a fixed machine."""
    pi = platform_info.PlatformInfo(
        os=os_name, arch=arch, libc=libc, libc_version=libc_version,
        distro=distro, distro_version=distro_version)
    return mock.patch.object(platform_info, "probe", return_value=pi)


class _VarTestBase(unittest.TestCase):

    def _doc(self, text):
        """Parse a YAML body into a srcinfo-carrying dict."""
        return yaml_load(io.StringIO(text), "ivpm.yaml")

    def _resolve(self, text, cli=None, persisted=None, derived_out=None,
                 **platform_kw):
        doc = self._doc(text)
        with _fake_platform(**platform_kw):
            return resolve_variables(
                doc, cli or {}, persisted or {}, derived_out=derived_out)


# ------------------------------------------------------------------
# match semantics
# ------------------------------------------------------------------

class TestMatch(_VarTestBase):

    def test_case_hit(self):
        doc, resolved = self._resolve("""
vars:
  p: { match: { on: "${{ivpm_os}}", cases: { linux: linux, macos: mac } } }
url: "https://example.com/${{p}}/x.tgz"
""", os_name="linux")
        self.assertEqual("linux", resolved["p"])
        self.assertEqual("https://example.com/linux/x.tgz", doc["url"])

    def test_default_taken(self):
        _, resolved = self._resolve("""
vars:
  ext: { match: { on: "${{ivpm_os}}", cases: { windows: zip }, default: tar.xz } }
""", os_name="linux")
        self.assertEqual("tar.xz", resolved["ext"])

    def test_no_match_no_default_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  p: { match: { on: "${{ivpm_arch}}", cases: { x86_64: a, arm64: b } } }
""", arch="riscv64")
        msg = str(ctx.exception)
        # The message must name what was matched *and* what was available:
        # yielding empty silently is how a construct like this rots.
        self.assertIn("riscv64", msg)
        self.assertIn("'p'", msg)
        self.assertIn("x86_64", msg)
        self.assertIn("arm64", msg)

    def test_nested_match(self):
        _, resolved = self._resolve("""
vars:
  sfx:
    match:
      on: "${{ivpm_os}}"
      cases:
        linux:
          match:
            on: "${{ivpm_arch}}"
            cases: { x86_64: "", arm64: "-arm64" }
        macos: "-mac"
""", os_name="linux", arch="arm64")
        self.assertEqual("-arm64", resolved["sfx"])

    def test_case_value_mapping_spliced_at_document_level(self):
        doc, _ = self._resolve("""
vars:
  unused: x
env:
  match:
    on: "${{ivpm_os}}"
    cases:
      linux: { CC: gcc, LD: ld }
      macos: { CC: clang }
""", os_name="linux")
        self.assertEqual({"CC": "gcc", "LD": "ld"}, dict(doc["env"]))

    def test_case_value_list_spliced_at_document_level(self):
        doc, _ = self._resolve("""
flags:
  match:
    on: "${{ivpm_os}}"
    cases:
      linux: [-fPIC, -O2]
      macos: [-O2]
""", os_name="linux")
        self.assertEqual(["-fPIC", "-O2"], list(doc["flags"]))

    def test_unquoted_numeric_case_key(self):
        # YAML reads 24.04 as a float; 'on' is always a string. The str()
        # coercion is what makes the unquoted form work.
        _, resolved = self._resolve("""
vars:
  rel: { match: { on: "${{ivpm_distro_version}}", cases: { 24.04: noble, 22.04: jammy } } }
""", distro_version="24.04")
        self.assertEqual("noble", resolved["rel"])

    def test_on_referencing_non_builtin_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  base: linux
  p: { match: { on: "${{base}}", cases: { linux: l } } }
""")
        self.assertIn("builtins only", str(ctx.exception))

    def test_unknown_key_inside_match_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  p: { match: { on: "${{ivpm_os}}", when: linux, cases: { linux: l } } }
""")
        msg = str(ctx.exception)
        self.assertIn("'when'", msg)
        self.assertIn("cases", msg)

    def test_match_missing_cases_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  p: { match: { on: "${{ivpm_os}}" } }
""")
        self.assertIn("'on' and 'cases'", str(ctx.exception))

    def test_match_missing_on_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  p: { match: { cases: { linux: l } } }
""")
        self.assertIn("'on' and 'cases'", str(ctx.exception))

    def test_non_scalar_match_result_in_vars_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  p: { match: { on: "${{ivpm_os}}", cases: { linux: { a: b } } } }
""", os_name="linux")
        self.assertIn("must be a scalar", str(ctx.exception))


class TestInertness(_VarTestBase):

    def test_mapping_that_is_not_a_match_node_is_untouched(self):
        # 'match' beside another key is data, not a match node. This is what
        # keeps the feature inert for manifests that predate it.
        doc, _ = self._resolve("""
with:
  match: something
  other: value
""")
        self.assertEqual({"match": "something", "other": "value"},
                         dict(doc["with"]))

    def test_manifest_with_no_vars_block(self):
        derived = set()
        doc, resolved = self._resolve("""
name: proj
url: "https://example.com/x.tgz"
""", derived_out=derived)
        self.assertEqual("https://example.com/x.tgz", doc["url"])
        # Builtins are seeded but nothing referenced them, so no user-visible
        # variable is resolved from the document's point of view...
        self.assertNotIn("vars", doc)
        # ...and every derived name is a builtin.
        self.assertTrue(all(n.startswith("ivpm_") for n in derived))

    def test_escape_sequence_still_literal(self):
        doc, _ = self._resolve("""
text: "$${{not_a_var}}"
""")
        self.assertEqual("${{not_a_var}}", doc["text"])

    def test_escape_inside_match_case_is_not_double_expanded(self):
        # _eval_match fully resolves the selected value; walking it again
        # would expand the ${{ }} that the escape just produced.
        doc, _ = self._resolve("""
text:
  match:
    on: "${{ivpm_os}}"
    cases:
      linux: "$${{not_a_var}}"
""", os_name="linux")
        self.assertEqual("${{not_a_var}}", doc["text"])

    def test_undefined_variable_still_fatal(self):
        with self.assertRaises(SrcLoaderError):
            self._resolve("""
url: "${{nope}}"
""")


# ------------------------------------------------------------------
# Builtins, precedence, reservation
# ------------------------------------------------------------------

class TestBuiltins(_VarTestBase):

    def test_builtin_expands_in_url(self):
        doc, _ = self._resolve("""
url: "https://example.com/${{ivpm_os}}-${{ivpm_arch}}.tgz"
""", os_name="macos", arch="arm64")
        self.assertEqual("https://example.com/macos-arm64.tgz", doc["url"])

    def test_ivpm_platform(self):
        doc, _ = self._resolve("""
url: "${{ivpm_platform}}"
""", os_name="linux", arch="x86_64")
        self.assertEqual("linux-x86_64", doc["url"])

    def test_ivpm_platform_follows_an_os_override(self):
        # ivpm_platform is a spelling of "{ivpm_os}-{ivpm_arch}", so it must
        # track an override of either. Leaving it at the probed value makes the
        # two disagree -- and it is what the lock's resolved_on records, so a
        # cross-resolved entry would be tagged with the machine that built it.
        doc, resolved = self._resolve("""
url: "${{ivpm_platform}}"
""", cli={"ivpm_os": "macos", "ivpm_arch": "arm64"},
             os_name="linux", arch="x86_64")
        self.assertEqual("macos-arm64", doc["url"])
        self.assertEqual("macos-arm64", resolved["ivpm_platform"])

    def test_ivpm_platform_follows_an_env_override(self):
        with mock.patch.dict(os.environ, {"IVPM_VAR_IVPM_ARCH": "arm64"}):
            _, resolved = self._resolve("""
name: p
""", os_name="linux", arch="x86_64")
        self.assertEqual("linux-arm64", resolved["ivpm_platform"])

    def test_explicit_ivpm_platform_override_still_wins(self):
        # Deriving over the top of it would make the override unusable.
        _, resolved = self._resolve("""
name: p
""", cli={"ivpm_platform": "custom-thing"}, os_name="linux", arch="x86_64")
        self.assertEqual("custom-thing", resolved["ivpm_platform"])

    def test_cli_override_of_builtin(self):
        doc, resolved = self._resolve("""
url: "${{ivpm_os}}"
""", cli={"ivpm_os": "windows"}, os_name="linux")
        self.assertEqual("windows", doc["url"])
        self.assertEqual("windows", resolved["ivpm_os"])

    def test_env_override_of_builtin(self):
        with mock.patch.dict(os.environ, {"IVPM_VAR_IVPM_OS": "windows"}):
            doc, _ = self._resolve("""
url: "${{ivpm_os}}"
""", os_name="linux")
        self.assertEqual("windows", doc["url"])

    def test_cli_beats_env(self):
        with mock.patch.dict(os.environ, {"IVPM_VAR_IVPM_OS": "macos"}):
            doc, _ = self._resolve("""
url: "${{ivpm_os}}"
""", cli={"ivpm_os": "windows"}, os_name="linux")
        self.assertEqual("windows", doc["url"])

    def test_override_drives_the_match(self):
        # The point of overridability: resolve a Linux artifact on a Mac.
        _, resolved = self._resolve("""
vars:
  p: { match: { on: "${{ivpm_os}}", cases: { linux: linux, macos: mac } } }
""", cli={"ivpm_os": "linux"}, os_name="macos")
        self.assertEqual("linux", resolved["p"])

    def test_reserved_prefix_is_fatal(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            self._resolve("""
vars:
  ivpm_foo: bar
""")
        msg = str(ctx.exception)
        self.assertIn("ivpm_foo", msg)
        self.assertIn("reserved", msg)

    def test_undeclared_cli_override_still_fatal(self):
        with self.assertRaises(SrcLoaderError):
            self._resolve("""
vars:
  a: 1
""", cli={"b": "2"})


class TestDerivedSet(_VarTestBase):

    def test_builtins_and_match_results_are_derived(self):
        derived = set()
        self._resolve("""
vars:
  pinned: "1.2.3"
  p: { match: { on: "${{ivpm_os}}", cases: { linux: linux } } }
""", derived_out=derived, os_name="linux")
        self.assertIn("p", derived)
        self.assertIn("ivpm_os", derived)
        self.assertNotIn("pinned", derived)

    def test_used_vars_recorded_per_node(self):
        doc, _ = self._resolve("""
vars:
  pinned: "1.2.3"
  p: { match: { on: "${{ivpm_os}}", cases: { linux: linux } } }
deps:
- name: a
  url: "https://example.com/${{p}}/x.tgz"
- name: b
  url: "https://example.com/${{pinned}}/x.tgz"
""", os_name="linux")
        a, b = doc["deps"][0], doc["deps"][1]
        self.assertEqual({"p"}, get_used_vars(a))
        self.assertEqual({"pinned"}, get_used_vars(b))

    def test_used_vars_records_the_on_reference_of_a_document_match(self):
        # The choice depended on ivpm_os just as much as an interpolation
        # would have, so the entry is platform-specific and must say so.
        doc, _ = self._resolve("""
deps:
- name: a
  url:
    match:
      on: "${{ivpm_os}}"
      cases: { linux: "https://example.com/l.tgz" }
""", os_name="linux")
        self.assertEqual({"ivpm_os"}, get_used_vars(doc["deps"][0]))


# ------------------------------------------------------------------
# Persistence -- the regression that reaches users
# ------------------------------------------------------------------

class TestPersistence(_VarTestBase):

    _MANIFEST = """
vars:
  pinned: "1.2.3"
  p: { match: { on: "${{ivpm_os}}", cases: { linux: linux, macos: mac } } }
url: "https://example.com/${{p}}/${{pinned}}/x.tgz"
"""

    def _persisted_state(self, resolved, derived):
        """What project_ops writes into ivpm.json's 'vars'."""
        return {k: v for k, v in resolved.items() if k not in derived}

    def test_derived_names_absent_from_persisted_state(self):
        derived = set()
        _, resolved = self._resolve(
            self._MANIFEST, derived_out=derived, os_name="linux")
        state = self._persisted_state(resolved, derived)
        self.assertEqual({"pinned": "1.2.3"}, state)
        self.assertNotIn("p", state)
        for name in resolved:
            if name.startswith("ivpm_"):
                self.assertNotIn(name, state)
        # It must survive a JSON round trip unchanged (this is a state file).
        self.assertEqual(state, json.loads(json.dumps(state)))

    def test_stale_derived_value_does_not_win_on_another_platform(self):
        # Resolve on Linux, persist, then re-resolve on macOS *feeding that
        # state back in*. If 'p' had been persisted it would sit above the
        # default in precedence and silently select the Linux artifact.
        derived = set()
        _, resolved = self._resolve(
            self._MANIFEST, derived_out=derived, os_name="linux")
        state = self._persisted_state(resolved, derived)

        doc2, resolved2 = self._resolve(
            self._MANIFEST, persisted=state, os_name="macos", arch="arm64")
        self.assertEqual("mac", resolved2["p"])
        self.assertEqual("https://example.com/mac/1.2.3/x.tgz", doc2["url"])

    def test_a_persisted_derived_value_would_have_won(self):
        # Proves the previous test is actually testing something: hand the
        # unfiltered map back and the stale value does take over.
        derived = set()
        _, resolved = self._resolve(
            self._MANIFEST, derived_out=derived, os_name="linux")
        _, resolved2 = self._resolve(
            self._MANIFEST, persisted=dict(resolved), os_name="macos")
        self.assertEqual("linux", resolved2["p"])

    def test_cli_override_of_derived_var_honoured_but_not_persisted(self):
        derived = set()
        _, resolved = self._resolve(
            self._MANIFEST, cli={"p": "mac"}, derived_out=derived,
            os_name="linux")
        self.assertEqual("mac", resolved["p"])
        self.assertNotIn("p", self._persisted_state(resolved, derived))

    def test_old_state_file_with_only_pinned_vars_read_unchanged(self):
        _, resolved = self._resolve(
            self._MANIFEST, persisted={"pinned": "9.9.9"}, os_name="linux")
        self.assertEqual("9.9.9", resolved["pinned"])
        self.assertEqual("linux", resolved["p"])


# ------------------------------------------------------------------
# The worked example (design 5.7): the acceptance test
# ------------------------------------------------------------------

class TestEmscriptenExample(_VarTestBase):

    _MANIFEST = """
name: my-project
vars:
  emsdk_hash: f04ea239d533260dd1db760dd2d668d5f9a88d6b
  p:   { match: { on: "${{ivpm_os}}",   cases: { linux: linux, macos: mac, windows: win } } }
  sfx: { match: { on: "${{ivpm_arch}}", cases: { x86_64: "", arm64: "-arm64" } } }
  ext: { match: { on: "${{ivpm_os}}",   cases: { windows: zip }, default: tar.xz } }
dep-sets:
- name: wasm-build
  deps:
  - name: emsdk
    src: url
    cache: true
    url: https://storage.googleapis.com/webassembly/emscripten-releases-builds/${{p}}/${{emsdk_hash}}/wasm-binaries${{sfx}}.${{ext}}
"""

    _BASE = ("https://storage.googleapis.com/webassembly/"
             "emscripten-releases-builds/%s/"
             "f04ea239d533260dd1db760dd2d668d5f9a88d6b/wasm-binaries%s.%s")

    def _url_for(self, os_name, arch):
        doc, _ = self._resolve(self._MANIFEST, os_name=os_name, arch=arch)
        return doc["dep-sets"][0]["deps"][0]["url"]

    def test_linux_x86_64(self):
        self.assertEqual(self._BASE % ("linux", "", "tar.xz"),
                         self._url_for("linux", "x86_64"))

    def test_linux_arm64(self):
        self.assertEqual(self._BASE % ("linux", "-arm64", "tar.xz"),
                         self._url_for("linux", "arm64"))

    def test_macos_x86_64(self):
        self.assertEqual(self._BASE % ("mac", "", "tar.xz"),
                         self._url_for("macos", "x86_64"))

    def test_macos_arm64(self):
        self.assertEqual(self._BASE % ("mac", "-arm64", "tar.xz"),
                         self._url_for("macos", "arm64"))

    def test_windows_x86_64(self):
        self.assertEqual(self._BASE % ("win", "", "zip"),
                         self._url_for("windows", "x86_64"))


class TestReaderIntegration(_VarTestBase):
    """The whole path: manifest -> ProjInfo/Package, with the derived-variable
    bookkeeping the cache key and lock entry depend on."""

    def _read(self, text, **platform_kw):
        from ivpm.ivpm_yaml_reader import IvpmYamlReader
        with _fake_platform(**platform_kw):
            return IvpmYamlReader().read(io.StringIO(text), "ivpm.yaml")

    _MANIFEST = """
package:
  name: proj
  vars:
    pinned: "1.2.3"
    p: { match: { on: "${{ivpm_os}}", cases: { linux: linux, macos: mac } } }
  dep-sets:
  - name: default
    deps:
    - name: platform-dep
      src: url
      url: "https://example.com/${{p}}/x.tar.gz"
    - name: plain-dep
      src: url
      url: "https://example.com/${{pinned}}/y.tar.gz"
"""

    def test_urls_resolve_per_platform(self):
        info = self._read(self._MANIFEST, os_name="macos", arch="arm64")
        deps = info.dep_set_m["default"]
        self.assertEqual("https://example.com/mac/x.tar.gz",
                         deps["platform-dep"].url)

    def test_derived_vars_recorded_on_proj_info(self):
        info = self._read(self._MANIFEST, os_name="linux")
        self.assertIn("p", info.derived_vars)
        self.assertIn("ivpm_os", info.derived_vars)
        self.assertNotIn("pinned", info.derived_vars)

    def test_used_derived_vars_on_packages(self):
        info = self._read(self._MANIFEST, os_name="linux")
        deps = info.dep_set_m["default"]
        # Platform-specific: cache key and lock entry must say which platform.
        self.assertEqual({"p"}, deps["platform-dep"].used_derived_vars)
        # Not platform-specific: everything about it must be unchanged.
        self.assertEqual(set(), deps["plain-dep"].used_derived_vars)


class TestParseDefinitions(unittest.TestCase):

    def test_basic(self):
        self.assertEqual({"a": "1", "b": "x=y"},
                         parse_definitions(["a=1", "b=x=y"]))

    def test_missing_equals_is_fatal(self):
        with self.assertRaises(SrcLoaderError):
            parse_definitions(["a"])


if __name__ == "__main__":
    unittest.main()
