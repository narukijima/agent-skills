import io
import os
import json
import stat
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from tests.sns_api_helpers import FINGERPRINT, base_env, core, credentials, make_manifest, prepare_args, signed
from sns_api_lib import authorization, http, manifest
from sns_api_lib.ledger import get_intent, reserve_attempt


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

    def _standing_scope(self, **overrides):
        now = datetime.now(timezone.utc)
        value = {
            "schema_version": 1,
            "authorization_type": "standing",
            "authorization_id": "standing-editorial-1",
            "platform": "x",
            "operations": ["publish.text"],
            "expected_account_id": "42",
            "account_type": "user",
            "app_id": "app-1",
            "expected_credential_fingerprint": FINGERPRINT,
            "allowed_content_sources": ["pipeline:editorial-approved"],
            "max_provider_calls_per_intent": 3,
            "daily_write_call_limit": 100,
            "caller_scope": {"project_id": "project-1", "agent_id": "agent-1", "schedule_id": "schedule:daily"},
            "not_before": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
        }
        value.update(overrides)
        return value

    def _standing_authorization(self, **overrides):
        value = self._standing_scope(**overrides)
        with patch.dict(os.environ, base_env(), clear=True):
            value = authorization.sign_standing_authorization(value)
        path = Path(self.temp.name) / ("standing-" + str(time.time_ns()) + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_sign_standing_authorization_cli_signs_scope_end_to_end(self):
        import sns_api
        scope_path = Path(self.temp.name) / "scope.json"
        scope_path.write_text(json.dumps(self._standing_scope()), encoding="utf-8")
        output_path = Path(self.temp.name) / "standing-signed.json"
        out = io.StringIO()
        with patch.dict(os.environ, base_env(), clear=True), patch("sys.stdout", out):
            code = sns_api.main([
                "sign-standing-authorization", "--scope-file", str(scope_path), "--output", str(output_path),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "signed")
        self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
        env = base_env(SNS_API_SCHEDULE_ID="schedule:daily")
        manifest_path = Path(self.temp.name) / "standing-cli.json"
        make_manifest(
            manifest_path, environment=env, approval_id=None, standing_authorization_file=str(output_path),
            content_source="pipeline:editorial-approved",
        )
        self.assertEqual(signed(manifest_path)["approval_id"], "standing-editorial-1")

    def test_sign_standing_authorization_cli_rejects_incomplete_scope(self):
        import sns_api
        scope = self._standing_scope()
        scope.pop("operations")
        scope_path = Path(self.temp.name) / "bad-scope.json"
        scope_path.write_text(json.dumps(scope), encoding="utf-8")
        output_path = Path(self.temp.name) / "bad-signed.json"
        error = io.StringIO()
        with patch.dict(os.environ, base_env(), clear=True), patch("sys.stderr", error):
            code = sns_api.main([
                "sign-standing-authorization", "--scope-file", str(scope_path), "--output", str(output_path),
            ])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(error.getvalue())["errors"][0]["code"], "INVALID_AUTHORIZATION")
        self.assertFalse(output_path.exists())

    def test_manifest_tamper_and_expiry_are_rejected(self):
        path = Path(self.temp.name) / "approved.json"; make_manifest(path)
        value = json.loads(path.read_text()); value["provider_payload"]["text"] = "changed"; path.write_text(json.dumps(value))
        with patch.dict(os.environ, base_env(), clear=True), self.assertRaises(core.ApiFailure) as tampered: manifest.load_manifest(path)
        self.assertEqual(tampered.exception.code, "MANIFEST_TAMPERED")
        make_manifest(path); value = json.loads(path.read_text())
        value["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        value["manifest_hash"] = manifest.manifest_hash(value)
        with patch.dict(os.environ, base_env(), clear=True): value["hmac_signature"] = manifest.signature(value)
        path.write_text(json.dumps(value))
        with patch.dict(os.environ, base_env(), clear=True), self.assertRaises(core.ApiFailure) as expired: manifest.load_manifest(path)
        self.assertEqual(expired.exception.code, "MANIFEST_EXPIRED")

    def test_secret_value_in_payload_is_rejected_before_write(self):
        path = Path(self.temp.name) / "m.json"; env = base_env(SNS_PRIVATE_ACCESS_TOKEN="secret-value-12345")
        args = prepare_args(path, payload={"text": "secret-value-12345"})
        with patch.dict(os.environ, env, clear=True), self.assertRaises(core.ApiFailure) as raised: core.prepare(args)
        self.assertEqual(raised.exception.code, "SECRET_IN_MANIFEST"); self.assertFalse(path.exists())

    def test_send_parser_surface_has_manifest_only(self):
        import sns_api
        parser = sns_api.parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): parser.parse_args(["send", "--manifest", "m.json", "--platform", "x"])
            with self.assertRaises(SystemExit): parser.parse_args(["send", "--manifest", "m.json", "--payload", "{}"])

    def test_standing_authorization_proceeds_without_per_intent_human_approval(self):
        authorization = self._standing_authorization()
        path = Path(self.temp.name) / "standing.json"
        env = base_env(SNS_API_SCHEDULE_ID="schedule:daily")
        make_manifest(
            path, environment=env, approval_id=None, standing_authorization_file=str(authorization),
            content_source="pipeline:editorial-approved",
        )
        value = signed(path)
        self.assertEqual(value["approval_id"], "standing-editorial-1")
        self.assertEqual(value["domain_authorization"]["type"], "standing")
        self.assertEqual(self._send(path, env=env)["status"], "published")

    def test_standing_authorization_rejects_scope_and_caller_mismatches(self):
        env = base_env(SNS_API_SCHEDULE_ID="schedule:daily")
        for label, authorization, kwargs in (
            ("account", self._standing_authorization(expected_account_id="99"), {}),
            ("app", self._standing_authorization(app_id="other-app"), {}),
            ("credential", self._standing_authorization(expected_credential_fingerprint="0" * 64), {}),
            ("operation", self._standing_authorization(operations=["publish.image"]), {}),
            ("budget", self._standing_authorization(max_provider_calls_per_intent=2), {}),
            ("daily-budget", self._standing_authorization(daily_write_call_limit=50), {}),
            ("content-source", self._standing_authorization(), {"content_source": "pipeline:unreviewed"}),
        ):
            with self.subTest(label=label), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(core.ApiFailure) as raised:
                    core.prepare(prepare_args(
                        Path(self.temp.name) / (label + ".json"), approval_id=None,
                        standing_authorization_file=str(authorization),
                        content_source=kwargs.get("content_source", "pipeline:editorial-approved"),
                    ))
                self.assertEqual(raised.exception.code, "AUTHORIZATION_SCOPE_MISMATCH")

        authorization = self._standing_authorization()
        path = Path(self.temp.name) / "caller.json"
        make_manifest(
            path, environment=env, approval_id=None, standing_authorization_file=str(authorization),
            content_source="pipeline:editorial-approved",
        )
        with self.assertRaises(core.ApiFailure) as raised:
            self._send(path, env=base_env(SNS_API_SCHEDULE_ID="schedule:other"))
        self.assertEqual(raised.exception.code, "AUTHORIZATION_SCOPE_MISMATCH")

    def test_standing_authorization_tamper_is_rejected(self):
        path = self._standing_authorization()
        value = json.loads(path.read_text(encoding="utf-8"))
        value["allowed_content_sources"] = ["pipeline:unreviewed"]
        path.write_text(json.dumps(value), encoding="utf-8")
        with patch.dict(os.environ, base_env(SNS_API_SCHEDULE_ID="schedule:daily"), clear=True):
            with self.assertRaises(core.ApiFailure) as raised:
                core.prepare(prepare_args(
                    Path(self.temp.name) / "tampered.json", approval_id=None,
                    standing_authorization_file=str(path), content_source="pipeline:unreviewed",
                ))
        self.assertEqual(raised.exception.code, "AUTHORIZATION_TAMPERED")

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

    def test_x_image_mutation_stops_before_budget_credentials_and_attempt(self):
        image = Path(self.temp.name) / "photo.png"; image.write_bytes(b"approved")
        path = Path(self.temp.name) / "image.json"
        make_manifest(path, operation="publish.image", payload={
            "text": "caption", "alt_texts": ["description"],
            "assets": [{"kind": "local", "path": str(image), "mime": "image/png"}],
        })
        image.write_bytes(b"mutated")
        provider = core.provider("x")
        with patch.dict(os.environ, base_env(SNS_API_WRITE_MAX_CALLS="6"), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as raised:
                core.send(path)
        self.assertEqual(raised.exception.code, "ASSET_MUTATED")
        called.assert_not_called()
        self.assertFalse((Path(self.temp.name) / "state/sns-api/usage.sqlite3").exists())
        self.assertFalse((Path(self.temp.name) / "state/sns-api/ledger.sqlite3").exists())

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

    def test_provider_resumable_uncertainty_is_submitted_not_account_blocking_unknown(self):
        path = Path(self.temp.name) / "submitted.json"; make_manifest(path)
        failure = core.ApiFailure("prepublish timeout", code="PROVIDER_RESULT_UNKNOWN", outcome="submitted")
        with self.assertRaises(core.ApiFailure): self._send(path, publish=lambda *_: (_ for _ in ()).throw(failure))
        self.assertEqual(get_intent("x", "42", "content-1")["status"], "submitted")

    def test_reconcile_resume_safe_changes_unknown_to_submitted(self):
        path = Path(self.temp.name) / "threads.json"
        make_manifest(path, platform="threads", operation="publish.text", payload={"text": "hello"},
                      account_type="threads-user")
        manifest = signed(path)
        reserve_attempt(manifest)
        provider = core.provider("threads")
        with patch.dict(os.environ, base_env(), clear=True), \
                patch.object(provider, "credentials", return_value=credentials("threads")), \
                patch.object(provider, "identity", return_value={"id": "42", "account_type": "threads-user"}), \
                patch.object(provider, "reconcile", return_value={
                    "status": "resume_safe", "provider": {"public_publish_started": False},
                }):
            result = core.reconcile("threads", "content-1", "42")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["data"]["reconcile_outcome"], "resume_safe")
        self.assertEqual(get_intent("threads", "42", "content-1")["status"], "submitted")

    def test_facebook_unknown_supports_evidence_bound_manual_resolve(self):
        path = Path(self.temp.name) / "facebook.json"
        make_manifest(path, platform="facebook", operation="publish.text", payload={"message": "hello"},
                      account_type="page")
        reserve_attempt(signed(path))
        with patch.dict(os.environ, base_env(), clear=True):
            result = core.resolve("facebook", "content-1", "42", "published",
                                  "verified in Page activity log", "42_9001")
        self.assertEqual(result["status"], "published")
        row = get_intent("facebook", "42", "content-1")
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["provider_id"], "42_9001")

    def test_429_refunds_attempt_and_gates_locally_until_official_reset(self):
        path = Path(self.temp.name) / "rate.json"; make_manifest(path)
        failure = core.ApiFailure("rate", status=429, outcome="rate_limited")
        with self.assertRaises(core.ApiFailure): self._send(path, publish=lambda *_: (_ for _ in ()).throw(failure))
        self.assertEqual(get_intent("x", "42", "content-1")["attempts"], 0)
        provider = core.provider("x")
        with patch.dict(os.environ, base_env(), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as gated: core.send(path)
        self.assertEqual(gated.exception.code, "RATE_LIMIT_ACTIVE"); called.assert_not_called()

    def test_429_retry_proceeds_after_provider_reset_passes(self):
        path = Path(self.temp.name) / "rate-reset.json"; make_manifest(path)
        failure = core.ApiFailure("rate", status=429, outcome="rate_limited",
                                  meta={"rate_limit": {"retry_after": "0"}})
        with self.assertRaises(core.ApiFailure): self._send(path, publish=lambda *_: (_ for _ in ()).throw(failure))
        self.assertEqual(get_intent("x", "42", "content-1")["attempts"], 0)
        result = self._send(path); self.assertEqual(result["status"], "published")

    def test_definite_failure_retry_reuses_same_domain_authorization(self):
        first = Path(self.temp.name) / "first.json"; make_manifest(first)
        failure = core.ApiFailure("bad", status=400)
        with self.assertRaises(core.ApiFailure): self._send(first, publish=lambda *_: (_ for _ in ()).throw(failure))
        self.assertEqual(self._send(first)["status"], "published")

    def test_attempt_limit_is_per_domain_authorization_and_new_authorization_can_retry(self):
        path = Path(self.temp.name) / "limited.json"; make_manifest(path)
        failure = core.ApiFailure("forbidden", status=403)
        for _ in range(2):
            with self.assertRaises(core.ApiFailure):
                self._send(path, publish=lambda *_: (_ for _ in ()).throw(failure))
        provider = core.provider("x")
        with patch.dict(os.environ, base_env(), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as limited: core.send(path)
        self.assertEqual(limited.exception.code, "ATTEMPT_LIMIT"); called.assert_not_called()
        reauthorized = Path(self.temp.name) / "reauthorized.json"
        make_manifest(reauthorized, approval_id="approval-2")
        self.assertEqual(self._send(reauthorized)["status"], "published")
        row = get_intent("x", "42", "content-1")
        self.assertEqual(row["status"], "published"); self.assertEqual(row["attempts"], 3)

    def test_duplicate_after_publish_is_refused_before_any_billable_call(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path)
        self._send(path)
        provider = core.provider("x")
        with patch.dict(os.environ, base_env(), clear=True), patch.object(provider, "credentials") as called:
            with self.assertRaises(core.ApiFailure) as duplicate: core.send(path)
        self.assertEqual(duplicate.exception.code, "DUPLICATE"); called.assert_not_called()

    def test_x_quote_uses_official_quote_tweet_id_and_rejects_url_in_text(self):
        path = Path(self.temp.name) / "quote.json"
        make_manifest(path, operation="publish.quote", payload={
            "text": "approved comment", "quote_url": "https://x.com/example/status/123456789",
        })
        value = signed(path)
        self.assertEqual(value["provider_payload"], {"text": "approved comment", "quote_tweet_id": "123456789"})
        provider = core.provider("x")
        sent = {}
        def publish(credentials, manifest, checkpoint):
            body = {"text": manifest["provider_payload"]["text"],
                    "quote_tweet_id": manifest["provider_payload"]["quote_tweet_id"]}
            sent.update(body)
            return {"status": "published", "provider_id": "9", "provider_status": "published"}
        self._send(path, publish=publish)
        self.assertEqual(sent["quote_tweet_id"], "123456789")
        with patch.dict(os.environ, base_env(), clear=True), self.assertRaises(core.ApiFailure) as raised:
            core.prepare(prepare_args(Path(self.temp.name) / "url-text.json", payload={
                "text": "see https://x.com/example/status/123456789",
            }))
        self.assertEqual(raised.exception.payload["errors"], ["UNDECLARED_QUOTE_TARGET"])

    def test_definite_failure_retry_cannot_change_binding_under_same_authorization_reference(self):
        first = Path(self.temp.name) / "first.json"; make_manifest(first)
        failure = core.ApiFailure("bad", status=400)
        with self.assertRaises(core.ApiFailure):
            self._send(first, publish=lambda *_: (_ for _ in ()).throw(failure))
        changed = Path(self.temp.name) / "changed.json"
        make_manifest(changed, credential_fingerprint="0" * 64)
        with self.assertRaises(core.ApiFailure) as raised:
            reserve_attempt(signed(changed))
        self.assertEqual(raised.exception.code, "AUTHORIZATION_SCOPE_MISMATCH")

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
        with self.assertRaises(core.ApiFailure) as capability: core.provider("x").require_capability("reply.create")
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

    def test_secret_redaction_rejects_generic_secret_keys_and_encoded_values(self):
        secret = "private/key value"
        with patch.dict(os.environ, {"SNS_X_API_KEY": secret}, clear=True):
            value = core.redact({
                "token": "provider-echo", "secret": "provider-echo", "credential": "provider-echo",
                "session_url": "https://upload.test/session", "message": "value=private%2Fkey%20value",
            })
        for key in ("token", "secret", "credential", "session_url"):
            self.assertNotIn(key, value)
        self.assertNotIn("private%2Fkey%20value", value["message"])

    def test_user_agent_tracks_canonical_skill_version(self):
        self.assertEqual(http.USER_AGENT, "agent-skills-sns-api/3.1.0")


if __name__ == "__main__": unittest.main()
