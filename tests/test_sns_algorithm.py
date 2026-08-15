import copy
import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/sns-algorithm"
REGISTRY_PATH = SKILL / "references/source-registry.json"
SPEC = importlib.util.spec_from_file_location(
    "sns_algorithm_registry", SKILL / "scripts/validate_registry.py"
)
registry_validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(registry_validator)


class SnsAlgorithmContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_required_self_contained_files_exist(self):
        required = {
            "SKILL.md",
            "LICENSE.txt",
            "agents/openai.yaml",
            "references/methodology.md",
            "references/evidence-policy.md",
            "references/analysis-framework.md",
            "references/platform-matrix.md",
            "references/source-registry.json",
            "references/platforms/x.md",
            "references/platforms/youtube.md",
            "references/platforms/facebook.md",
            "references/platforms/instagram.md",
            "references/platforms/threads.md",
            "references/platforms/tiktok.md",
            "scripts/validate_registry.py",
        }
        actual = {str(path.relative_to(SKILL)) for path in SKILL.rglob("*") if path.is_file()}
        self.assertTrue(required.issubset(actual), required - actual)

    def test_all_markdown_references_are_progressively_linked(self):
        links = set(re.findall(r"`(references/[^`]+\.md)`", self.skill_text))
        files = {str(path.relative_to(SKILL)) for path in (SKILL / "references").rglob("*.md")}
        self.assertEqual(links, files)

    def test_frontmatter_catalog_and_agent_metadata(self):
        frontmatter = re.match(r"^---\n(.*?)\n---\n", self.skill_text, re.S).group(1)
        self.assertIn('claudagt.version: "0.1.0"', frontmatter)
        self.assertIn("[`sns-algorithm`](sns-algorithm/SKILL.md)", (ROOT / "skills/SKILLS.md").read_text())
        metadata = (SKILL / "agents/openai.yaml").read_text()
        self.assertIn("$sns-algorithm", metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)

    def test_registry_schema_and_all_platforms(self):
        self.assertEqual(registry_validator.validate_registry(self.registry, SKILL), [])
        self.assertEqual(set(self.registry["platforms"]), registry_validator.PLATFORMS)
        self.assertGreaterEqual(len(self.registry["sources"]), 20)
        self.assertGreaterEqual(len(self.registry["claims"]), 30)

    def test_registry_rejects_unpinned_code_claim(self):
        payload = copy.deepcopy(self.registry)
        code_claim = next(c for c in payload["claims"] if c["evidence_class"] == "confirmed_code")
        code_claim["version_commit"] = None
        errors = registry_validator.validate_registry(payload, SKILL)
        self.assertTrue(any("confirmed_code requires a full commit SHA" in error for error in errors))

    def test_x_snapshot_is_pinned_and_count_equivalence_is_forbidden(self):
        x_text = (SKILL / "references/platforms/x.md").read_text()
        self.assertIn("c65aa179db7bdd61e2c2821eac87f208a105c053", x_text)
        self.assertIn("do **not** mean one report cancels 468 likes", x_text)
        claim = next(c for c in self.registry["claims"] if c["id"] == "x-foryou-weight-snapshot")
        self.assertEqual(claim["version_commit"], "c65aa179db7bdd61e2c2821eac87f208a105c053")
        self.assertEqual(
            claim["code_paths"],
            ["home-mixer/params/param.rs", "home-mixer/scorers/ranking_scorer.rs"],
        )

    def test_publisher_import_preserves_registry_and_validator(self):
        from tests.test_import_skill import temporary_source_repository

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agent-directory"
            source, importer = temporary_source_repository(directory, "sns-algorithm", "fixture description")
            completed = subprocess.run(
                ["/bin/bash", str(importer), "sns-algorithm", "--target", str(target)],
                cwd=source,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            imported = target / "skills/sns-algorithm"
            self.assertTrue((imported / "references/source-registry.json").is_file())
            self.assertTrue((imported / "scripts/validate_registry.py").is_file())
            upstream = (imported / "agents/upstream.yaml").read_text()
            self.assertIn('source_version: "0.1.0"', upstream)


if __name__ == "__main__":
    unittest.main()
