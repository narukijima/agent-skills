import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "skills" / "x-api" / "scripts" / "x_api.py"
SPEC = importlib.util.spec_from_file_location("x_api", SCRIPT)
x_api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(x_api)


class FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"data":{"id":"123"}}'


class XApiTests(unittest.TestCase):
    def test_dry_run_never_requires_a_token(self):
        args = x_api.build_parser().parse_args(["post", "--text", "hello"])
        self.assertEqual(x_api.post_text(args)["dry_run"], True)

    def test_live_post_records_sent_and_refuses_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            env = {"X_POSTING_ENABLED": "true", "X_ACCESS_TOKEN": "secret"}
            with patch.dict(os.environ, env, clear=False), patch.object(x_api, "urlopen", return_value=FakeResponse()):
                args = x_api.build_parser().parse_args(["post", "--live", "--content-id", "c-1", "--text", "hello", "--ledger", str(ledger)])
                result = x_api.post_text(args)
                self.assertEqual(result["post_id"], "123")
                self.assertIn('"status": "sent"', ledger.read_text(encoding="utf-8"))
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(args)

    def test_unknown_result_is_not_retried_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            ledger.write_text(json.dumps({"content_id": "c-1", "content_sha256": x_api.content_sha256("hello"), "status": "unknown"}) + "\n", encoding="utf-8")
            env = {"X_POSTING_ENABLED": "true", "X_ACCESS_TOKEN": "secret"}
            with patch.dict(os.environ, env, clear=False):
                args = x_api.build_parser().parse_args(["post", "--live", "--content-id", "c-1", "--text", "hello", "--ledger", str(ledger)])
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(args)


if __name__ == "__main__":
    unittest.main()
