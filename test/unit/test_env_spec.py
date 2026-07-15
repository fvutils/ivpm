import unittest
from ivpm.env_spec import EnvSpec


class TestEnvSpecDirenv(unittest.TestCase):
    """EnvSpec renders each action as a direnv/bash directive. ${VAR} is
    emitted verbatim (expanded by bash at direnv-eval time)."""

    def test_set_scalar(self):
        es = EnvSpec("MY_VAR", "hello world", EnvSpec.Act.Set)
        self.assertEqual(es.as_direnv(), 'export MY_VAR="hello world"')

    def test_set_list_space_joined(self):
        es = EnvSpec("CFLAGS", ["-O2", "-Wall"], EnvSpec.Act.Set)
        self.assertEqual(es.as_direnv(), 'export CFLAGS="-O2 -Wall"')

    def test_path_colon_joined(self):
        es = EnvSpec("LD_LIBRARY_PATH",
                     ["${IVPM_PACKAGES}/lib", "/usr/local/lib"],
                     EnvSpec.Act.Path)
        self.assertEqual(
            es.as_direnv(),
            'export LD_LIBRARY_PATH="${IVPM_PACKAGES}/lib:/usr/local/lib"')

    def test_path_prepend_uses_path_add(self):
        es = EnvSpec("PATH", "${IVPM_PROJECT}/scripts", EnvSpec.Act.PathPrepend)
        self.assertEqual(es.as_direnv(), 'path_add PATH "${IVPM_PROJECT}/scripts"')

    def test_path_prepend_list(self):
        es = EnvSpec("PATH", ["a/bin", "b/bin"], EnvSpec.Act.PathPrepend)
        self.assertEqual(es.as_direnv(), 'path_add PATH "a/bin" "b/bin"')

    def test_path_append_guarded(self):
        es = EnvSpec("EXTRA", "${IVPM_PACKAGES}/extra/bin", EnvSpec.Act.PathAppend)
        self.assertEqual(
            es.as_direnv(),
            'export EXTRA="${EXTRA:+$EXTRA:}${IVPM_PACKAGES}/extra/bin"')


if __name__ == "__main__":
    unittest.main()
