import importlib.util
import io
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request


SCRIPT = Path(__file__).parents[1] / "skills" / "x-api" / "scripts" / "x_api.py"
SPEC = importlib.util.spec_from_file_location("x_api", SCRIPT)
x_api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(x_api)


SIGNING_KEY = "test-only-manifest-signing-key-32-bytes-minimum"
STATIC_CLIENT_ID = "client-1"
APP_FINGERPRINT = x_api.app_credential_fingerprint("oauth2", STATIC_CLIENT_ID)

LIVE_ENV = {
    "X_POSTING_ENABLED": "true",
    "X_API_WRITE_MAX_CALLS": "3",
    "X_ACCESS_TOKEN": "secret",
    "X_OAUTH2_STATIC_CLIENT_ID": STATIC_CLIENT_ID,
    "X_API_APP_ID": "production-app",
    "X_API_MANIFEST_SIGNING_KEY": SIGNING_KEY,
    "X_API_PROJECT_ID": "project-1",
    "X_API_AGENT_ID": "agent-1",
    "X_API_DAILY_WRITE_CALL_LIMIT": "100",
}
READ_ENV = {
    "X_API_READ_ENABLED": "true", "X_API_READ_MAX_CALLS": "3", "X_ACCESS_TOKEN": "secret",
    "X_OAUTH2_STATIC_CLIENT_ID": STATIC_CLIENT_ID,
    "X_API_PROJECT_ID": "project-1", "X_API_AGENT_ID": "agent-1", "X_API_DAILY_READ_CALL_LIMIT": "100",
}


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), super().get(key, default))


class FakeResponse:
    def __init__(self, status=201, body=b'{"data":{"id":"123"}}', headers=None):
        self.status = status
        self._body = body
        self.headers = FakeHeaders(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def prepare_args(path, text="hello", **overrides):
    values = {
        "content_id": "c-1",
        "expected_user_id": "42",
        "app_id": "production-app",
        "expected_app_fingerprint": APP_FINGERPRINT,
        "approval_id": "approval-1",
        "expires_in": "900",
    }
    values.update({key: str(value) for key, value in overrides.items()})
    argv = [
        "prepare", "--manifest", str(path), "--text", text,
        "--content-id", values["content_id"],
        "--expected-user-id", values["expected_user_id"],
        "--app-id", values["app_id"],
        "--expected-app-fingerprint", values["expected_app_fingerprint"],
        "--approval-id", values["approval_id"],
        "--expires-in", values["expires_in"],
    ]
    return x_api.build_parser().parse_args(argv)


def make_manifest(path, text="hello", **overrides):
    with patch.dict(os.environ, {"X_API_MANIFEST_SIGNING_KEY": SIGNING_KEY}, clear=False):
        return x_api.prepare_manifest(prepare_args(path, text, **overrides))


def load_signed_manifest(path):
    with patch.dict(os.environ, {"X_API_MANIFEST_SIGNING_KEY": SIGNING_KEY}, clear=False):
        return x_api.load_manifest(path)


def resign_manifest(value):
    value["manifest_sha256"] = x_api._manifest_digest(value)
    value["manifest_hmac_sha256"] = x_api._manifest_signature(value, SIGNING_KEY.encode("utf-8"))
    return value


def send_args(path):
    return x_api.build_parser().parse_args(["send", "--manifest", str(path)])


def identity_response(user_id="42"):
    return 200, {"data": {"id": user_id, "username": "example"}}, {"rate_limit": {"remaining": "99"}}


def post_response(post_id="123"):
    return 201, {"data": {"id": post_id}}, {"rate_limit": {"remaining": "98"}}


class XApiTests(unittest.TestCase):
    def test_prepare_normalizes_nfc_and_never_requires_x_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            result = make_manifest(manifest, "cafe\u0301")
            self.assertEqual(result["text"], "caf\u00e9")
            self.assertEqual(result["content_sha256"], x_api.content_sha256("caf\u00e9"))
            self.assertTrue(result["validation"]["valid"])
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)

    def test_weighted_length_handles_emoji_clusters_and_urls(self):
        for value in ("👾", "🙋🏽", "👨‍🎤", "👨‍👩‍👧‍👦", "🇯🇵"):
            self.assertEqual(x_api.weighted_length(value), 2, value)
            self.assertTrue(x_api.validate_post_text(value)["valid"], value)
        self.assertEqual(x_api.weighted_length("https://example.com)."), 25)
        self.assertEqual(x_api.weighted_length("https://example.com/aです。"), 29)
        self.assertEqual(x_api.weighted_length("example.com"), 23)
        self.assertEqual(x_api.weighted_length("mail@example.com"), 16)

    def test_invalid_text_is_rejected_before_manifest_or_api(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            with self.assertRaises(x_api.ApiFailure) as too_long:
                make_manifest(manifest, "あ" * 141)
            self.assertIn("TEXT_TOO_LONG", str(too_long.exception))
            self.assertFalse(manifest.exists())
            with self.assertRaises(x_api.ApiFailure) as cashtags:
                make_manifest(manifest, "$AAA $BBB")
            self.assertIn("TOO_MANY_CASHTAGS", str(cashtags.exception))
            with self.assertRaises(x_api.ApiFailure) as control:
                make_manifest(manifest, "plain\u200dtext")
            self.assertIn("CONTROL_CHARACTER", str(control.exception))

    def test_send_rejects_manifest_tampering_and_expiry_before_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            make_manifest(manifest)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["text"] = "changed"
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with patch.dict(os.environ, LIVE_ENV, clear=False), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure):
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            make_manifest(manifest)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["budget"]["calls"] = ["POST /2/tweets", "GET /2/users/me"]
            value["manifest_sha256"] = x_api._manifest_digest(value)
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with patch.dict(os.environ, LIVE_ENV, clear=False), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure) as unsigned_plan:
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            self.assertIn("approval signature", str(unsigned_plan.exception))
            resign_manifest(value)
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with patch.dict(os.environ, LIVE_ENV, clear=False), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure) as plan:
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            self.assertIn("identity, and send plan", str(plan.exception))
            make_manifest(manifest)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            resign_manifest(value)
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with patch.dict(os.environ, LIVE_ENV, clear=False), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure):
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()

    def test_expected_account_mismatch_stops_before_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            with patch.dict(os.environ, LIVE_ENV, clear=False), \
                    patch.object(x_api, "CANONICAL_LEDGER_PATH", database), \
                    patch.object(x_api, "api_request", return_value=identity_response("99")):
                with self.assertRaises(x_api.ApiFailure) as raised:
                    x_api.send_manifest(send_args(manifest))
            self.assertIn("expected_user_id", str(raised.exception))
            self.assertFalse(database.exists())

    def test_app_binding_and_exact_write_budget_stop_before_api(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            wrong_app = {**LIVE_ENV, "X_API_APP_ID": "other-app"}
            with patch.dict(os.environ, wrong_app, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure):
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            wrong_credential_app = {**LIVE_ENV, "X_OAUTH2_STATIC_CLIENT_ID": "other-client"}
            with patch.dict(os.environ, wrong_credential_app, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure) as fingerprint:
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            self.assertIn("expected_app_fingerprint", str(fingerprint.exception))
            broad_budget = {**LIVE_ENV, "X_API_WRITE_MAX_CALLS": "4"}
            with patch.dict(os.environ, broad_budget, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(x_api, "api_request") as request:
                with self.assertRaises(x_api.ApiFailure) as raised:
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            self.assertIn("exactly 3", str(raised.exception))

    def test_send_records_account_app_and_refuses_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            with patch.dict(os.environ, LIVE_ENV, clear=False), \
                    patch.object(x_api, "CANONICAL_LEDGER_PATH", database), \
                    patch.object(x_api, "api_request", side_effect=[identity_response(), post_response(), identity_response()]):
                result = x_api.send_manifest(send_args(manifest))
                self.assertEqual(result["post_id"], "123")
                with self.assertRaises(x_api.ApiFailure):
                    x_api.send_manifest(send_args(manifest))
            connection = sqlite3.connect(database)
            row = connection.execute("SELECT account_id,app_id,status,attempts,http_status FROM intents").fetchone()
            connection.close()
            self.assertEqual(row, ("42", "production-app", "sent", 1, 201))

    def test_send_reuses_one_captured_credential_for_identity_and_post(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            seen_credentials = []

            def fake_request(_method, path, *_args, **kwargs):
                seen_credentials.append(kwargs.get("user_credentials"))
                if path == "/2/users/me":
                    os.environ["X_ACCESS_TOKEN"] = "switched-after-identity"
                    return identity_response()
                return post_response()

            with patch.dict(os.environ, LIVE_ENV, clear=True), \
                    patch.object(x_api, "CANONICAL_LEDGER_PATH", database), \
                    patch.object(x_api, "api_request", side_effect=fake_request):
                x_api.send_manifest(send_args(manifest))
            self.assertEqual(len(seen_credentials), 2)
            self.assertIs(seen_credentials[0], seen_credentials[1])
            self.assertEqual(seen_credentials[0][1]["X_ACCESS_TOKEN"], "secret")

    def test_daily_write_budget_is_persistent_per_project_and_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(first, "first", content_id="c-first")
            make_manifest(second, "second", content_id="c-second")
            env = {**LIVE_ENV, "X_API_DAILY_WRITE_CALL_LIMIT": "3"}
            with patch.dict(os.environ, env, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(
                x_api, "api_request", side_effect=[identity_response(), post_response(), identity_response(), post_response("456")]
            ) as request:
                x_api.send_manifest(send_args(first))
                with self.assertRaises(x_api.ApiFailure) as raised:
                    x_api.send_manifest(send_args(second))
            self.assertIn("exceeded", str(raised.exception))
            self.assertEqual(request.call_count, 2)

    def test_direct_live_text_and_arbitrary_ledger_options_do_not_exist(self):
        parser = x_api.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["send", "--text", "bypass", "--manifest", "m.json"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["send", "--manifest", "m.json", "--ledger", "/tmp/new.jsonl"])

    def test_crash_after_reservation_leaves_unknown_and_blocks_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            with patch.dict(os.environ, LIVE_ENV, clear=False), patch.object(x_api, "CANONICAL_LEDGER_PATH", database):
                with patch.object(x_api, "api_request", side_effect=[identity_response(), SystemExit("killed")]):
                    with self.assertRaises(SystemExit):
                        x_api.send_manifest(send_args(manifest))
                with patch.object(x_api, "api_request", return_value=identity_response()):
                    with self.assertRaises(x_api.ApiFailure) as raised:
                        x_api.send_manifest(send_args(manifest))
            self.assertIn("reconcile", str(raised.exception))

    def test_any_account_unknown_blocks_a_different_new_post(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.json"
            second_path = Path(directory) / "second.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(first_path, "first", content_id="c-first")
            make_manifest(second_path, "second", content_id="c-second")
            with patch.object(x_api, "CANONICAL_LEDGER_PATH", database):
                x_api.reserve_attempt(load_signed_manifest(first_path))
                with patch.dict(os.environ, LIVE_ENV, clear=True), patch.object(x_api, "api_request", return_value=identity_response()):
                    with self.assertRaises(x_api.ApiFailure) as raised:
                        x_api.send_manifest(send_args(second_path))
            self.assertIn("unresolved unknown intent", str(raised.exception))

    def test_failed_or_confirmed_absent_retry_requires_new_signed_approval(self):
        for terminal_status in ("failed", "confirmed_absent"):
            with self.subTest(status=terminal_status), tempfile.TemporaryDirectory() as directory:
                first_path = Path(directory) / "first.json"
                second_path = Path(directory) / "second.json"
                database = Path(directory) / "posts.sqlite3"
                make_manifest(first_path)
                make_manifest(second_path, approval_id="approval-2")
                first = load_signed_manifest(first_path)
                second = load_signed_manifest(second_path)
                with patch.object(x_api, "CANONICAL_LEDGER_PATH", database):
                    intent_id = x_api.reserve_attempt(first)
                    x_api.record_result(intent_id, terminal_status)
                    with self.assertRaises(x_api.ApiFailure) as reused:
                        x_api.reserve_attempt(first)
                    self.assertIn("new signed approval_id", str(reused.exception))
                    self.assertEqual(x_api.reserve_attempt(second), intent_id)

    def test_5xx_is_unknown_4xx_failed_and_429_refunds_attempt(self):
        cases = [(302, "unknown", 1), (504, "unknown", 1), (403, "failed", 1), (429, "rate_limited", 0)]
        for code, expected_status, expected_attempts in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / "approved.json"
                database = Path(directory) / "posts.sqlite3"
                make_manifest(manifest)
                failure = x_api.ApiFailure("failure", status=code, retry_after="9", rate_limit_reset="10")
                with patch.dict(os.environ, LIVE_ENV, clear=False), \
                        patch.object(x_api, "CANONICAL_LEDGER_PATH", database), \
                        patch.object(x_api, "api_request", side_effect=[identity_response(), failure]):
                    with self.assertRaises(x_api.ApiFailure):
                        x_api.send_manifest(send_args(manifest))
                connection = sqlite3.connect(database)
                row = connection.execute("SELECT status,attempts,http_status FROM intents").fetchone()
                connection.close()
                self.assertEqual(row, (expected_status, expected_attempts, code))

    def test_concurrent_sends_have_one_external_post(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            post_calls = []
            lock = threading.Lock()

            def fake_request(method, path, *_args, **_kwargs):
                if path == "/2/users/me":
                    return identity_response()
                with lock:
                    post_calls.append(1)
                time.sleep(0.1)
                return post_response()

            outcomes = []
            def run():
                try:
                    x_api.send_manifest(send_args(manifest))
                    outcomes.append("sent")
                except x_api.ApiFailure:
                    outcomes.append("refused")

            with patch.dict(os.environ, LIVE_ENV, clear=False), \
                    patch.object(x_api, "CANONICAL_LEDGER_PATH", database), \
                    patch.object(x_api, "api_request", side_effect=fake_request):
                threads = [threading.Thread(target=run) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(post_calls, [1])
            self.assertEqual(sorted(outcomes), ["refused", "sent"])

    def test_redirect_is_rejected_without_forwarding_authorization(self):
        request = Request("https://api.x.com/2/users/me")
        request.add_header("Authorization", "Bearer SECRET")
        redirected = x_api.RejectRedirects().redirect_request(
            request, None, 302, "Found", {"Location": "https://evil.example/steal"}, "https://evil.example/steal"
        )
        self.assertIsNone(redirected)
        with self.assertRaises(HTTPError) as raised:
            x_api.RejectRedirects().http_error_302(request, io.BytesIO(b""), 302, "Found", {})
        raised.exception.close()

    def test_fetch_returns_partial_and_success_rate_limit_metadata(self):
        payload = {"data": [{"id": "1"}], "errors": [{"detail": "missing 2"}], "meta": {"result_count": 1}}
        with patch.object(x_api, "api_request", return_value=(200, payload, {"rate_limit": {"remaining": "7"}})):
            result = x_api.fetch("GET", "/2/tweets", "app")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["_meta"]["rate_limit"]["remaining"], "7")

    def test_main_returns_nonzero_for_partial_response(self):
        partial = {"status": "partial", "data": [{"id": "1"}], "errors": [{"detail": "missing"}]}
        with patch.object(x_api, "dispatch", return_value=partial), patch.dict(os.environ, READ_ENV, clear=False):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(x_api.main(["user", "--username", "example"]), 2)

    def test_read_requires_explicit_budget_and_max_results_is_never_clamped(self):
        args = x_api.build_parser().parse_args(["posts", "--user-id", "42", "--max-results", "1"])
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(x_api.ApiFailure):
                x_api.dispatch(args)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "posts.sqlite3"
            env = {
                "X_API_READ_ENABLED": "true", "X_API_READ_MAX_CALLS": "1", "X_BEARER_TOKEN": "secret",
                "X_API_PROJECT_ID": "project-1", "X_API_AGENT_ID": "agent-1", "X_API_DAILY_READ_CALL_LIMIT": "10",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database):
                with self.assertRaises(x_api.ApiFailure) as raised:
                    x_api.dispatch(args)
            self.assertFalse(database.with_name("x-usage.sqlite3").exists())
        self.assertIn("refusing to increase or clamp", str(raised.exception))

    def test_user_context_read_budget_includes_conditional_oauth_refresh(self):
        args = x_api.build_parser().parse_args(["user", "--username", "example"])
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "posts.sqlite3"
            env = {
                "X_API_READ_ENABLED": "true", "X_API_READ_MAX_CALLS": "1", "X_ACCESS_TOKEN": "secret",
                "X_API_PROJECT_ID": "project-1", "X_API_AGENT_ID": "agent-1", "X_API_DAILY_READ_CALL_LIMIT": "10",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(x_api, "fetch") as fetch:
                with self.assertRaises(x_api.ApiFailure) as raised:
                    x_api.dispatch(args)
                fetch.assert_not_called()
            self.assertIn("at least 2", str(raised.exception))

            env["X_API_READ_MAX_CALLS"] = "2"
            with patch.dict(os.environ, env, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(
                x_api, "fetch", return_value={"status": "success", "_meta": {}}
            ):
                result = x_api.dispatch(args)
            self.assertEqual(result["_meta"]["budget"]["reserved_calls"], 2)

    def test_reconcile_confirms_sent_absent_or_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "posts.sqlite3"
            manifest_path = Path(directory) / "approved.json"
            make_manifest(manifest_path)
            manifest = load_signed_manifest(manifest_path)
            with patch.object(x_api, "CANONICAL_LEDGER_PATH", database):
                intent_id = x_api.reserve_attempt(manifest)
                attempted = datetime.now(timezone.utc)
                args = x_api.build_parser().parse_args(["reconcile", "--content-id", "c-1", "--expected-user-id", "42"])
                sent_payload = {"data": [{"id": "777", "text": "hello", "created_at": attempted.isoformat()}]}
                with patch.dict(os.environ, READ_ENV, clear=False), patch.object(
                    x_api, "api_request", side_effect=[identity_response(), (200, sent_payload, {})]
                ):
                    result = x_api.reconcile(args)
                self.assertEqual(result["status"], "confirmed_success")
                self.assertEqual(result["post_id"], "777")

                # A separate unknown intent whose returned timeline brackets the attempt is confirmed absent.
                second_path = Path(directory) / "second.json"
                make_manifest(second_path, "different", content_id="c-2")
                second = load_signed_manifest(second_path)
                second_id = x_api.reserve_attempt(second)
                before = (attempted - timedelta(minutes=1)).isoformat()
                after = (attempted + timedelta(minutes=1)).isoformat()
                absent_payload = {"data": [{"id": "1", "text": "other", "created_at": before}, {"id": "2", "text": "other2", "created_at": after}]}
                args2 = x_api.build_parser().parse_args(["reconcile", "--content-id", "c-2", "--expected-user-id", "42"])
                with patch.dict(os.environ, READ_ENV, clear=False), patch.object(
                    x_api, "api_request", side_effect=[identity_response(), (200, absent_payload, {})]
                ):
                    result = x_api.reconcile(args2)
                self.assertEqual(result["status"], "confirmed_absent")

                third_path = Path(directory) / "third.json"
                make_manifest(third_path, "third", content_id="c-3")
                x_api.reserve_attempt(load_signed_manifest(third_path))
                args3 = x_api.build_parser().parse_args(["reconcile", "--content-id", "c-3", "--expected-user-id", "42"])
                with patch.dict(os.environ, READ_ENV, clear=False), patch.object(
                    x_api, "api_request", side_effect=[identity_response(), (200, {"data": []}, {})]
                ):
                    result = x_api.reconcile(args3)
                self.assertEqual(result["status"], "unresolved")
                self.assertGreater(intent_id, 0)
                self.assertGreater(second_id, 0)

    def test_reconcile_ignores_old_same_text_and_keeps_url_absence_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "posts.sqlite3"
            old_path = Path(directory) / "old.json"
            url_path = Path(directory) / "url.json"
            make_manifest(old_path)
            make_manifest(url_path, "see https://example.com", content_id="c-url")
            args = x_api.build_parser().parse_args(["reconcile", "--content-id", "c-1", "--expected-user-id", "42"])
            with patch.object(x_api, "CANONICAL_LEDGER_PATH", database):
                old_id = x_api.reserve_attempt(load_signed_manifest(old_path))
                attempted = datetime.now(timezone.utc)
                old_payload = {"data": [
                    {"id": "old", "text": "hello", "created_at": (attempted - timedelta(days=1)).isoformat()},
                    {"id": "before", "text": "other", "created_at": (attempted - timedelta(minutes=1)).isoformat()},
                    {"id": "after", "text": "other2", "created_at": (attempted + timedelta(minutes=1)).isoformat()},
                ]}
                with patch.dict(os.environ, READ_ENV, clear=False), patch.object(
                    x_api, "api_request", side_effect=[identity_response(), (200, old_payload, {})]
                ):
                    old_result = x_api.reconcile(args)
                self.assertEqual(old_result["status"], "confirmed_absent")

                x_api.reserve_attempt(load_signed_manifest(url_path))
                url_args = x_api.build_parser().parse_args(["reconcile", "--content-id", "c-url", "--expected-user-id", "42"])
                url_payload = {"data": [
                    {"id": "before", "text": "other", "created_at": (attempted - timedelta(minutes=1)).isoformat()},
                    {"id": "after", "text": "other2", "created_at": (attempted + timedelta(minutes=1)).isoformat()},
                ]}
                with patch.dict(os.environ, READ_ENV, clear=False), patch.object(
                    x_api, "api_request", side_effect=[identity_response(), (200, url_payload, {})]
                ):
                    url_result = x_api.reconcile(url_args)
                self.assertEqual(url_result["status"], "unresolved")
                self.assertTrue(url_result["_meta"]["reconciliation"]["contains_url"])
                self.assertGreater(old_id, 0)

    def test_partial_oauth1_environment_is_an_error(self):
        with patch.dict(os.environ, {"X_API_KEY": "key", "X_ACCESS_TOKEN": "token"}, clear=True):
            with self.assertRaises(x_api.ApiFailure) as raised:
                x_api.require_user_credentials()
        self.assertIn("X_API_SECRET", str(raised.exception))

    def test_oauth1_signature_matches_documented_example(self):
        credentials = {
            "X_API_KEY": "xvz1evFS4wEEPTGEFPHBog",
            "X_API_SECRET": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            "X_ACCESS_TOKEN": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
            "X_ACCESS_TOKEN_SECRET": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        }
        header = x_api.oauth1_authorization(
            "POST", "https://api.x.com/1.1/statuses/update.json",
            {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
            credentials, nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958",
        )
        self.assertIn('oauth_signature="' + x_api.percent_encode("Ls93hJiZbQ3akF3HF3x1Bz8/zU4=") + '"', header)

    def test_oauth2_refresh_rotates_private_store_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt1", "store": str(store)}
            response = FakeResponse(status=200, body=json.dumps({"access_token": "at1", "refresh_token": "rt2", "expires_in": 7200}).encode())
            with patch.object(x_api, "urlopen", return_value=response) as request:
                self.assertEqual(x_api.oauth2_refreshed_access_token(config), "at1")
                self.assertEqual(x_api.oauth2_refreshed_access_token(config), "at1")
                self.assertEqual(request.call_count, 1)
            stored = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(stored["refresh_token"], "rt2")
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)

    def test_oauth2_refresh_lock_allows_only_one_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt1", "store": str(store)}
            calls = []

            def slow_refresh(*_args, **_kwargs):
                calls.append(1)
                time.sleep(0.1)
                return FakeResponse(status=200, body=json.dumps({"access_token": "at1", "refresh_token": "rt2", "expires_in": 7200}).encode())

            outcomes = []
            with patch.object(x_api, "urlopen", side_effect=slow_refresh):
                threads = [threading.Thread(target=lambda: outcomes.append(x_api.oauth2_refreshed_access_token(config))) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(calls, [1])
            self.assertEqual(outcomes, ["at1", "at1"])

    def test_oauth2_store_failure_after_rotation_requires_reauthorization(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            marker = store.with_name(store.name + ".refresh-pending")
            config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt1", "store": str(store)}
            response = FakeResponse(status=200, body=json.dumps({"access_token": "at1", "refresh_token": "rt2", "expires_in": 7200}).encode())
            with patch.object(x_api, "urlopen", return_value=response), patch.object(x_api, "write_private_json", side_effect=OSError("disk full")):
                with self.assertRaises(x_api.CredentialRotationFailure) as raised:
                    x_api.oauth2_refreshed_access_token(config)
            self.assertEqual(raised.exception.recovery_marker, str(marker))
            marker_text = marker.read_text(encoding="utf-8")
            for secret in ("rt1", "rt2", "at1"):
                self.assertNotIn(secret, marker_text)

    def test_oauth2_rejected_refresh_clears_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            marker = store.with_name(store.name + ".refresh-pending")
            config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt1", "store": str(store)}
            rejected = HTTPError("https://api.x.com/2/oauth2/token", 400, "Bad Request", {}, io.BytesIO(b"{}"))
            with patch.object(x_api, "urlopen", side_effect=rejected):
                with self.assertRaises(x_api.ApiFailure):
                    x_api.oauth2_refreshed_access_token(config)
            rejected.close()
            self.assertFalse(marker.exists())

    def test_oauth2_committed_store_recovers_stale_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            marker = store.with_name(store.name + ".refresh-pending")
            config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt1", "store": str(store)}
            x_api.write_private_json(store, {
                "access_token": "at1", "access_token_expires_at": time.time() + 3600,
                "refresh_token": "rt2", "last_rotation_id": "rotation-1",
            })
            x_api.write_refresh_marker(marker, {
                "schema_version": 1, "state": "refresh_pending", "rotation_id": "rotation-1", "started_at": x_api.utc_now(),
            })
            with patch.object(x_api, "urlopen") as request:
                self.assertEqual(x_api.oauth2_refreshed_access_token(config), "at1")
                request.assert_not_called()
            self.assertFalse(marker.exists())

    def test_oauth2_unknown_rotation_requires_reauthorization(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            marker = store.with_name(store.name + ".refresh-pending")
            config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt1", "store": str(store)}
            failure = HTTPError("https://api.x.com/2/oauth2/token", 503, "Unavailable", {}, io.BytesIO(b"{}"))
            with patch.object(x_api, "urlopen", side_effect=failure):
                with self.assertRaises(x_api.CredentialRotationFailure):
                    x_api.oauth2_refreshed_access_token(config)
            failure.close()
            self.assertTrue(marker.exists())
            self.assertNotIn("rt1", marker.read_text(encoding="utf-8"))

    def test_missing_credentials_never_create_post_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "approved.json"
            database = Path(directory) / "posts.sqlite3"
            make_manifest(manifest)
            env = {
                "X_POSTING_ENABLED": "true", "X_API_WRITE_MAX_CALLS": "3", "X_API_APP_ID": "production-app",
                "X_API_MANIFEST_SIGNING_KEY": SIGNING_KEY,
                "X_API_PROJECT_ID": "project-1", "X_API_AGENT_ID": "agent-1", "X_API_DAILY_WRITE_CALL_LIMIT": "10",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(x_api, "CANONICAL_LEDGER_PATH", database), patch.object(x_api, "urlopen") as request:
                with self.assertRaises(x_api.ApiFailure):
                    x_api.send_manifest(send_args(manifest))
                request.assert_not_called()
            self.assertFalse(database.exists())

    def test_user_agent_tracks_skill_version_and_base_url_is_loopback_only(self):
        text = (SCRIPT.parents[1] / "SKILL.md").read_text(encoding="utf-8")
        version = re.search(r'^\s+claudagt\.version:\s*"([^"]+)"', text, re.M).group(1)
        self.assertEqual(x_api.USER_AGENT, "agent-skills-x-api/" + version)
        with patch.dict(os.environ, {"X_API_BASE_URL": "https://evil.example"}, clear=False):
            with self.assertRaises(x_api.ApiFailure):
                x_api.resolve_base_url()
        with patch.dict(os.environ, {"X_API_BASE_URL": "http://127.0.0.1:9999"}, clear=True):
            with self.assertRaises(x_api.ApiFailure):
                x_api.resolve_base_url()
        with patch.dict(os.environ, {
            "X_API_BASE_URL": "http://127.0.0.1:9999", "X_API_TEST_MODE": "true",
        }, clear=True):
            self.assertEqual(x_api.resolve_base_url(), "http://127.0.0.1:9999")
        with patch.dict(os.environ, {
            "X_API_BASE_URL": "http://127.0.0.1:9999", "X_API_TEST_MODE": "true", "X_POSTING_ENABLED": "true",
        }, clear=True):
            with self.assertRaises(x_api.ApiFailure):
                x_api.resolve_base_url()

    def test_skill_v05_contract_and_metadata_are_consistent(self):
        skill = (SCRIPT.parents[1] / "SKILL.md").read_text(encoding="utf-8")
        metadata = (SCRIPT.parents[1] / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('claudagt.version: "0.5.0"', skill)
        self.assertIn("license: MIT. See LICENSE.txt", skill)
        self.assertTrue((SCRIPT.parents[1] / "LICENSE.txt").is_file())
        for term in ("prepare", "send", "reconcile", "expected_user_id", "x-posts.sqlite3"):
            self.assertIn(term, skill)
        self.assertIn("allow_implicit_invocation: false", metadata)
        parser = x_api.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertTrue({"prepare", "send", "reconcile", "usage"}.issubset(choices))


if __name__ == "__main__":
    unittest.main()
