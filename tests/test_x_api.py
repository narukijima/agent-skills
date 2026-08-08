import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPT = Path(__file__).parents[1] / "skills" / "x-api" / "scripts" / "x_api.py"
SPEC = importlib.util.spec_from_file_location("x_api", SCRIPT)
x_api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(x_api)


OAUTH2_ENV = {"X_POSTING_ENABLED": "true", "X_ACCESS_TOKEN": "secret"}


class FakeResponse:
    def __init__(self, status=201, body=b'{"data":{"id":"123"}}'):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def live_args(ledger, extra=()):
    return x_api.build_parser().parse_args(
        ["post", "--live", "--content-id", "c-1", "--text", "hello", "--ledger", str(ledger), *extra]
    )


class XApiTests(unittest.TestCase):
    def test_dry_run_never_requires_a_token(self):
        args = x_api.build_parser().parse_args(["post", "--text", "hello"])
        result = x_api.post_text(args)
        self.assertEqual(result["dry_run"], True)
        self.assertEqual(result["weighted_length"], 5)

    def test_live_post_records_sent_and_refuses_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            with patch.dict(os.environ, OAUTH2_ENV, clear=False), patch.object(x_api, "urlopen", return_value=FakeResponse()):
                result = x_api.post_text(live_args(ledger))
                self.assertEqual(result["post_id"], "123")
                self.assertIn('"status": "sent"', ledger.read_text(encoding="utf-8"))
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(live_args(ledger))

    def test_actual_http_status_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            with patch.dict(os.environ, OAUTH2_ENV, clear=False), patch.object(x_api, "urlopen", return_value=FakeResponse(status=200)):
                x_api.post_text(live_args(ledger))
            record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["http_status"], 200)

    def test_crash_mid_send_leaves_an_unknown_attempt_row(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            with patch.dict(os.environ, OAUTH2_ENV, clear=False):
                with patch.object(x_api, "api_request", side_effect=SystemExit("killed")):
                    with self.assertRaises(SystemExit):
                        x_api.post_text(live_args(ledger))
                lines = ledger.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                record = json.loads(lines[0])
                self.assertEqual(record["status"], "unknown")
                self.assertEqual(record["event"], "attempt")
                # The next run must be gated behind --retry-unknown.
                with self.assertRaises(x_api.ApiFailure) as raised:
                    x_api.post_text(live_args(ledger))
                self.assertIn("retry-unknown", str(raised.exception))

    def test_unknown_result_is_not_retried_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            ledger.write_text(
                json.dumps({"content_id": "c-1", "content_sha256": x_api.content_sha256("hello"), "status": "unknown"}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, OAUTH2_ENV, clear=False):
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(live_args(ledger))

    def test_5xx_is_recorded_unknown_and_gates_the_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            gateway_timeout = HTTPError("https://api.x.com/2/tweets", 504, "Gateway Timeout", {}, io.BytesIO(b"{}"))
            with patch.dict(os.environ, OAUTH2_ENV, clear=False):
                with patch.object(x_api, "urlopen", side_effect=gateway_timeout):
                    with self.assertRaises(x_api.ApiFailure):
                        x_api.post_text(live_args(ledger))
                record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual(record["status"], "unknown")
                self.assertEqual(record["http_status"], 504)
                # A retry without --retry-unknown must be refused.
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(live_args(ledger))
                # An explicit --retry-unknown may retry, once.
                with patch.object(x_api, "urlopen", return_value=FakeResponse()):
                    result = x_api.post_text(live_args(ledger, ["--retry-unknown"]))
                self.assertEqual(result["post_id"], "123")

    def test_4xx_is_recorded_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            forbidden = HTTPError("https://api.x.com/2/tweets", 403, "Forbidden", {}, io.BytesIO(b"{}"))
            with patch.dict(os.environ, OAUTH2_ENV, clear=False), patch.object(x_api, "urlopen", side_effect=forbidden):
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(live_args(ledger))
            record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["status"], "failed")
            # A "failed" outcome may be retried without --retry-unknown.
            with patch.dict(os.environ, OAUTH2_ENV, clear=False), patch.object(x_api, "urlopen", return_value=FakeResponse()):
                result = x_api.post_text(live_args(ledger))
            self.assertEqual(result["post_id"], "123")

    def test_credential_errors_never_reach_the_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            env = {"X_POSTING_ENABLED": "true"}
            with patch.dict(os.environ, env, clear=False):
                for variable in ("X_ACCESS_TOKEN", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN_SECRET"):
                    os.environ.pop(variable, None)
                with self.assertRaises(x_api.ApiFailure):
                    x_api.post_text(live_args(ledger))
            self.assertFalse(ledger.exists())

    def test_partial_oauth1_environment_is_an_error(self):
        env = {"X_API_KEY": "key", "X_ACCESS_TOKEN": "token"}
        with patch.dict(os.environ, env, clear=False):
            for variable in ("X_API_SECRET", "X_ACCESS_TOKEN_SECRET"):
                os.environ.pop(variable, None)
            with self.assertRaises(x_api.ApiFailure) as raised:
                x_api.require_user_credentials()
            self.assertIn("X_API_SECRET", str(raised.exception))

    def test_oauth1_signature_matches_the_documented_example(self):
        # Worked example from docs.x.com "Creating a signature".
        credentials = {
            "X_API_KEY": "xvz1evFS4wEEPTGEFPHBog",
            "X_API_SECRET": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            "X_ACCESS_TOKEN": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
            "X_ACCESS_TOKEN_SECRET": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        }
        header = x_api.oauth1_authorization(
            "POST",
            "https://api.x.com/1.1/statuses/update.json",
            {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
            credentials,
            nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
            timestamp="1318622958",
        )
        self.assertIn('oauth_signature="' + x_api.percent_encode("Ls93hJiZbQ3akF3HF3x1Bz8/zU4=") + '"', header)
        self.assertTrue(header.startswith("OAuth "))

    def test_weighted_length_counts_cjk_double_and_urls_as_23(self):
        self.assertEqual(x_api.weighted_length("test"), 4)
        self.assertEqual(x_api.weighted_length("あ" * 10), 20)
        self.assertEqual(x_api.weighted_length("check https://example.com/x"), 6 + 23)

    def test_weighted_length_does_not_swallow_cjk_after_a_url(self):
        # Japanese text is normally written with no space after a URL.
        self.assertEqual(x_api.weighted_length("https://example.com/a" + "あ" * 130), 23 + 260)
        self.assertEqual(x_api.weighted_length("https://example.com/aです。"), 23 + 6)

    def test_base_url_override_is_limited_to_loopback(self):
        with patch.dict(os.environ, {"X_API_BASE_URL": "https://evil.example"}, clear=False):
            with self.assertRaises(x_api.ApiFailure) as raised:
                x_api.resolve_base_url()
            self.assertIn("loopback", str(raised.exception))
        with patch.dict(os.environ, {"X_API_BASE_URL": "http://127.0.0.1:8943"}, clear=False):
            self.assertEqual(x_api.resolve_base_url(), "http://127.0.0.1:8943")
        with patch.dict(os.environ, {"X_API_BASE_URL": "https://api.x.com/"}, clear=False):
            self.assertEqual(x_api.resolve_base_url(), "https://api.x.com")


if __name__ == "__main__":
    unittest.main()
