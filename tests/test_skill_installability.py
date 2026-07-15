import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
REFERENCES = ROOT / "references"


class SkillInstallabilityTests(unittest.TestCase):
    def test_skill_entrypoint_stays_within_common_size_limit(self):
        self.assertLessEqual(len(SKILL.read_text(encoding="utf-8").splitlines()), 500)

    def test_workflow_references_exist(self):
        expected = {
            "auto-install-zero-command-line.md",
            "detecting-platform.md",
            "first-run-onboarding.md",
            "content-delivery-digest-run.md",
            "configuration-handling.md",
            "manual-trigger.md",
            "content-sources.md",
        }
        self.assertTrue(expected.issubset({path.name for path in REFERENCES.glob("*.md")}))

    def test_single_file_installers_have_a_runtime_bootstrap(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("## Runtime Bootstrap", skill)
        self.assertIn("https://github.com/Benboerba620/ai-signal.git", skill)
        self.assertIn("scripts/prepare_digest.py", skill)

    def test_instruction_docs_avoid_common_injection_scanner_triggers(self):
        docs = [SKILL, *REFERENCES.glob("*.md")]
        text = "\n".join(path.read_text(encoding="utf-8") for path in docs).lower()
        scanner_triggers = (
            ("ignore", "previous", "instructions"),
            ("override", "this", "skill"),
        )
        for words in scanner_triggers:
            self.assertNotIn(" ".join(words), text)


if __name__ == "__main__":
    unittest.main()
