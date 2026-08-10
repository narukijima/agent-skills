import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/ai-native-design/cases.json"
SCRIPT = ROOT / "tools/score-behavior-eval.py"
SPEC = importlib.util.spec_from_file_location("score_behavior_eval", SCRIPT)
evaluator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(evaluator)


class AiNativeDesignBehaviorEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(CASES.read_text(encoding="utf-8"))

    def test_eval_fixture_schema_and_required_scenarios(self):
        self.assertEqual(evaluator.validate_cases(self.payload), [])
        ids = {case["id"] for case in self.payload["cases"]}
        self.assertEqual(
            ids,
            {
                "negative-general-landing-page",
                "material-ui-ai-chat",
                "shadcn-ai-sdk-chat",
                "tool-heavy-agent-ui",
                "unknown-community-license",
                "destructive-tool-approval",
                "unrun-build-reporting",
                "malicious-generated-content",
            },
        )

    def test_scorer_requires_semantic_judgments_and_evidence(self):
        result = evaluator.score(
            self.payload["cases"],
            {
                "case_results": [
                    {
                        "id": "negative-general-landing-page",
                        "observed_activation": False,
                        "criteria": [{"id": "no-false-positive", "passed": True, "evidence": "Skill was not loaded."}],
                        "forbidden_behavior_observed": False,
                    },
                    {
                        "id": "unknown-community-license",
                        "observed_activation": True,
                        "criteria": [{"id": "refuse-unknown-license", "passed": False, "evidence": "Agent copied it."}],
                        "forbidden_behavior_observed": True,
                    },
                ]
            },
        )
        self.assertFalse(result["passed"])
        unsafe = next(item for item in result["results"] if item["id"] == "unknown-community-license")
        self.assertIn("criterion failed: refuse-unknown-license", unsafe["failures"])
        self.assertIn("forbidden_behavior_observed must be false", unsafe["failures"])

    def test_cli_validates_cases_without_model_execution(self):
        self.assertEqual(evaluator.main(["--cases", str(CASES)]), 0)


if __name__ == "__main__":
    unittest.main()
