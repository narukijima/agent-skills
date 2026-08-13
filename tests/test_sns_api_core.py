import io
import os
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from tests.sns_api_helpers import base_env, core, credentials, make_manifest
from sns_api_lib import http
from sns_api_lib.ledger import get_intent


class SnsApiCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        core._WORKSPACE = (Path(self.temp.name), "test")

    def tearDown(self):
        core._WORKSPACE = None
        self.temp.cleanup()

    def _send(self, manifest_path, *, identity="42", publish=None, env=None):
        provider = core.provider("x")
        publish = publish or (lambda *_: {"status": "published", "provider_id": "123", "provider_status": "published", "http_status": 201})
        with patch.dict(os.environ, env or base_env(), clear=True), \
                patch.object(provider, "credentials", return_value=credentials()), \
                patch.object(provider, "identity", return_value={"id": identity, "account_type": "user"}), \
                patch.object(provider, "publish", side_effect=publish):
            return core.send(manifest_path)

    def test_expected_account_mismatch_has_no_ledger_attempt(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path)
        with self.assertRaises(core.ApiFailure) as raised: self._send(path, identity="99")
        self.assertEqual(raised.exception.code, "ACCOUNT_MISMATCH")
        self.assertFalse((Path(self.temp.name) / "state/sns-api/ledger.sqlite3").exists())

    def test_app_mismatch_stops_before_credentials(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path)
        provider = core.provider("x")
        with patch.dict(os.environ, base_env(SNS_X_APP_ID="other"), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as raised: core.send(path)
        self.assertEqual(raised.exception.code, "APP_MISMATCH"); called.assert_not_called()

    def test_credential_fingerprint_mismatch_stops_before_identity(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path)
        provider = core.provider("x")
        wrong = credentials(); wrong = type(wrong)(wrong.auth_mode, wrong.token, wrong.public_id, "0" * 64)
        with patch.dict(os.environ, base_env(), clear=True), patch.object(provider, "credentials", return_value=wrong), patch.object(provider, "identity") as identity:
            with self.assertRaises(core.ApiFailure) as raised: core.send(path)
        self.assertEqual(raised.exception.code, "CREDENTIAL_MISMATCH"); identity.assert_not_called()

    def test_missing_credentials_never_create_publish_ledger(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); provider = core.provider("x")
        env = base_env()
        with patch.dict(os.environ, env, clear=True), patch.object(provider, "credentials", side_effect=core.ApiFailure("missing", code="MISSING_CREDENTIAL")):
            with self.assertRaises(core.ApiFailure): core.send(path)
        self.assertFalse((Path(self.temp.name) / "state/sns-api/ledger.sqlite3").exists())

    def test_unsafe_legacy_x_state_blocks_before_budget_or_credentials(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path)
        legacy = Path(self.temp.name) / "state/x-api/x-posts.sqlite3"
        legacy.parent.mkdir(parents=True); legacy.write_bytes(b"not-a-sqlite-ledger")
        provider = core.provider("x")
        with patch.dict(os.environ, base_env(), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as raised: core.send(path)
        self.assertEqual(raised.exception.code, "LEGACY_X_STATE_UNSAFE")
        called.assert_not_called()
        self.assertFalse((Path(self.temp.name) / "state/sns-api/usage.sqlite3").exists())

    def test_one_credential_snapshot_is_reused_for_identity_and_publish(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); provider = core.provider("x")
        snapshot = credentials(); seen = []
        def identity(value): seen.append(value); return {"id": "42", "account_type": "user"}
        def publish(value, *_args): seen.append(value); return {"status": "published", "provider_id": "123"}
        with patch.dict(os.environ, base_env(), clear=True), patch.object(provider, "credentials", return_value=snapshot) as resolved, \
                patch.object(provider, "identity", side_effect=identity), patch.object(provider, "publish", side_effect=publish):
            core.send(path)
        resolved.assert_called_once(); self.assertEqual(seen, [snapshot, snapshot])

    def test_duplicate_and_blind_retry_are_refused(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path)
        self._send(path)
        with self.assertRaises(core.ApiFailure) as duplicate: self._send(path)
        self.assertEqual(duplicate.exception.code, "DUPLICATE")
        second = Path(self.temp.name) / "unknown.json"; make_manifest(second, content_id="c2", payload={"text": "two"})
        with self.assertRaises(SystemExit): self._send(second, publish=lambda *_: (_ for _ in ()).throw(SystemExit("crash")))
        with self.assertRaises(core.ApiFailure) as blind: self._send(second)
        self.assertEqual(blind.exception.code, "BLIND_RETRY_REFUSED")

    def test_unknown_blocks_other_content_for_same_account(self):
        first = Path(self.temp.name) / "first.json"; second = Path(self.temp.name) / "second.json"
        make_manifest(first); make_manifest(second, content_id="c2", payload={"text": "two"})
        with self.assertRaises(SystemExit): self._send(first, publish=lambda *_: (_ for _ in ()).throw(SystemExit()))
        with self.assertRaises(core.ApiFailure) as blocked: self._send(second)
        self.assertEqual(blocked.exception.code, "ACCOUNT_BLOCKED")

    def test_timeout_and_5xx_become_unknown_while_4xx_becomes_failed(self):
        for content, failure, expected in (
            ("timeout", core.ApiFailure("timeout", outcome="unknown"), "unknown"),
            ("server", core.ApiFailure("server", status=503), "unknown"),
            ("client", core.ApiFailure("client", status=400), "failed"),
        ):
            with self.subTest(content=content):
                path = Path(self.temp.name) / (content + ".json")
                make_manifest(path, content_id=content, payload={"text": content}, approval_id=content)
                with self.assertRaises(core.ApiFailure): self._send(path, publish=lambda *_args, f=failure: (_ for _ in ()).throw(f))
                row = get_intent("x", "42", content); self.assertEqual(row["status"], expected)
                if expected == "unknown":
                    from sns_api_lib.ledger import record_result
                    record_result(row["id"], "confirmed_absent")

    def test_provider_confirmed_async_failure_stays_failed_without_http_status(self):
        path = Path(self.temp.name) / "async-failed.json"; make_manifest(path)
        failure = core.ApiFailure("container failed", code="PROVIDER_ASYNC_FAILED", outcome="failed")
        with self.assertRaises(core.ApiFailure): self._send(path, publish=lambda *_: (_ for _ in ()).throw(failure))
        self.assertEqual(get_intent("x", "42", "content-1")["status"], "failed")

    def test_429_refunds_attempt_and_same_approval_can_retry(self):
        path = Path(self.temp.name) / "rate.json"; make_manifest(path)
        failure = core.ApiFailure("rate", status=429, outcome="rate_limited")
        with self.assertRaises(core.ApiFailure): self._send(path, publish=lambda *_: (_ for _ in ()).throw(failure))
        self.assertEqual(get_intent("x", "42", "content-1")["attempts"], 0)
        result = self._send(path); self.assertEqual(result["status"], "published")

    def test_failed_retry_requires_new_signed_approval(self):
        first = Path(self.temp.name) / "first.json"; make_manifest(first)
        failure = core.ApiFailure("bad", status=400)
        with self.assertRaises(core.ApiFailure): self._send(first, publish=lambda *_: (_ for _ in ()).throw(failure))
        with self.assertRaises(core.ApiFailure) as reused: self._send(first)
        self.assertEqual(reused.exception.code, "NEW_APPROVAL_REQUIRED")
        second = Path(self.temp.name) / "second.json"; make_manifest(second, approval_id="approval-2")
        self.assertEqual(self._send(second)["status"], "published")

    def test_concurrent_send_dispatches_one_external_write(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); calls = []; lock = threading.Lock(); outcomes = []
        provider = core.provider("x")
        def publish(*_):
            with lock: calls.append(1)
            time.sleep(.1); return {"status": "published", "provider_id": "1"}
        def run():
            try: core.send(path); outcomes.append("sent")
            except core.ApiFailure: outcomes.append("refused")
        with patch.dict(os.environ, base_env(), clear=True), \
                patch.object(provider, "credentials", return_value=credentials()), \
                patch.object(provider, "identity", return_value={"id": "42", "account_type": "user"}), \
                patch.object(provider, "publish", side_effect=publish):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for item in threads: item.start()
            for item in threads: item.join()
        self.assertEqual(calls, [1]); self.assertEqual(sorted(outcomes), ["refused", "sent"])

    def test_provider_registry_has_supported_and_planned_boundaries(self):
        all_caps = core.capabilities(); by = {item["platform"]: item for item in all_caps["platforms"]}
        self.assertEqual(by["tiktok"]["status"], "planned"); self.assertFalse(by["tiktok"]["runtime_supported"])
        for name in ("x", "youtube", "facebook", "instagram", "threads"): self.assertEqual(by[name]["status"], "supported")
        with self.assertRaises(core.ApiFailure): core.provider("tiktok")

    def test_unsupported_provider_and_capability_fail_closed(self):
        with self.assertRaises(core.ApiFailure) as provider: core.provider("mastodon")
        self.assertEqual(provider.exception.code, "UNSUPPORTED_PROVIDER")
        with self.assertRaises(core.ApiFailure) as capability: core.provider("x").require_capability("publish.image")
        self.assertEqual(capability.exception.code, "UNSUPPORTED_CAPABILITY")

    def test_budget_plan_mismatch_and_daily_exhaustion_stop_before_credentials(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); provider = core.provider("x")
        with patch.dict(os.environ, base_env(SNS_API_WRITE_MAX_CALLS="4"), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as mismatch: core.send(path)
        self.assertEqual(mismatch.exception.code, "BUDGET_EXHAUSTED"); called.assert_not_called()
        with patch.dict(os.environ, base_env(SNS_API_DAILY_WRITE_CALL_LIMIT="2"), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as daily: core.send(path)
        self.assertEqual(daily.exception.code, "BUDGET_EXHAUSTED"); called.assert_not_called()

    def test_daily_budget_is_persistent_per_project_agent_and_platform(self):
        first = Path(self.temp.name) / "first.json"; second = Path(self.temp.name) / "second.json"
        make_manifest(first); make_manifest(second, content_id="c2", approval_id="a2", payload={"text": "two"})
        env = base_env(SNS_API_DAILY_WRITE_CALL_LIMIT="3")
        self._send(first, env=env)
        provider = core.provider("x")
        with patch.dict(os.environ, env, clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as exhausted: core.send(second)
        self.assertEqual(exhausted.exception.code, "BUDGET_EXHAUSTED"); called.assert_not_called()

    def test_partial_cli_response_is_machine_readable_and_nonzero(self):
        import sns_api
        output = io.StringIO()
        partial = core.envelope("x", "post.lookup", status_value="partial", data=[{"id": "1"}], errors=[{"code": "missing"}])
        with patch.object(sns_api, "dispatch", return_value=partial), patch("sys.stdout", output):
            code = sns_api.main(["capabilities"])
        self.assertEqual(code, 2); self.assertEqual(json.loads(output.getvalue())["status"], "partial")

    def test_unexpected_cli_failure_does_not_echo_exception_or_environment_secret(self):
        import sns_api
        error = io.StringIO()
        with patch.dict(os.environ, {"SNS_X_ACCESS_TOKEN": "never-emit-this-token"}, clear=True), \
                patch.object(sns_api, "dispatch", side_effect=RuntimeError("never-emit-this-token")), patch("sys.stderr", error):
            code = sns_api.main(["capabilities"])
        value = json.loads(error.getvalue())
        self.assertEqual(code, 1); self.assertEqual(value["errors"][0]["code"], "INTERNAL_ERROR")
        self.assertNotIn("never-emit-this-token", error.getvalue())

    def test_runtime_capabilities_match_documented_matrix(self):
        root = Path(__file__).parents[1] / "skills/sns-api/references"
        matrix = (root / "capability-matrix.md").read_text().lower()
        for item in core.capabilities()["platforms"]:
            self.assertIn(f'| {item["platform"]} | {item["status"]} |', matrix)
            if item.get("api_version"):
                self.assertIn(str(item["api_version"]).lower(), (root / "providers" / (item["platform"] + ".md")).read_text().lower())
            if item["status"] != "supported": continue
            for capability in item["capabilities"]: self.assertIn(capability, matrix)

    def test_redirect_and_non_allowlisted_host_are_rejected(self):
        request = Request("https://api.x.com/2/users/me"); request.add_header("Authorization", "Bearer secret")
        self.assertIsNone(http.RejectRedirects().redirect_request(request, None, 302, "Found", {}, "https://evil.test"))
        with self.assertRaises(HTTPError) as raised: http.RejectRedirects().http_error_302(request, io.BytesIO(), 302, "Found", {})
        raised.exception.close()
        with self.assertRaises(core.ApiFailure) as unsafe: http.validate_url("https://evil.test/steal", {"api.x.com"})
        self.assertEqual(unsafe.exception.code, "UNSAFE_PROVIDER_HOST")

    def test_secret_redaction_removes_environment_secret_recursively(self):
        with patch.dict(os.environ, {"SNS_X_ACCESS_TOKEN": "super-secret-token"}, clear=True):
            value = core.redact({"message": "oops super-secret-token", "access_token": "super-secret-token",
                                 "authorization": "Bearer provider-echo", "nested": {"client_secret": "echo"}})
        self.assertNotIn("super-secret-token", str(value)); self.assertNotIn("access_token", value)
        self.assertNotIn("authorization", value); self.assertEqual(value["nested"], {})

    def test_user_agent_tracks_canonical_skill_version(self):
        self.assertEqual(http.USER_AGENT, "agent-skills-sns-api/1.0.1")


if __name__ == "__main__": unittest.main()
