import json
import hashlib
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
from sns_api_lib import media
from sns_api_lib.auth import fingerprint
from sns_api_lib.providers.base import CredentialSnapshot
from sns_api_lib.providers import x


def result(body, status=200, rate_limit=None):
    return type("R", (), {"body": body, "status": status, "rate_limit": rate_limit or {}})()


class XProviderTests(unittest.TestCase):
    def test_text_normalization_weighting_and_quote_rejection(self):
        self.assertEqual(x.validate_text("cafe\u0301"), "café")
        for value in ("👾", "🙋🏽", "👨‍🎤", "👨‍👩‍👧‍👦", "🇯🇵"):
            self.assertEqual(x.weighted_length(value), 2)
        with self.assertRaises(core.ApiFailure) as quote:
            x.validate_text("see https://x.com/example/status/123")
        self.assertIn("UNDECLARED_QUOTE_TARGET", str(quote.exception))
        with self.assertRaises(core.ApiFailure): x.validate_text("あ" * 141)

    def test_publish_normalization_preserves_legacy_text_hash_and_rejects_unknown_fields(self):
        provider = x.XProvider()
        self.assertEqual(provider.normalize_publish("publish.text", {"text": "hello"}, []), {"text": "hello"})
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.text", {"text": "hello", "reply": "1"}, [])
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.text", {"text": "hello"}, [{"kind": "remote"}])

    def test_quote_operation_canonicalizes_and_binds_url_in_text(self):
        provider = x.XProvider()
        payload = provider.normalize_publish(
            "publish.quote", {"text": "comment", "quote_url": "https://twitter.com/example/status/123?ref=tracking"}, [],
        )
        self.assertEqual(payload, {
            "text": "comment\nhttps://x.com/i/web/status/123",
            "quote_url": "https://x.com/i/web/status/123",
        })
        embedded = provider.normalize_publish(
            "publish.quote", {"text": "comment https://x.com/example/status/123", "quote_url": "https://x.com/i/web/status/123"}, [],
        )
        self.assertEqual(embedded["text"], "comment https://x.com/i/web/status/123")
        with self.assertRaises(core.ApiFailure):
            provider.normalize_publish("publish.text", {"text": "comment https://x.com/example/status/123"}, [])
        with self.assertRaises(core.ApiFailure):
            provider.normalize_publish("publish.quote", {"text": "comment", "quote_url": "https://example.com/post/123"}, [])
        credential = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with patch.object(provider, "_call", return_value=result({"data": {"id": "456", "text": payload["text"]}}, status=201)) as called:
            published = provider.publish(credential, {"operation": "publish.quote", "provider_payload": payload, "assets": [], "_resume_state": {}}, lambda _state: None)
        self.assertEqual(published["provider_id"], "456")
        self.assertEqual(called.call_args.kwargs["body"], {"text": payload["text"]})
        self.assertNotIn("quote_tweet_id", called.call_args.kwargs["body"])

    def test_image_publish_uploads_local_bytes_sets_alt_text_and_attaches_media_ids(self):
        provider = x.XProvider(); credential = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.png"; path.write_bytes(b"image-bytes")
            asset = media.local_asset({"kind": "local", "path": str(path), "mime": "image/png"})
            payload = provider.normalize_publish("publish.image", {
                "text": "photo", "alt_texts": ["A test image"], "made_with_ai": True,
            }, [asset])
            manifest = {"operation": "publish.image", "provider_payload": payload, "assets": [asset], "_resume_state": {}}
            responses = [
                result({"data": {"id": "101", "media_key": "3_101"}}),
                result({"data": {"id": "101"}}),
                result({"data": {"id": "202", "text": "photo"}}, status=201, rate_limit={"remaining": "9"}),
            ]
            checkpoints = []
            with patch.object(provider, "_call", side_effect=responses) as called:
                published = provider.publish(credential, manifest, lambda state: checkpoints.append(dict(state)))
        self.assertEqual(published["status"], "published")
        upload_body = called.call_args_list[0].kwargs["body"]
        self.assertEqual(upload_body, {"media": "aW1hZ2UtYnl0ZXM=", "media_category": "tweet_image"})
        self.assertEqual(called.call_args_list[1].args[2], "/2/media/metadata")
        post_body = called.call_args_list[2].kwargs["body"]
        self.assertEqual(post_body["media"], {"media_ids": ["101"]})
        self.assertTrue(post_body["made_with_ai"])
        self.assertNotIn("reply", post_body); self.assertNotIn("quote_tweet_id", post_body)
        self.assertTrue(checkpoints[-2]["post_create_started"])
        self.assertEqual(checkpoints[-1]["provider_id"], "202")

    def test_partial_media_response_is_not_hidden_as_success(self):
        provider = x.XProvider(); credential = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.png"; path.write_bytes(b"image")
            asset = media.local_asset({"kind": "local", "path": str(path), "mime": "image/png"})
            manifest = {"operation": "publish.image", "provider_payload": {"text": "photo"}, "assets": [asset], "_resume_state": {}}
            partial = result({"data": {"id": "101", "media_key": "3_101"}, "errors": [{"detail": "partial"}]})
            checkpoints = []
            with patch.object(provider, "_call", return_value=partial), self.assertRaises(core.ApiFailure) as raised:
                provider.publish(credential, manifest, lambda state: checkpoints.append(dict(state)))
        self.assertEqual(raised.exception.code, "INVALID_PROVIDER_RESPONSE")
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertFalse(checkpoints[-1]["post_create_started"])

    def test_post_timeout_after_media_upload_keeps_durable_post_started_boundary(self):
        provider = x.XProvider(); credential = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.png"; path.write_bytes(b"image")
            asset = media.local_asset({"kind": "local", "path": str(path), "mime": "image/png"})
            manifest = {"operation": "publish.image", "provider_payload": {"text": "photo"}, "assets": [asset], "_resume_state": {}}
            checkpoints = []
            with patch.object(provider, "_call", side_effect=[
                result({"data": {"id": "101", "media_key": "3_101"}}),
                core.ApiFailure("timeout", code="PROVIDER_RESULT_UNKNOWN", outcome="unknown"),
            ]), self.assertRaises(core.ApiFailure):
                provider.publish(credential, manifest, lambda state: checkpoints.append(dict(state)))
        self.assertTrue(checkpoints[-1]["post_create_started"])
        row = {"account_id": "42", "attempted_at": checkpoints[-1]["post_create_started_at"],
               "provider_payload": {"text": "photo"}, "provider_state": checkpoints[-1]}
        with patch.object(provider, "_call", return_value=result({"data": []})):
            reconciled = provider.reconcile(credential, row)
        self.assertEqual(reconciled["status"], "unresolved")

    def test_chunked_video_submits_then_resumes_same_media_without_reupload(self):
        provider = x.XProvider(); credential = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with tempfile.TemporaryDirectory() as directory, patch.object(x.x_media, "UPLOAD_CHUNK_BYTES", 4):
            path = Path(directory) / "clip.mp4"; path.write_bytes(b"abcdef")
            asset = media.local_asset({"kind": "local", "path": str(path), "mime": "video/mp4"})
            payload = provider.normalize_publish("publish.video", {"text": "clip"}, [asset])
            manifest = {"operation": "publish.video", "provider_payload": payload, "assets": [asset], "_resume_state": {}}
            responses = [
                result({"data": {"id": "301", "media_key": "7_301"}}),
                result({"data": {"expires_at": 1}}), result({"data": {"expires_at": 1}}),
                result({"data": {"id": "301", "media_key": "7_301", "processing_info": {"state": "pending"}}}),
                result({"data": {"id": "301", "media_key": "7_301", "processing_info": {"state": "in_progress"}}}),
            ]
            checkpoints = []
            with patch.object(provider, "_call", side_effect=responses) as first:
                submitted = provider.publish(credential, manifest, lambda state: checkpoints.append(dict(state)))
            self.assertEqual(submitted["status"], "submitted")
            self.assertEqual([item.kwargs["body"]["segment_index"] for item in first.call_args_list[1:3]], [0, 1])
            resume = dict(checkpoints[-1]); manifest["_resume_state"] = resume
            with patch.object(provider, "_call", side_effect=[
                result({"data": {"id": "301", "media_key": "7_301", "processing_info": {"state": "succeeded"}}}),
                result({"data": {"id": "401", "text": "clip"}}, status=201),
            ]) as second:
                published = provider.publish(credential, manifest, lambda state: checkpoints.append(dict(state)))
        self.assertEqual(published["provider_id"], "401")
        self.assertEqual([item.args[2] for item in second.call_args_list], ["/2/media/upload", "/2/tweets"])
        self.assertEqual(second.call_args_list[-1].kwargs["body"]["media"], {"media_ids": ["301"]})

    def test_chunked_upload_detects_same_size_mutation_before_finalize(self):
        provider = x.XProvider(); credential = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        with tempfile.TemporaryDirectory() as directory, patch.object(x.x_media, "UPLOAD_CHUNK_BYTES", 4):
            path = Path(directory) / "clip.mp4"; path.write_bytes(b"abcdef")
            asset = media.local_asset({"kind": "local", "path": str(path), "mime": "video/mp4"})
            manifest = {"operation": "publish.video", "provider_payload": {"text": "clip"}, "assets": [asset], "_resume_state": {}}
            calls = []
            def transport(_credentials, _method, endpoint, **_kwargs):
                calls.append(endpoint)
                if endpoint.endswith("/initialize"):
                    return result({"data": {"id": "301", "media_key": "7_301"}})
                if endpoint.endswith("/append") and calls.count(endpoint) == 1:
                    path.write_bytes(b"abcdXY")
                return result({"data": {"expires_at": 1}})
            with patch.object(provider, "_call", side_effect=transport), self.assertRaises(core.ApiFailure) as raised:
                provider.publish(credential, manifest, lambda _state: None)
        self.assertEqual(raised.exception.code, "ASSET_MUTATED")
        self.assertFalse(any(endpoint.endswith("/finalize") for endpoint in calls))

    def test_media_validation_and_call_plan_are_bounded(self):
        provider = x.XProvider()
        local = {"kind": "local", "path": "/tmp/a.png", "mime": "image/png", "size": 10, "sha256": hashlib.sha256(b"x").hexdigest()}
        normalized = provider.normalize_publish("publish.image", {"text": "", "alt_texts": ["one"]}, [local])
        plan = provider.call_plan("publish.image", normalized, [local])
        self.assertEqual(plan["max_calls"], 6)
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.image", {"text": ""}, [{**local, "kind": "remote"}])
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.video", {"text": ""}, [{**local, "mime": "video/quicktime"}])
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.image", {"text": "", "alt_texts": []}, [local] * 5)

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
            with self.assertRaises(core.ApiFailure): provider.credentials(False, "publish.status")

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

    def test_reconcile_media_requires_matching_media_keys(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        attempted = datetime.now(timezone.utc)
        row = {
            "account_id": "42", "attempted_at": attempted.isoformat(), "provider_payload": {"text": "photo"},
            "provider_state": {"post_create_started": True, "post_create_started_at": attempted.isoformat(), "media_keys": ["3_101"]},
        }
        response = result({"data": [{"id": "7", "text": "photo", "created_at": attempted.isoformat(),
                                      "attachments": {"media_keys": ["3_999"]}}]})
        with patch.object(provider, "_call", return_value=response): unmatched = provider.reconcile(cred, row)
        self.assertEqual(unmatched["status"], "unresolved")
        response.body["data"][0]["attachments"]["media_keys"] = ["3_101"]
        with patch.object(provider, "_call", return_value=response): matched = provider.reconcile(cred, row)
        self.assertEqual(matched["status"], "confirmed_success")

    def test_reconcile_quote_matches_target_reference_when_x_omits_url_from_text(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        attempted = datetime.now(timezone.utc)
        row = {
            "account_id": "42", "attempted_at": attempted.isoformat(),
            "provider_payload": {"text": "comment\nhttps://x.com/i/web/status/123", "quote_url": "https://x.com/i/web/status/123"},
            "provider_state": {"post_create_started": True, "post_create_started_at": attempted.isoformat()},
        }
        response = result({"data": [{"id": "7", "text": "comment", "created_at": attempted.isoformat(),
                                      "referenced_tweets": [{"type": "quoted", "id": "123"}]}]})
        with patch.object(provider, "_call", return_value=response): matched = provider.reconcile(cred, row)
        self.assertEqual(matched["status"], "confirmed_success")
        response.body["data"][0]["referenced_tweets"][0]["id"] = "999"
        with patch.object(provider, "_call", return_value=response): wrong_target = provider.reconcile(cred, row)
        self.assertEqual(wrong_target["status"], "unresolved")

    def test_reconcile_proves_no_post_when_durable_checkpoint_precedes_post_request(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        row = {"status": "unknown", "provider_state": {"post_create_started": False}, "provider_payload": {"text": "photo"}}
        with patch.object(provider, "_call") as called:
            outcome = provider.reconcile(cred, row)
        self.assertEqual(outcome["status"], "confirmed_absent"); called.assert_not_called()
        self.assertEqual(provider.reconcile_call_budget(row), 1)

    def test_media_status_uses_allowlisted_user_context_shape(self):
        provider = x.XProvider(); cred = CredentialSnapshot("oauth2", "token", "client", fingerprint("oauth2", "client"))
        response = result({"data": {"id": "301", "media_key": "7_301", "processing_info": {"state": "succeeded"}}})
        with patch.object(provider, "_call", return_value=response) as called:
            value = provider.read(cred, "publish.status", {"resource_id": "301"})
        self.assertEqual(value["data"]["id"], "301")
        self.assertEqual(called.call_args.args[2], "/2/media/upload")
        self.assertEqual(called.call_args.args[3], {"media_id": "301"})

    def test_x_manual_resolve_id_must_be_numeric(self):
        provider = x.XProvider(); self.assertTrue(provider.valid_provider_id("123")); self.assertFalse(provider.valid_provider_id("post-1"))


if __name__ == "__main__": unittest.main()
