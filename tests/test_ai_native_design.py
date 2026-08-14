import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/ai-native-design"


class AiNativeDesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cls.reference_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((SKILL / "references").glob("*.md"))
        )
        cls.contract = cls.skill_text + "\n" + cls.reference_text

    def test_required_self_contained_files_exist(self):
        required = {
            "SKILL.md",
            "LICENSE.txt",
            "agents/openai.yaml",
            "references/source-strategy.md",
            "references/ai-ui-patterns.md",
            "references/quality-gates.md",
        }
        actual = {
            str(path.relative_to(SKILL))
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertTrue(required.issubset(actual), required - actual)

    def test_skill_reference_links_resolve(self):
        links = re.findall(r"`(references/[^`]+\.md)`", self.skill_text)
        self.assertEqual(
            set(links),
            {
                "references/source-strategy.md",
                "references/ai-ui-patterns.md",
                "references/quality-gates.md",
            },
        )
        for link in links:
            self.assertTrue((SKILL / link).is_file(), link)

    def test_core_source_roles_and_order_are_explicit(self):
        workflow = re.search(
            r"### 3\. 再利用候補を探索する\n(.*?)\n### 4\.",
            self.skill_text,
            re.S,
        ).group(1)
        ordered = [
            "対象Projectの既存component",
            "shadcn/ui",
            "Vercel AI Elements",
            "21st Agent Elements",
            "21st.dev Marketplace",
            "custom implementation",
        ]
        positions = [workflow.index(term) for term in ordered]
        self.assertEqual(positions, sorted(positions))
        for phrase in (
            "shadcn/ui — UI foundation",
            "Vercel AI Elements — AI-native components",
            "21st Agent Elements — tool-heavy AI-native candidate",
            "21st.dev Marketplace — design discovery",
            "Custom implementation — last resort",
        ):
            self.assertIn(phrase, self.reference_text)

    def test_ai_states_cot_license_and_quality_contracts_exist(self):
        for state in (
            "idle", "submitting", "queued", "streaming", "reasoning",
            "tool_requested", "tool_running", "tool_succeeded", "tool_failed",
            "approval_required", "cancelled", "retry", "partial_result",
            "completed", "empty", "error",
        ):
            self.assertIn(f"`{state}`", self.reference_text)
        for requirement in (
            "内部Chain of Thought",
            "license不明",
            "keyboard",
            "reduced motion",
            "long code",
            "TypeScript",
            "production build",
            "dangerouslySetInnerHTML",
            "server-side authorization",
            "idempotency",
            "replay protection",
            "Marketplace Terms",
        ):
            self.assertIn(requirement, self.contract)

    def test_agent_skills_frontmatter_and_bundled_license(self):
        frontmatter = re.match(r"^---\n(.*?)\n---\n", self.skill_text, re.S).group(1)
        top_level = {
            match.group(1)
            for line in frontmatter.splitlines()
            if (match := re.match(r"^([a-z][a-z0-9-]*):", line))
        }
        self.assertEqual(
            top_level,
            {"name", "description", "license", "metadata"},
        )
        self.assertIn('claudagt.version: "0.3.0"', frontmatter)
        self.assertIn('claudagt.status: "active"', frontmatter)
        self.assertTrue((SKILL / "LICENSE.txt").is_file())

    def test_catalog_and_agent_metadata_are_registered(self):
        catalog = (ROOT / "skills/SKILLS.md").read_text(encoding="utf-8")
        metadata = (SKILL / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("[`ai-native-design`](ai-native-design/SKILL.md)", catalog)
        self.assertIn("$ai-native-design", metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)


if __name__ == "__main__":
    unittest.main()
