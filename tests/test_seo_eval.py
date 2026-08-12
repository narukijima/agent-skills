import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/seo/cases.json"
SCRIPT = ROOT / "tools/score-behavior-eval.py"
SPEC = importlib.util.spec_from_file_location("score_behavior_eval", SCRIPT)
evaluator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(evaluator)


class SeoBehaviorEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(CASES.read_text(encoding="utf-8"))

    def test_eval_schema_and_required_scenarios(self):
        self.assertEqual(evaluator.validate_cases(self.payload), [])
        ids = {case["id"] for case in self.payload["cases"]}
        self.assertEqual(
            ids,
            {
                "negative-paid-ad-optimization",
                "waf-blocks-search-crawler",
                "wrong-cross-canonical",
                "dirty-sitemap-population",
                "organic-traffic-drop",
                "javascript-injected-jsonld",
                "current-ai-crawler-controls",
                "llms-txt-ranking-claim",
                "hundred-thousand-pseo-pages",
                "schema-implementation",
                "post-fix-verification",
                "field-vs-lab-performance",
            },
        )

    def test_scorer_requires_semantic_judgments_and_evidence(self):
        result = evaluator.score(
            self.payload["cases"],
            {
                "case_results": [
                    {
                        "id": "negative-paid-ad-optimization",
                        "observed_activation": False,
                        "criteria": [
                            {
                                "id": "no-false-positive",
                                "passed": True,
                                "evidence": "The SEO Skill was not activated.",
                            }
                        ],
                        "forbidden_behavior_observed": False,
                    },
                    {
                        "id": "llms-txt-ranking-claim",
                        "observed_activation": True,
                        "criteria": [
                            {
                                "id": "four-claim-separation",
                                "passed": False,
                                "evidence": "The response guaranteed a ranking lift.",
                            }
                        ],
                        "forbidden_behavior_observed": True,
                    },
                ]
            },
        )
        self.assertFalse(result["passed"])
        unsafe = next(item for item in result["results"] if item["id"] == "llms-txt-ranking-claim")
        self.assertIn("criterion failed: four-claim-separation", unsafe["failures"])
        self.assertIn("forbidden_behavior_observed must be false", unsafe["failures"])

    def test_cli_validates_cases_without_claiming_behavior_execution(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(evaluator.main(["--cases", str(CASES)]), 0)


if __name__ == "__main__":
    unittest.main()
