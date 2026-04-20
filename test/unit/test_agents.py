import logging
import os
import unittest
from unittest.mock import patch
from .test_base import TestBase


class TestAgents(TestBase):

    # ------------------------------------------------------------------ #
    # Basic symlink creation                                               #
    # ------------------------------------------------------------------ #

    def test_agents_dir_created(self):
        """Single dep with root SKILLS.md → .agents/skills/<pkg> symlink created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_basic
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agents_leaf1")
        self.assertTrue(os.path.exists(link), ".agents/skills/agents_leaf1 should exist")

    def test_symlink_is_relative(self):
        """Symlink target is a relative path (does not start with '/')."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_relative
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agents_leaf1")
        self.assertTrue(os.path.islink(link), "Should be a symlink")
        target = os.readlink(link)
        self.assertFalse(os.path.isabs(target),
                         "Symlink target should be relative, got: %s" % target)

    def test_skill_md_fallback(self):
        """Dep has only SKILL.md (not SKILLS.md) → still picked up."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_fallback
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agents_leaf2")
        self.assertTrue(os.path.exists(link))

    def test_symlink_skill_md_accessible(self):
        """SKILL.md is readable through the created symlink."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_readable
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agents_leaf2")
        skill_via_link = os.path.join(link, "SKILL.md")
        self.assertTrue(os.path.isfile(skill_via_link),
                        "SKILL.md should be accessible through the symlink")

    # ------------------------------------------------------------------ #
    # Claude mirroring                                                     #
    # ------------------------------------------------------------------ #

    def test_claude_false_default(self):
        """claude: absent → .claude/ not created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_claude
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        claude_dir = os.path.join(self.testdir, ".claude")
        self.assertFalse(os.path.isdir(claude_dir), ".claude/ should not be created")

    def test_claude_true(self):
        """claude: true → both .agents/skills/ and .claude/skills/ populated."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_claude
            with:
                agents:
                    claude: true
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        agents_link = os.path.join(self.testdir, ".agents", "skills", "agents_leaf1")
        claude_link = os.path.join(self.testdir, ".claude", "skills", "agents_leaf1")
        self.assertTrue(os.path.exists(agents_link), ".agents/skills/agents_leaf1 should exist")
        self.assertTrue(os.path.exists(claude_link), ".claude/skills/agents_leaf1 should exist")

    # ------------------------------------------------------------------ #
    # No skills → no directory                                            #
    # ------------------------------------------------------------------ #

    def test_no_skills_no_dir(self):
        """No deps with skills → .agents/ not created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_none
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        agents_dir = os.path.join(self.testdir, ".agents")
        self.assertFalse(os.path.isdir(agents_dir), ".agents/ should not be created")

    # ------------------------------------------------------------------ #
    # Bad frontmatter                                                      #
    # ------------------------------------------------------------------ #

    def test_bad_frontmatter_warns(self):
        """Malformed frontmatter → warning logged, package skipped."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_bad_fm
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_bad_frontmatter
                      url: file://${DATA_DIR}/agents_bad_frontmatter
                      src: dir
        """)
        with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("malformed frontmatter" in msg or "missing" in msg for msg in cm.output))
        agents_dir = os.path.join(self.testdir, ".agents")
        self.assertFalse(os.path.isdir(agents_dir))

    # ------------------------------------------------------------------ #
    # Multiple packages                                                    #
    # ------------------------------------------------------------------ #

    def test_multiple_packages(self):
        """Two deps each with SKILL.md → both appear in .agents/skills/."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_multi_pkg
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agents_leaf1")))
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agents_leaf2")))

    # ------------------------------------------------------------------ #
    # Stale entry cleanup                                                  #
    # ------------------------------------------------------------------ #

    def test_stale_links_removed(self):
        """Second ivpm update after removing a dep → old symlink removed."""
        # First run: two deps
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_stale
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agents_leaf1")))
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agents_leaf2")))

        # Second run: only one dep
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_stale
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agents_leaf1")))
        self.assertFalse(os.path.exists(os.path.join(skills_dir, "agents_leaf2")),
                         "Stale agents_leaf2 link should have been removed")

    # ------------------------------------------------------------------ #
    # Dep's own ivpm.yaml skill declaration (priority 2)                  #
    # ------------------------------------------------------------------ #

    def test_declared_skill_paths(self):
        """Dep's ivpm.yaml lists non-root path → that directory is linked."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_declared
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_multi_skill
                      url: file://${DATA_DIR}/agents_multi_skill
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        # Two skill paths → links named agents_multi_skill-1 and agents_multi_skill-2
        link1 = os.path.join(skills_dir, "agents_multi_skill-1")
        link2 = os.path.join(skills_dir, "agents_multi_skill-2")
        self.assertTrue(os.path.exists(link1), "First declared skill path should be linked")
        self.assertTrue(os.path.exists(link2), "Second declared skill path should be linked")

    def test_declared_paths_override_probe(self):
        """Dep declares skills: in ivpm.yaml → default probe is not used."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_override_probe
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_multi_skill
                      url: file://${DATA_DIR}/agents_multi_skill
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        # With explicit paths, there should be no plain 'agents_multi_skill' link
        plain_link = os.path.join(skills_dir, "agents_multi_skill")
        self.assertFalse(os.path.exists(plain_link),
                         "Plain link should not exist when explicit paths are declared")

    # ------------------------------------------------------------------ #
    # Consumer dep-entry agents: override (priority 1)                    #
    # ------------------------------------------------------------------ #

    def test_dep_spec_overrides_self(self):
        """Consumer dep-entry agents: takes priority over dep's own ivpm.yaml."""
        # agents_multi_skill declares two paths; we override with just one
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_dep_spec_override
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_multi_skill
                      url: file://${DATA_DIR}/agents_multi_skill
                      src: dir
                      agents:
                          skills:
                              - SKILL.md
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        # Only one path → link named 'agents_multi_skill' (no suffix)
        link = os.path.join(skills_dir, "agents_multi_skill")
        self.assertTrue(os.path.exists(link), "Consumer-specified skill should be linked")
        # Suffix links should not exist
        self.assertFalse(os.path.exists(link + "-1"))
        self.assertFalse(os.path.exists(link + "-2"))

    def test_dep_spec_no_ivpm_yaml(self):
        """Non-IVPM dep (no ivpm.yaml) with agents: in consumer dep-entry works."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_ivpm_yaml
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_no_ivpm_yaml
                      url: file://${DATA_DIR}/agents_no_ivpm_yaml
                      src: dir
                      agents:
                          skills:
                              - SKILL.md
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agents_no_ivpm_yaml")
        self.assertTrue(os.path.exists(link))

    # ------------------------------------------------------------------ #
    # Glob patterns                                                        #
    # ------------------------------------------------------------------ #

    def test_dep_spec_skill_patterns(self):
        """Dep entry with glob pattern skills/**/SKILL.md — all matches linked."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_glob
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_glob_tree
                      url: file://${DATA_DIR}/agents_glob_tree
                      src: dir
                      agents:
                          skills:
                              - skills/**/SKILL.md
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        link1 = os.path.join(skills_dir, "agents_glob_tree-1")
        link2 = os.path.join(skills_dir, "agents_glob_tree-2")
        self.assertTrue(os.path.exists(link1), "First glob match should be linked")
        self.assertTrue(os.path.exists(link2), "Second glob match should be linked")

    def test_dep_spec_pattern_no_match_warns(self):
        """Glob pattern with no matches → warning logged, no link."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_match
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
                      agents:
                          skills:
                              - nonexistent/**/SKILL.md
        """)
        with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("matched no files" in msg for msg in cm.output))
        agents_dir = os.path.join(self.testdir, ".agents")
        self.assertFalse(os.path.isdir(agents_dir))

    # ------------------------------------------------------------------ #
    # Copy fallback (no symlink support)                                   #
    # ------------------------------------------------------------------ #

    def test_companion_dirs_copied_on_fallback(self):
        """On copy fallback: scripts/ and assets/ are copied alongside SKILL.md."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_copy_fallback
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_with_assets
                      url: file://${DATA_DIR}/agents_with_assets
                      src: dir
        """)

        with patch("ivpm.handlers.package_handler_agents._symlinks_supported",
                   return_value=False):
            self.ivpm_update(skip_venv=True)

        dest = os.path.join(self.testdir, ".agents", "skills", "agents_with_assets")
        self.assertTrue(os.path.isdir(dest))
        self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")))
        self.assertTrue(os.path.isdir(os.path.join(dest, "scripts")))
        self.assertTrue(os.path.isdir(os.path.join(dest, "assets")))


if __name__ == "__main__":
    unittest.main()
