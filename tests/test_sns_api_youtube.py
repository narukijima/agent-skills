import tempfile
import unittest
import json
import stat
from pathlib import Path
from unittest.mock import patch

from tests.sns_api_helpers import core, credentials
from sns_api_lib import media
from sns_api_lib.http import HttpResult
from sns_api_lib.providers import youtube, youtube_resumable


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.TemporaryDirectory(); core._WORKSPACE = (Path(self.state.name), "test")

    def tearDown(self):
        core._WORKSPACE = None; self.state.cleanup()

    def test_identity_and_own_uploads_request_shape(self):
        provider = youtube.YouTubeProvider(); cred = credentials("youtube")
        channel = {"items": [{"id": "UC1", "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        playlist = {"items": [{"contentDetails": {"videoId": "v1"}}]}
        responses = [type("R", (), {"body": channel, "rate_limit": {}})(), type("R", (), {"body": playlist, "rate_limit": {}})()]
        with patch.object(youtube, "request", side_effect=responses) as called:
            result = provider.read(cred, "own.videos", {"max_results": 25})
        self.assertEqual(result["data"][0]["contentDetails"]["videoId"], "v1")
        self.assertEqual(called.call_args_list[0].kwargs["query"]["mine"], "true")
        self.assertEqual(called.call_args_list[1].kwargs["query"]["playlistId"], "UU1")

    def test_upload_is_resumable_streamed_and_returns_submitted_until_processed(self):
        provider = youtube.YouTubeProvider(); cred = credentials("youtube")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"; path.write_bytes(b"video")
            asset = media.local_asset({"path": str(path), "mime": "video/mp4"})
            manifest = {"expected_account_id": "UC1", "intent_hash": "a" * 64, "assets": [asset], "_resume_state": {},
                        "provider_payload": {"title": "T", "description": "D", "tags": [], "category_id": "22",
                        "privacy_status": "private", "made_for_kids": False, "contains_synthetic_media": False}}
            initiated = type("R", (), {"headers": {"location": "https://www.googleapis.com/upload/session"}})()
            uploaded = HttpResult(200, {"id": "v1", "processingDetails": {"processingStatus": "processing"}}, {})
            states = []
            with patch.object(youtube, "request", return_value=initiated) as start, \
                    patch.object(youtube_resumable, "upload_range", return_value=uploaded) as upload:
                result = provider.publish(cred, manifest, states.append)
            self.assertEqual(result["status"], "submitted"); self.assertEqual(result["provider_id"], "v1")
            self.assertEqual(start.call_args.kwargs["query"]["uploadType"], "resumable")
            self.assertEqual(upload.call_args.args[2], path.resolve()); self.assertEqual(states[0]["provider_status"], "session_initiating")
            self.assertTrue(all("session_url" not in state for state in states))
            session_state = next(state for state in states if state.get("upload_session_handle"))
            self.assertEqual(len(session_state["upload_session_sha256"]), 64)
            private = Path(self.state.name) / "state/sns-api/private/youtube-upload-sessions"
            self.assertEqual(list(private.glob("*.json")), [])

    def test_resumable_transport_sends_bearer_for_probe_and_media_put(self):
        class Response:
            status = 308
            headers = {"Range": "bytes=0-3"}
            def read(self, _limit): return b""
        class Connection:
            def __init__(self, *_args, **_kwargs): self.headers = []; self.sent = bytearray()
            def putrequest(self, *_args, **_kwargs): pass
            def putheader(self, key, value): self.headers.append((key, value))
            def endheaders(self): pass
            def send(self, value): self.sent.extend(value)
            def getresponse(self): return Response()
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v.mp4"; path.write_bytes(b"video")
            probe_connection = Connection(); upload_connection = Connection()
            with patch.object(youtube_resumable.http.client, "HTTPSConnection", side_effect=[probe_connection, upload_connection]):
                youtube_resumable.probe("https://www.googleapis.com/upload/session", "secret-token", 5)
                youtube_resumable.upload_range("https://www.googleapis.com/upload/session", "secret-token", path, "video/mp4", 0, 3, 5)
        for connection in (probe_connection, upload_connection):
            self.assertIn(("Authorization", "Bearer secret-token"), connection.headers)
        self.assertIn(("Content-Range", "bytes */5"), probe_connection.headers)
        self.assertIn(("Content-Range", "bytes 0-3/5"), upload_connection.headers)
        self.assertEqual(bytes(upload_connection.sent), b"vide")

    def test_private_session_is_mode_600_and_resume_uses_same_url(self):
        binding = {"platform": "youtube", "account_id": "UC1", "intent_hash": "a" * 64,
                   "asset_sha256": "b" * 64, "asset_size": 5, "asset_mime": "video/mp4"}
        handle, digest = youtube_resumable.save_session("https://www.googleapis.com/upload/session?upload_id=opaque", binding)
        path = Path(self.state.name) / "state/sns-api/private/youtube-upload-sessions" / (handle + ".json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn("upload_id=opaque", json.loads(path.read_text())["session_url"])
        loaded = youtube_resumable.load_session(handle, binding)
        self.assertEqual(loaded["session_url"], "https://www.googleapis.com/upload/session?upload_id=opaque")
        self.assertEqual(len(digest), 64)

    def test_private_session_rejects_symlinked_canonical_state(self):
        external = Path(self.state.name) / "external"; external.mkdir()
        state = Path(self.state.name) / "state"; state.mkdir()
        (state / "sns-api").symlink_to(external, target_is_directory=True)
        binding = {"platform": "youtube", "account_id": "UC1", "intent_hash": "a" * 64,
                   "asset_sha256": "b" * 64, "asset_size": 5, "asset_mime": "video/mp4"}
        with self.assertRaises(core.ApiFailure) as raised:
            youtube_resumable.save_session("https://www.googleapis.com/upload/session", binding)
        self.assertEqual(raised.exception.code, "PRIVATE_STATE_UNSAFE")

    def test_timeout_resumes_same_private_session_from_server_range(self):
        provider = youtube.YouTubeProvider(); cred = credentials("youtube")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"; path.write_bytes(b"video")
            asset = media.local_asset({"path": str(path), "mime": "video/mp4"})
            manifest = {"expected_account_id": "UC1", "intent_hash": "a" * 64, "assets": [asset], "_resume_state": {},
                        "provider_payload": {"title": "T", "description": "", "tags": [], "category_id": "22",
                                             "privacy_status": "private", "made_for_kids": False, "contains_synthetic_media": False}}
            initiated = type("R", (), {"headers": {"location": "https://www.googleapis.com/upload/session?upload_id=one"}})()
            states = []
            with patch.object(youtube, "request", return_value=initiated), \
                    patch.object(youtube_resumable, "upload_range", side_effect=core.ApiFailure("timeout", outcome="submitted")):
                with self.assertRaises(core.ApiFailure): provider.publish(cred, manifest, states.append)
            resume_state = dict(states[-1]); manifest["_resume_state"] = resume_state
            probe = HttpResult(308, {}, {"range": "bytes=0-2"})
            complete = HttpResult(201, {"id": "v1", "processingDetails": {"processingStatus": "processing"}}, {})
            with patch.object(youtube, "request") as no_reinit, \
                    patch.object(youtube_resumable, "probe", return_value=probe) as status, \
                    patch.object(youtube_resumable, "upload_range", return_value=complete) as upload:
                result = provider.publish(cred, manifest, states.append)
        no_reinit.assert_not_called(); status.assert_called_once()
        self.assertEqual(upload.call_args.args[4:6], (3, 4))
        self.assertEqual(result["provider_id"], "v1")

    def test_post_upload_asset_mutation_preserves_video_id_and_refuses_second_upload(self):
        provider = youtube.YouTubeProvider(); cred = credentials("youtube")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"; path.write_bytes(b"video")
            asset = media.local_asset({"path": str(path), "mime": "video/mp4"})
            manifest = {"expected_account_id": "UC1", "intent_hash": "a" * 64, "assets": [asset], "_resume_state": {},
                        "provider_payload": {"title": "T", "description": "", "tags": [], "category_id": "22",
                                             "privacy_status": "private", "made_for_kids": False, "contains_synthetic_media": False}}
            initiated = type("R", (), {"headers": {"location": "https://www.googleapis.com/upload/session?upload_id=mutation"}})()
            uploaded = HttpResult(201, {"id": "v-mutated", "processingDetails": {"processingStatus": "processing"}}, {})
            states = []
            with patch.object(youtube, "request", return_value=initiated), \
                    patch.object(youtube_resumable, "upload_range", return_value=uploaded), \
                    patch.object(youtube, "verify_assets", side_effect=core.ApiFailure("changed", code="ASSET_MUTATED")), \
                    self.assertRaises(core.ApiFailure) as raised:
                provider.publish(cred, manifest, states.append)
        self.assertEqual(raised.exception.outcome, "submitted")
        self.assertEqual(states[-1]["provider_id"], "v-mutated")
        self.assertEqual(states[-1]["provider_status"], "uploaded_asset_mutated")
        manifest["_resume_state"] = states[-1]
        with patch.object(provider, "read", return_value={"data": [{"id": "v-mutated", "processingDetails": {"processingStatus": "processing"}}]}), \
                patch.object(youtube, "request") as no_new_session, \
                patch.object(youtube_resumable, "upload_range") as no_second_upload:
            resumed = provider.publish(cred, manifest, states.append)
        self.assertEqual(resumed["provider_id"], "v-mutated")
        no_new_session.assert_not_called(); no_second_upload.assert_not_called()

    def test_processing_status_is_not_equated_with_published(self):
        provider = youtube.YouTubeProvider(); cred = credentials("youtube")
        response = type("R", (), {"body": {"items": [{"id": "v1", "processingDetails": {"processingStatus": "processing"}}]}, "rate_limit": {}})()
        with patch.object(youtube, "request", return_value=response): result = provider.read(cred, "publish.status", {"resource_id": "v1"})
        self.assertEqual(result["data"][0]["processingDetails"]["processingStatus"], "processing")

    def test_asset_mutation_and_invalid_media_are_rejected(self):
        provider = youtube.YouTubeProvider()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"; path.write_bytes(b"one")
            asset = media.local_asset({"path": str(path), "mime": "video/mp4"}); path.write_bytes(b"two")
            with self.assertRaises(core.ApiFailure) as mutated: media.verify_assets([asset])
            self.assertEqual(mutated.exception.code, "ASSET_MUTATED")
            target = Path(directory) / "target.mp4"; target.write_bytes(b"video")
            link = Path(directory) / "link.mp4"; link.symlink_to(target)
            with self.assertRaises(core.ApiFailure): media.local_asset({"path": str(link), "mime": "video/mp4"})
        with self.assertRaises(core.ApiFailure): provider.normalize_publish("publish.video", {"title": "T"}, [])
        with self.assertRaises(core.ApiFailure): media.remote_asset({"kind": "remote", "url": "https://cdn.test/video.mp4?token=secret", "mime": "video/mp4"})
        with self.assertRaises(core.ApiFailure): media.remote_asset({"kind": "remote", "url": "https://cdn.test/video.mp4?X-Goog-Signature=secret", "mime": "video/mp4"})

    def test_upload_status_reconcile_distinguishes_processing_and_success(self):
        provider = youtube.YouTubeProvider(); cred = credentials("youtube"); row = {"provider_id": "v1", "provider_state": {}}
        with patch.object(provider, "read", return_value={"data": [{"processingDetails": {"processingStatus": "succeeded"}}]}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "confirmed_success")
        with patch.object(provider, "read", return_value={"data": [{"processingDetails": {"processingStatus": "processing"}}]}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "unresolved")
        with patch.object(provider, "read", return_value={"data": [{"processingDetails": {"processingStatus": "terminated"}}]}):
            self.assertEqual(provider.reconcile(cred, row)["status"], "confirmed_absent")


if __name__ == "__main__": unittest.main()
