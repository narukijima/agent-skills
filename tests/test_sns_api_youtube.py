import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.sns_api_helpers import core, credentials
from sns_api_lib import media
from sns_api_lib.providers import youtube


class YouTubeTests(unittest.TestCase):
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
            manifest = {"assets": [asset], "provider_payload": {"title": "T", "description": "D", "tags": [], "category_id": "22",
                        "privacy_status": "private", "made_for_kids": False, "contains_synthetic_media": False}}
            initiated = type("R", (), {"headers": {"location": "https://www.googleapis.com/upload/session"}})()
            uploaded = type("R", (), {"status": 200, "body": {"id": "v1", "processingDetails": {"processingStatus": "processing"}}, "rate_limit": {}})()
            states = []
            with patch.object(youtube, "request", return_value=initiated) as start, patch.object(youtube, "upload_file", return_value=uploaded) as upload:
                result = provider.publish(cred, manifest, states.append)
            self.assertEqual(result["status"], "submitted"); self.assertEqual(result["provider_id"], "v1")
            self.assertEqual(start.call_args.kwargs["query"]["uploadType"], "resumable")
            self.assertEqual(upload.call_args.args[1], path.resolve()); self.assertEqual(states[0]["provider_status"], "uploading")
            self.assertNotIn("upload_session", states[0]); self.assertEqual(len(states[0]["upload_session_sha256"]), 64)

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
