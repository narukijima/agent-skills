import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.sns_api_helpers import core, credentials
from sns_api_lib import core
from sns_api_lib.providers import facebook


class FacebookTests(unittest.TestCase):
    def test_personal_profile_surface_does_not_exist(self):
        provider = facebook.FacebookProvider()
        self.assertNotIn("publish.profile", provider.capabilities); self.assertIn("page.content", provider.capabilities)

    def test_page_text_photo_video_request_shapes(self):
        provider = facebook.FacebookProvider(); cred = credentials("facebook", "page-token")
        response = type("R", (), {"status": 200, "rate_limit": {}})()
        cases = [
            ("publish.text", {"message": "hi"}, [], "42/feed", "message", "published"),
            ("publish.image", {"caption": "c"}, [{"url": "https://cdn.test/a.jpg"}], "42/photos", "url", "published"),
            ("publish.video", {"description": "d"}, [{"url": "https://cdn.test/a.mp4"}], "42/videos", "file_url", "submitted"),
        ]
        for operation, payload, assets, path, key, status in cases:
            with self.subTest(operation=operation), patch.object(facebook, "graph_call", return_value=(response, {"id": "p1"})) as call:
                result = provider.publish(cred, {"expected_account_id": "42", "operation": operation, "provider_payload": payload, "assets": assets}, lambda _: None)
            self.assertEqual(call.call_args.args[4], path); self.assertIn(key, call.call_args.kwargs["form"]); self.assertEqual(result["status"], status)

    def test_remote_media_is_required_and_partial_reads_are_preserved(self):
        provider = facebook.FacebookProvider()
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.image", {}, [{"kind": "local", "mime": "image/jpeg"}])
        response = type("R", (), {"rate_limit": {"app_usage": "{}"}})()
        with patch.object(facebook, "_account", return_value="42"), patch.object(facebook, "graph_call", return_value=(response, {"data": [{"id": "1"}], "errors": [{"message": "partial"}]})):
            result = provider.read(credentials("facebook", "page-token"), "page.content", {})
        self.assertEqual(result["status"], "partial")

    def test_video_reconcile_preserves_processing_and_requires_ready_evidence(self):
        provider = facebook.FacebookProvider(); cred = credentials("facebook", "page-token")
        row = {"provider_id": "v1", "provider_state": {}}
        with patch.object(provider, "read", return_value={"data": {"id": "v1", "status": {"video_status": "processing"}}}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "unresolved")
        with patch.object(provider, "read", return_value={"data": {"id": "v1", "status": {"video_status": "ready"}}}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "confirmed_success")
        with patch.object(provider, "read", return_value={"data": {"id": "v1", "status": {"video_status": "error"}}}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "confirmed_absent")

    def test_unknown_without_id_reconciles_against_recent_page_content(self):
        provider = facebook.FacebookProvider(); cred = credentials("facebook", "page-token")
        attempted = datetime.now(timezone.utc)
        row = {"operation": "publish.text", "attempted_at": attempted.isoformat(), "provider_payload": {"message": "approved"},
               "provider_state": {"publish_started": True, "publish_started_at": attempted.isoformat()}}
        recent = {"status": "success", "data": [{"id": "42_99", "message": "approved", "created_time": attempted.isoformat()}], "errors": []}
        with patch.object(provider, "read", return_value=recent): result = provider.reconcile(cred, row)
        self.assertEqual(result["status"], "confirmed_success"); self.assertEqual(result["provider_id"], "42_99")

    def test_unknown_recent_page_content_requires_unique_match_and_manual_resolve_is_declared(self):
        provider = facebook.FacebookProvider(); cred = credentials("facebook", "page-token")
        attempted = datetime.now(timezone.utc)
        item = {"id": "42_99", "message": "same", "created_time": attempted.isoformat()}
        row = {"operation": "publish.text", "attempted_at": attempted.isoformat(), "provider_payload": {"message": "same"},
               "provider_state": {"publish_started": True, "publish_started_at": attempted.isoformat()}}
        with patch.object(provider, "read", return_value={"status": "success", "data": [item, {**item, "id": "42_100"}], "errors": []}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "unresolved")
        self.assertTrue(provider.supports_manual_resolve); self.assertTrue(provider.valid_provider_id("42_99"))
        self.assertFalse(provider.valid_provider_id("https://evil.test"))

    def test_crash_before_page_write_checkpoint_is_absent_only_after_grace(self):
        provider = facebook.FacebookProvider(); cred = credentials("facebook", "page-token")
        old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        row = {"operation": "publish.text", "attempted_at": old, "provider_payload": {"message": "approved"},
               "provider_state": {}}
        with patch.object(provider, "read") as read:
            result = provider.reconcile(cred, row)
        self.assertEqual(result["status"], "confirmed_absent"); read.assert_not_called()

    def test_publish_checkpoints_before_page_post_request(self):
        provider = facebook.FacebookProvider(); cred = credentials("facebook", "page-token"); states = []
        manifest = {"expected_account_id": "42", "operation": "publish.text", "provider_payload": {"message": "hi"}, "assets": []}
        with patch.object(facebook, "graph_call", side_effect=core.ApiFailure("timeout", outcome="unknown")), self.assertRaises(core.ApiFailure):
            provider.publish(cred, manifest, states.append)
        self.assertTrue(states[-1]["publish_started"]); self.assertEqual(states[-1]["stage"], "publish_started")


if __name__ == "__main__": unittest.main()
