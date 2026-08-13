import json
import os
import stat
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.sns_api_helpers import core
from sns_api_lib.auth import fingerprint
from sns_api_lib.providers.base import CredentialSnapshot
from sns_api_lib.providers import x


class XProviderTests(unittest.TestCase):
    def test_text_normalization_weighting_and_quote_rejection(self):
        self.assertEqual(x.validate_text("cafe\u0301"), "café")
        for value in ("👾", "🙋🏽", "👨‍🎤", "👨‍👩‍👧‍👦", "🇯🇵"):
            self.assertEqual(x.weighted_length(value), 2)
        with self.assertRaises(core.ApiFailure) as quote:
            x.validate_text("see https://x.com/example/status/123")
        self.assertIn("UNDECLARED_QUOTE_TARGET", str(quote.exception))
        with self.assertRaises(core.ApiFailure): x.validate_text("あ" * 141)

    def test_publish_normalization_rejects_media_and_unknown_fields(self):
        provider = x.XProvider()
        self.assertEqual(provider.normalize_publish("publish.text", {"text": "hello"}, []), {"text": "hello"})
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.text", {"text": "hello", "reply": "1"}, [])
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.text", {"text": "hello"}, [{"kind": "remote"}])

    def test_oauth1_signature_matches_x_documented_example(self):
        extra = {"api_key": "xvz1evFS4wEEPTGEFPHBog", "api_secret": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                 "access_token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
                 "access_token_secret": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}
        header = x.oauth1_header("POST", "https://api.x.com/1.1/statuses/update.json",
                                 {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
                                 extra, nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958")
        self.assertIn(x.percent("Ls93hJiZbQ3akF3HF3x1Bz8/zU4="), header)

    def test_legacy_environment_is_supported_but_conflict_fails_closed(self):
        provider = x.XProvider()
        legacy = {"X_ACCESS_TOKEN": "token", "X_OAUTH2_STATIC_CLIENT_ID": "client"}
        with patch.dict(os.environ, legacy, clear=True):
            result = provider.credentials(True); self.assertEqual(result.fingerprint, fingerprint("oauth2", "client"))
        with patch.dict(os.environ, {**legacy, "SNS_X_OAUTH2_ACCESS_TOKEN": "different"}, clear=True):
            with self.assertRaises(core.ApiFailure) as conflict: provider.credentials(True)
        self.assertEqual(conflict.exception.code, "ENV_CONFLICT")

    def test_partial_oauth1_environment_fails_closed(self):
        with patch.dict(os.environ, {"SNS_X_API_KEY": "key", "SNS_X_ACCESS_TOKEN": "token"}, clear=True):
            with self.assertRaises(core.ApiFailure): x.XProvider().credentials(True)

    def test_oauth2_refresh_rotates_private_store_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"
            response = type("R", (), {"body": {"access_token": "at1", "refresh_token": "rt2", "expires_in": 7200}})()
            with patch.object(x, "request", return_value=response) as called:
                self.assertEqual(x._refresh_token("cid", "secret", str(store), "rt1"), "at1")
                self.assertEqual(x._refresh_token("cid", "secret", str(store), "rt1"), "at1")
            self.assertEqual(called.call_count, 1)
            stored = json.loads(store.read_text())
            self.assertEqual(stored["refresh_token"], "rt2")
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)

    @unittest.skipIf(x.fcntl is None, "fcntl is required for the runtime contract")
    def test_oauth2_refresh_lock_allows_only_one_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"; calls = []; results = []
            response = type("R", (), {"body": {"access_token": "at1", "refresh_token": "rt2", "expires_in": 7200}})()
            def slow(*_args, **_kwargs): calls.append(1); time.sleep(.1); return response
            def run(): results.append(x._refresh_token("cid", "secret", str(store), "rt1"))
            with patch.object(x, "request", side_effect=slow):
                workers = [threading.Thread(target=run) for _ in range(2)]
                for worker in workers: worker.start()
                for worker in workers: worker.join()
            self.assertEqual(calls, [1]); self.assertEqual(results, ["at1", "at1"])

    def test_oauth2_store_failure_after_rotation_leaves_nonsecret_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"; marker = store.with_name(store.name + ".refresh-pending")
            response = type("R", (), {"body": {"access_token": "at1", "refresh_token": "rt2", "expires_in": 7200}})()
            original = x._atomic_private
            def fail_store(path, value):
                if path == store: raise OSError("disk full")
                return original(path, value)
            with patch.object(x, "request", return_value=response), patch.object(x, "_atomic_private", side_effect=fail_store):
                with self.assertRaises(core.ApiFailure) as failed: x._refresh_token("cid", "secret", str(store), "rt1")
            self.assertEqual(failed.exception.code, "CREDENTIAL_STATE_UNKNOWN")
            marker_text = marker.read_text()
            for secret in ("rt1", "rt2", "at1", "secret"): self.assertNotIn(secret, marker_text)
            with self.assertRaises(core.ApiFailure) as unresolved: x._refresh_token("cid", "secret", str(store), "rt1")
            self.assertEqual(unresolved.exception.code, "CREDENTIAL_STATE_UNKNOWN")

    def test_oauth2_rejected_refresh_clears_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"; marker = store.with_name(store.name + ".refresh-pending")
            rejected = core.ApiFailure("rejected", status=400)
            with patch.object(x, "request", side_effect=rejected), self.assertRaises(core.ApiFailure):
                x._refresh_token("cid", "secret", str(store), "rt1")
            self.assertFalse(marker.exists())

    def test_oauth2_committed_store_recovers_matching_stale_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"; marker = store.with_name(store.name + ".refresh-pending")
            x._atomic_private(store, {"access_token": "at1", "expires_at": time.time() + 3600,
                                      "refresh_token": "rt2", "last_rotation_id": "rotation-1"})
            x._atomic_private(marker, {"rotation_id": "rotation-1", "started_at": x.utc_now()})
            with patch.object(x, "request") as called:
                self.assertEqual(x._refresh_token("cid", "secret", str(store), "rt1"), "at1")
            called.assert_not_called(); self.assertFalse(marker.exists())

    def test_oauth2_unknown_refresh_requires_reauthorization_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "tokens.json"; marker = store.with_name(store.name + ".refresh-pending")
            failure = core.ApiFailure("unavailable", status=503)
            with patch.object(x, "request", side_effect=failure) as called, self.assertRaises(core.ApiFailure) as raised:
                x._refresh_token("cid", "secret", str(store), "rt1")
            self.assertEqual(raised.exception.code, "CREDENTIAL_STATE_UNKNOWN"); self.assertTrue(marker.exists())
            self.assertNotIn("rt1", marker.read_text())
            with patch.object(x, "request") as retry, self.assertRaises(core.ApiFailure):
                x._refresh_token("cid", "secret", str(store), "rt1")
            retry.assert_not_called(); self.assertEqual(called.call_count, 1)

    def test_app_bearer_is_used_for_eligible_reads_but_not_identity(self):
        provider = x.XProvider(); env = {"SNS_X_BEARER_TOKEN": "bearer", "SNS_X_APP_PUBLIC_ID": "app"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(provider.credentials(False, "user.lookup").auth_mode, "app")
            self.assertEqual(provider.read_call_budget("user.lookup", {}, None), 1)
            with self.assertRaises(core.ApiFailure): provider.credentials(False, "identity.read")

    def test_read_request_shapes_and_partial_response(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        response = type("R", (), {"body": {"data": [{"id": "1"}], "errors": [{"detail": "one missing"}], "meta": {"result_count": 1}},
                                  "rate_limit": {"remaining": "7"}})()
        with patch.object(x, "request", return_value=response) as called:
            result = provider.read(cred, "post.search.recent", {"query": "from:me", "max_results": 10})
        self.assertEqual(result["status"], "partial"); self.assertEqual(result["rate_limit"]["remaining"], "7")
        self.assertEqual(called.call_args.args[:2], ("GET", "https://api.x.com/2/tweets/search/recent"))
        self.assertEqual(called.call_args.kwargs["query"]["query"], "from:me")

    def test_user_lookup_preserves_username_and_stable_id_forms_and_pagination(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        response = type("R", (), {"body": {"data": {"id": "42"}}, "rate_limit": {}})()
        with patch.object(provider, "_call", return_value=response) as called:
            provider.read(cred, "user.lookup", {"user_id": "42"})
        self.assertEqual(called.call_args.args[2], "/2/users/42")
        with patch.object(provider, "_call", return_value=response) as called:
            provider.read(cred, "user.lookup", {"username": "XDevelopers"})
        self.assertEqual(called.call_args.args[2], "/2/users/by/username/XDevelopers")
        with self.assertRaises(core.ApiFailure): provider.read(cred, "user.lookup", {"username": "x", "user_id": "42"})
        with patch.object(provider, "_call", return_value=response) as called:
            provider.read(cred, "user.posts", {"user_id": "42", "max_results": 5, "next_token": "next"})
        self.assertEqual(called.call_args.args[3]["pagination_token"], "next")

    def test_max_results_and_user_id_are_never_clamped(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with self.assertRaises(core.ApiFailure): provider.read(cred, "user.posts", {"user_id": "bad?", "max_results": 10})
        with self.assertRaises(core.ApiFailure): provider.read(cred, "user.posts", {"user_id": "42", "max_results": 1})

    def test_reconcile_matches_html_and_tco_but_refuses_url_absence(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        attempted = datetime.now(timezone.utc)
        row = {"account_id": "42", "attempted_at": attempted.isoformat(), "provider_payload": {"text": "A & B"}}
        body = {"data": [{"id": "7", "text": "A &amp; B", "created_at": attempted.isoformat()}]}
        response = type("R", (), {"body": body})()
        with patch.object(provider, "_call", return_value=response): result = provider.reconcile(cred, row)
        self.assertEqual(result["status"], "confirmed_success")
        row["provider_payload"] = {"text": "see https://example.com"}
        response.body = {"data": [{"id": "1", "text": "other", "created_at": (attempted - timedelta(minutes=1)).isoformat()},
                                  {"id": "2", "text": "other", "created_at": (attempted + timedelta(minutes=1)).isoformat()}]}
        with patch.object(provider, "_call", return_value=response): result = provider.reconcile(cred, row)
        self.assertEqual(result["status"], "unresolved")

    def test_x_manual_resolve_id_must_be_numeric(self):
        provider = x.XProvider(); self.assertTrue(provider.valid_provider_id("123")); self.assertFalse(provider.valid_provider_id("post-1"))


if __name__ == "__main__": unittest.main()
