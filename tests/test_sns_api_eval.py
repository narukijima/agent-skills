import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/sns-api/cases.json"
SPEC = importlib.util.spec_from_file_location("score_behavior_eval", ROOT / "tools/score-behavior-eval.py")
evaluator = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(evaluator)


class SnsApiEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.payload = json.loads(CASES.read_text())

    def test_schema_and_required_scenarios(self):
        self.assertEqual(evaluator.validate_cases(self.payload), [])
        ids = {case["id"] for case in self.payload["cases"]}
        required = {"explicit-budgeted-read", "guarded-post-flow", "youtube-upload", "instagram-reel", "threads-text", "facebook-page-publish",
                    "cross-post-partial-failure", "unsupported-capability", "negative-implicit-casual-post", "negative-browser-posting-fallback",
                    "unknown-timeout-no-blind-retry", "quote-url-default-rejection", "url-unknown-manual-resolve",
                    "x-approved-url-quote", "x-image-upload", "x-video-upload",
                    "credential-leakage-refusal", "legacy-x-state-migration", "youtube-authenticated-resume",
                    "meta-prepublish-crash-recovery", "facebook-unknown-recovery", "submitted-expired-resume-approval",
                    "instagram-media-fail-fast", "tiktok-planned"}
        self.assertTrue(required.issubset(ids)); self.assertEqual(len(ids), 24)

    def test_explicit_invocation_has_negative_cases(self):
        negatives = [case for case in self.payload["cases"] if not case["expected_activation"]]
        self.assertGreaterEqual(len(negatives), 2); self.assertTrue(all(case["forbidden_behaviors"] for case in negatives))

    def test_cli_validates_without_claiming_model_execution(self):
        with redirect_stdout(io.StringIO()): self.assertEqual(evaluator.main(["--cases", str(CASES)]), 0)


if __name__ == "__main__": unittest.main()
