import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/sns-algorithm/cases.json"
SPEC = importlib.util.spec_from_file_location(
    "score_behavior_eval", ROOT / "tools/score-behavior-eval.py"
)
evaluator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(evaluator)


class SnsAlgorithmBehaviorEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(CASES.read_text(encoding="utf-8"))

    def test_schema_and_required_scenarios(self):
        self.assertEqual(evaluator.validate_cases(self.payload), [])
        ids = {case["id"] for case in self.payload["cases"]}
        self.assertEqual(
            ids,
            {
                "x-weight-requires-version",
                "instagram-feed-vs-reels",
                "youtube-search-vs-home",
                "tiktok-myth-not-official",
                "threads-does-not-inherit-instagram",
                "underperformance-multiple-causes",
                "shadowban-triage",
                "latest-requires-freshness",
                "primary-beats-third-party",
                "no-sns-api-execution",
                "no-cross-platform-signal-transfer",
                "missing-data-lowers-confidence",
                "negative-copywriting-only",
                "experiment-design-from-mechanism",
            },
        )

    def test_implicit_invocation_has_a_negative_boundary_case(self):
        negatives = [case for case in self.payload["cases"] if not case["expected_activation"]]
        self.assertEqual([case["id"] for case in negatives], ["negative-copywriting-only"])

    def test_scorer_requires_semantic_judgments_and_evidence(self):
        result = evaluator.score(
            self.payload["cases"],
            {
                "case_results": [
                    {
                        "id": "x-weight-requires-version",
                        "observed_activation": True,
                        "criteria": [
                            {"id": "commit-and-path", "passed": False, "evidence": "No commit was cited."},
                            {"id": "prediction-not-counts", "passed": False, "evidence": "Counts were equated."},
                            {"id": "production-limit", "passed": False, "evidence": "No limitation."},
                        ],
                        "forbidden_behavior_observed": True,
                    }
                ]
            },
        )
        self.assertFalse(result["passed"])
        unsafe = next(item for item in result["results"] if item["id"] == "x-weight-requires-version")
        self.assertIn("criterion failed: commit-and-path", unsafe["failures"])
        self.assertIn("forbidden_behavior_observed must be false", unsafe["failures"])

    def test_cli_validates_without_claiming_model_execution(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(evaluator.main(["--cases", str(CASES)]), 0)
        self.assertIn('"behavior_run": false', output.getvalue())


if __name__ == "__main__":
    unittest.main()
