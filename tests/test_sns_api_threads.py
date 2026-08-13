import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.sns_api_helpers import credentials
from sns_api_lib import core
from sns_api_lib.providers import threads


class ThreadsTests(unittest.TestCase):
    def test_text_publish_uses_container_status_and_publish(self):
        provider = threads.ThreadsProvider(); cred = credentials("threads", "threads-oauth2")
        manifest = {"expected_account_id": "42", "operation": "publish.text", "provider_payload": {"text": "hello", "alt_text": ""}, "assets": [], "_resume_state": {}}
        response = type("R", (), {"status": 200, "rate_limit": {}})(); calls = []
        def graph(_host, _version, _token, method, path, **kwargs): calls.append((method, path, kwargs)); return response, {"id": "container" if path.endswith("/threads") else "post"}
        with patch.object(provider, "read", return_value={"data": {"status": "FINISHED"}}), patch.object(threads, "graph_call", side_effect=graph):
            result = provider.publish(cred, manifest, lambda _: None)
        self.assertEqual(result["status"], "published"); self.assertEqual(calls[0][2]["form"]["media_type"], "TEXT"); self.assertEqual(calls[-1][1], "42/threads_publish")

    def test_image_video_and_carousel_request_shapes(self):
        provider = threads.ThreadsProvider(); cred = credentials("threads", "threads-oauth2"); response = type("R", (), {"status": 200, "rate_limit": {}})()
        for operation, mime, field in (("publish.image", "image/jpeg", "image_url"), ("publish.video", "video/mp4", "video_url")):
            manifest = {"expected_account_id": "42", "operation": operation, "provider_payload": {"text": "", "alt_text": "a"},
                        "assets": [{"url": "https://cdn.test/a", "mime": mime}], "_resume_state": {}}
            with patch.object(provider, "read", return_value={"data": {"status": "FINISHED"}}), patch.object(threads, "graph_call", side_effect=[(response, {"id": "c"}), (response, {"id": "p"})]) as graph:
                provider.publish(cred, manifest, lambda _: None)
            self.assertIn(field, graph.call_args_list[0].kwargs["form"])
        assets = [{"url": "https://cdn.test/1", "mime": "image/jpeg"}, {"url": "https://cdn.test/2", "mime": "video/mp4"}]
        normalized = provider.normalize_publish("publish.carousel", {"text": "caption"}, [{"kind": "remote", **a} for a in assets])
        self.assertEqual(normalized["text"], "caption")

    def test_carousel_resume_reuses_checkpointed_children(self):
        provider = threads.ThreadsProvider(); cred = credentials("threads", "threads-oauth2")
        assets = [{"url": "https://cdn.test/1", "mime": "image/jpeg"}, {"url": "https://cdn.test/2", "mime": "video/mp4"}]
        state = {"stage": "creating_children", "child_container_ids": ["101"], "next_child_index": 1,
                 "provider_status": "creating_children", "final_publish_started": False}
        manifest = {"expected_account_id": "42", "operation": "publish.carousel", "provider_payload": {"text": "caption", "alt_text": ""},
                    "assets": assets, "_resume_state": state}
        response = type("R", (), {"status": 200, "rate_limit": {}})(); ids = iter(("102", "201", "301")); forms = []
        def graph(*_args, **kwargs): forms.append(kwargs.get("form", {})); return response, {"id": next(ids)}
        with patch.object(provider, "read", return_value={"data": {"status": "FINISHED"}}), patch.object(threads, "graph_call", side_effect=graph):
            result = provider.publish(cred, manifest, lambda _: None)
        self.assertEqual(result["provider_id"], "301"); self.assertEqual(forms[0]["video_url"], "https://cdn.test/2")
        self.assertEqual(forms[1]["children"], "101,102")

    def test_container_timeout_is_submitted_and_reconcile_unlocks_prepublish_stage(self):
        provider = threads.ThreadsProvider(); cred = credentials("threads", "threads-oauth2")
        manifest = {"expected_account_id": "42", "operation": "publish.text", "provider_payload": {"text": "hello", "alt_text": ""},
                    "assets": [], "_resume_state": {}}
        states = []
        with patch.object(threads, "graph_call", side_effect=core.ApiFailure("timeout", outcome="unknown")), self.assertRaises(core.ApiFailure) as raised:
            provider.publish(cred, manifest, states.append)
        self.assertEqual(raised.exception.outcome, "submitted"); self.assertEqual(states[-1]["stage"], "creating_container")
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        row = {"status": "unknown", "attempted_at": old, "provider_state": states[-1]}
        with patch.object(provider, "read") as read:
            reconciled = provider.reconcile(cred, row)
        self.assertEqual(reconciled["status"], "resume_safe"); read.assert_not_called()

    def test_async_states_are_not_hidden_as_success(self):
        provider = threads.ThreadsProvider(); cred = credentials("threads", "threads-oauth2")
        manifest = {"expected_account_id": "42", "operation": "publish.text", "provider_payload": {"text": "hello", "alt_text": ""}, "assets": [], "_resume_state": {"container_id": "c1"}}
        with patch.object(provider, "read", return_value={"data": {"status": "IN_PROGRESS"}}): result = provider.publish(cred, manifest, lambda _: None)
        self.assertEqual(result["status"], "submitted")
        with patch.object(provider, "read", return_value={"data": {"status": "ERROR"}}), self.assertRaises(core.ApiFailure): provider.publish(cred, manifest, lambda _: None)

    def test_invalid_text_and_local_media_fail(self):
        provider = threads.ThreadsProvider()
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.text", {"text": ""}, [])
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.image", {}, [{"kind": "local", "mime": "image/jpeg"}])

    def test_reconcile_checks_container_but_preserves_final_post_id(self):
        provider = threads.ThreadsProvider(); row = {"provider_id": "post1", "provider_state": {"container_id": "container1", "provider_id": "post1"}}
        with patch.object(provider, "read", return_value={"data": {"status": "PUBLISHED"}}) as called:
            result = provider.reconcile(credentials("threads", "threads-oauth2"), row)
        self.assertEqual(called.call_args.args[2]["resource_id"], "container1"); self.assertEqual(result["provider_id"], "post1")


if __name__ == "__main__": unittest.main()
