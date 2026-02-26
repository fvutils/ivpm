import logging
import os
from .test_base import TestBase


class TestSkills(TestBase):

    def test_skills_md_found(self):
        """A single package with SKILLS.md produces packages/SKILLS.md."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_skills_basic
            dep-sets:
                - name: default-dev
                  deps:
                    - name: skills_leaf1
                      url: file://${DATA_DIR}/skills_leaf1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        skills_path = os.path.join(self.testdir, "packages", "SKILLS.md")
        self.assertTrue(os.path.isfile(skills_path),
                        "SKILLS.md should be generated")

        with open(skills_path) as f:
            content = f.read()

        self.assertIn("## skill-leaf1", content)
        self.assertIn("Provides leaf1 capability", content)
        self.assertIn("./skills_leaf1/SKILLS.md", content)
        # Frontmatter header should name the project
        self.assertIn("name: test_skills_basic-skills", content)
        # Optional field should be propagated
        self.assertIn("license", content)
        self.assertIn("Apache-2.0", content)

    def test_skill_md_fallback(self):
        """A package with only SKILL.md (no SKILLS.md) is picked up."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_skills_fallback
            dep-sets:
                - name: default-dev
                  deps:
                    - name: skills_leaf2
                      url: file://${DATA_DIR}/skills_leaf2
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        skills_path = os.path.join(self.testdir, "packages", "SKILLS.md")
        self.assertTrue(os.path.isfile(skills_path))

        with open(skills_path) as f:
            content = f.read()

        self.assertIn("## skill-leaf2", content)
        self.assertIn("leaf2 capability", content)
        self.assertIn("./skills_leaf2/SKILL.md", content)
        # Optional compatibility field should be propagated
        self.assertIn("compatibility", content)

    def test_skills_md_preferred_over_skill_md(self):
        """SKILLS.md is preferred when a package has both files."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_skills_prefer
            dep-sets:
                - name: default-dev
                  deps:
                    - name: skills_both
                      url: file://${DATA_DIR}/skills_both
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        skills_path = os.path.join(self.testdir, "packages", "SKILLS.md")
        self.assertTrue(os.path.isfile(skills_path))

        with open(skills_path) as f:
            content = f.read()

        self.assertIn("SKILLS.md", content)
        self.assertNotIn("skill-both-dot", content,
                         "SKILL.md content should not appear when SKILLS.md is present")

    def test_no_skills_no_file(self):
        """No SKILLS.md generated when no packages have skill files."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_skills_none
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        skills_path = os.path.join(self.testdir, "packages", "SKILLS.md")
        self.assertFalse(os.path.isfile(skills_path),
                         "SKILLS.md should NOT be generated when no skill files exist")

    def test_bad_frontmatter_warns(self):
        """Malformed frontmatter issues a warning but does not crash."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_skills_bad
            dep-sets:
                - name: default-dev
                  deps:
                    - name: skills_bad_frontmatter
                      url: file://${DATA_DIR}/skills_bad_frontmatter
                      src: dir
        """)

        with self.assertLogs("ivpm.handlers.package_handler_skills", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("malformed frontmatter" in msg or "missing" in msg for msg in cm.output),
                        "Expected a warning about bad frontmatter")

        # No SKILLS.md should be written (package was skipped)
        skills_path = os.path.join(self.testdir, "packages", "SKILLS.md")
        self.assertFalse(os.path.isfile(skills_path))

    def test_multiple_packages(self):
        """Both packages appear in the generated SKILLS.md."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_skills_multi
            dep-sets:
                - name: default-dev
                  deps:
                    - name: skills_leaf1
                      url: file://${DATA_DIR}/skills_leaf1
                      src: dir
                    - name: skills_leaf2
                      url: file://${DATA_DIR}/skills_leaf2
                      src: dir
        """)

        self.ivpm_update(skip_venv=True)

        skills_path = os.path.join(self.testdir, "packages", "SKILLS.md")
        self.assertTrue(os.path.isfile(skills_path))

        with open(skills_path) as f:
            content = f.read()

        self.assertIn("## skill-leaf1", content)
        self.assertIn("## skill-leaf2", content)
        self.assertIn("./skills_leaf1/SKILLS.md", content)
        self.assertIn("./skills_leaf2/SKILL.md", content)
