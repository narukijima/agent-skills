import os
import unittest
from unittest.mock import patch

from tests.sns_api_helpers import credentials
from sns_api_lib import core
from sns_api_lib.providers import instagram


class InstagramTests(unittest.TestCase):
    def test_auth_modes_are_explicit_and_wrong_mode_fails(self):
        provider = instagram.InstagramProvider()
        with patch.dict(os.environ, {"SNS_INSTAGRAM_AUTH_MODE": "instagram-login"}, clear=True): self.assertEqual(provider._host(), "graph.instagram.com")
        with patch.dict(os.environ, {"SNS_INSTAGRAM_AUTH_MODE": "facebook-login"}, clear=True): self.assertEqual(provider._host(), "graph.facebook.com")
        with patch.dict(os.environ, {"SNS_INSTAGRAM_AUTH_MODE": "auto"}, clear=True), self.assertRaises(core.ApiFailure) as raised: provider._host()
        self.assertEqual(raised.exception.code, "WRONG_AUTH_MODE")

    def test_reel_request_shape_waits_for_finished_before_publish(self):
        provider = instagram.InstagramProvider(); cred = credentials("instagram", "instagram-login")
        manifest = {"expected_account_id": "42", "operation": "publish.reel", "provider_payload": {"caption": "c", "share_to_feed": True},
                    "assets": [{"url": "https://cdn.test/v.mp4", "mime": "video/mp4"}], "_resume_state": {}}
        response = type("R", (), {"status": 200, "rate_limit": {}})(); calls = []
        def graph(_host, _version, _token, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return (response, {"id": "container"} if path.endswith("/media") else {"id": "post"})
        with patch.object(provider, "_host", return_value="graph.instagram.com"), patch.object(provider, "read", return_value={"data": {"status_code": "FINISHED"}}), patch.object(instagram, "graph_call", side_effect=graph):
            result = provider.publish(cred, manifest, lambda _: None)
        self.assertEqual(result["status"], "published")
        self.assertEqual(calls[0][2]["form"]["media_type"], "REELS"); self.assertEqual(calls[-1][1], "42/media_publish")

    def test_in_progress_container_returns_submitted_and_can_resume_without_recreate(self):
        provider = instagram.InstagramProvider(); cred = credentials("instagram", "instagram-login")
        manifest = {"expected_account_id": "42", "operation": "publish.image", "provider_payload": {"caption": "", "share_to_feed": False},
                    "assets": [{"url": "https://cdn.test/i.jpg", "mime": "image/jpeg"}], "_resume_state": {"container_id": "c1"}}
        with patch.object(provider, "read", return_value={"data": {"status_code": "IN_PROGRESS"}}), patch.object(instagram, "graph_call") as graph:
            result = provider.publish(cred, manifest, lambda _: None)
        self.assertEqual(result["status"], "submitted"); graph.assert_not_called()

    def test_carousel_creates_children_parent_then_publishes(self):
        provider = instagram.InstagramProvider(); cred = credentials("instagram", "facebook-login")
        assets = [{"url": "https://cdn.test/1.jpg", "mime": "image/jpeg"}, {"url": "https://cdn.test/2.mp4", "mime": "video/mp4"}]
        manifest = {"expected_account_id": "42", "operation": "publish.carousel", "provider_payload": {"caption": "c", "share_to_feed": False}, "assets": assets, "_resume_state": {}}
        response = type("R", (), {"status": 200, "rate_limit": {}})(); ids = iter(("child1", "child2", "parent", "post")); forms = []
        def graph(*_args, **kwargs): forms.append(kwargs.get("form", {})); return response, {"id": next(ids)}
        with patch.object(provider, "_host", return_value="graph.facebook.com"), patch.object(provider, "read", return_value={"data": {"status_code": "FINISHED"}}), patch.object(instagram, "graph_call", side_effect=graph):
            result = provider.publish(cred, manifest, lambda _: None)
        self.assertEqual(result["provider_id"], "post"); self.assertEqual(forms[2]["media_type"], "CAROUSEL"); self.assertEqual(forms[2]["children"], "child1,child2")

    def test_professional_account_and_media_limits_are_enforced(self):
        provider = instagram.InstagramProvider()
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.carousel", {}, [{"kind": "remote", "mime": "image/jpeg"}])
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.image", {"caption": "x" * 2201}, [{"kind": "remote", "mime": "image/jpeg"}])

    def test_reconcile_checks_container_but_preserves_final_media_id(self):
        provider = instagram.InstagramProvider(); row = {"provider_id": "media1", "provider_state": {"container_id": "container1", "provider_id": "media1"}}
        with patch.object(provider, "read", return_value={"data": {"status_code": "PUBLISHED"}}) as called:
            result = provider.reconcile(credentials("instagram", "instagram-login"), row)
        self.assertEqual(called.call_args.args[2]["resource_id"], "container1"); self.assertEqual(result["provider_id"], "media1")


if __name__ == "__main__": unittest.main()
