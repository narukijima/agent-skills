"""YouTube Data API v3 adapter with streamed resumable video upload."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict

from ..auth import bearer_credentials
from ..core import ApiFailure
from ..http import classify, request, upload_file, validate_url
from .base import Provider

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
HOSTS = {"www.googleapis.com", "upload.youtube.com"}


class YouTubeProvider(Provider):
    name = "youtube"; account_type = "channel"; api_version = "v3"
    capabilities = ("identity.read", "video.lookup", "own.videos", "publish.video", "publish.status", "media.upload.resumable")
    read_operations = ("identity.read", "video.lookup", "own.videos", "publish.status")
    publish_operations = ("publish.video",)

    def credentials(self, for_write, operation=""):
        return bearer_credentials("youtube", public_suffix="CLIENT_ID")

    def normalize_publish(self, operation, payload, assets):
        if len(assets) != 1 or assets[0].get("kind") != "local" or not str(assets[0].get("mime", "")).startswith("video/"):
            raise ApiFailure("YouTube publish.video requires one local video asset", code="INVALID_MEDIA")
        allowed = {"title", "description", "tags", "category_id", "privacy_status", "made_for_kids", "contains_synthetic_media"}
        if set(payload) - allowed: raise ApiFailure("unsupported YouTube publish field", code="INVALID_CONTENT")
        title = str(payload.get("title", "")).strip()
        if not title or len(title) > 100: raise ApiFailure("YouTube title must be 1-100 characters", code="INVALID_CONTENT")
        privacy = payload.get("privacy_status", "private")
        if privacy not in {"private", "public", "unlisted"}: raise ApiFailure("invalid YouTube privacy_status", code="INVALID_CONTENT")
        tags = payload.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags): raise ApiFailure("YouTube tags must be strings", code="INVALID_CONTENT")
        return {"title": title, "description": str(payload.get("description", "")), "tags": tags,
                "category_id": str(payload.get("category_id", "22")), "privacy_status": privacy,
                "made_for_kids": bool(payload.get("made_for_kids", False)),
                "contains_synthetic_media": bool(payload.get("contains_synthetic_media", False))}

    def call_plan(self, operation, payload, assets):
        return {"max_calls": 3, "calls": ["GET channels?mine=true", "POST resumable videos.insert", "PUT resumable media"]}

    def identity(self, credentials):
        result = request("GET", API + "/channels", allowed_hosts=HOSTS, token=credentials.token,
                         query={"part": "id,snippet,contentDetails", "mine": "true"})
        items = result.body.get("items") if isinstance(result.body, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not items[0].get("id"):
            raise ApiFailure("YouTube authenticated channel identity was not singular", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
        return {**items[0], "id": str(items[0]["id"]), "account_type": "channel"}

    def read_call_budget(self, operation, params, credentials): return 2 if operation == "own.videos" else 1

    def read(self, credentials, operation, params):
        if operation == "identity.read": data = self.identity(credentials); return {"data": data, "provider": {"api_version": "v3"}}
        if operation == "video.lookup":
            result = request("GET", API + "/videos", allowed_hosts=HOSTS, token=credentials.token,
                             query={"part": "snippet,status,processingDetails", "id": str(params.get("ids", ""))})
        elif operation == "publish.status":
            result = request("GET", API + "/videos", allowed_hosts=HOSTS, token=credentials.token,
                             query={"part": "status,processingDetails", "id": str(params.get("resource_id", ""))})
        elif operation == "own.videos":
            identity = self.identity(credentials)
            playlist = (((identity.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
            if not playlist: raise ApiFailure("YouTube identity missing uploads playlist", code="INVALID_PROVIDER_RESPONSE")
            result = request("GET", API + "/playlistItems", allowed_hosts=HOSTS, token=credentials.token,
                             query={"part": "snippet,contentDetails", "playlistId": playlist,
                                    "maxResults": _max(params.get("max_results", 25)), "pageToken": params.get("page_token")})
        else: raise ApiFailure("unsupported YouTube read", code="UNSUPPORTED_CAPABILITY")
        body = result.body if isinstance(result.body, dict) else {"items": result.body}
        return {"status": classify(body), "data": body.get("items"), "errors": body.get("error", {}).get("errors", []),
                "rate_limit": result.rate_limit, "provider": {"pageInfo": body.get("pageInfo"), "nextPageToken": body.get("nextPageToken")}}

    def publish(self, credentials, manifest, checkpoint):
        asset = manifest["assets"][0]; payload = manifest["provider_payload"]
        metadata = {"snippet": {"title": payload["title"], "description": payload["description"],
                                "tags": payload["tags"], "categoryId": payload["category_id"]},
                    "status": {"privacyStatus": payload["privacy_status"], "selfDeclaredMadeForKids": payload["made_for_kids"],
                               "containsSyntheticMedia": payload["contains_synthetic_media"]}}
        initiated = request("POST", UPLOAD, allowed_hosts=HOSTS, token=credentials.token,
                            query={"uploadType": "resumable", "part": "snippet,status"}, json_body=metadata,
                            headers={"X-Upload-Content-Length": str(asset["size"]), "X-Upload-Content-Type": asset["mime"]})
        session = initiated.headers.get("location")
        if not session: raise ApiFailure("YouTube resumable initiation missing Location", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
        validate_url(session, HOSTS, authenticated=False)
        checkpoint({"upload_session_sha256": hashlib.sha256(session.encode("utf-8")).hexdigest(), "provider_status": "uploading"})
        uploaded = upload_file(session, Path(asset["path"]), allowed_hosts=HOSTS, mime=asset["mime"])
        data = uploaded.body if isinstance(uploaded.body, dict) else {}
        video_id = data.get("id")
        if uploaded.status == 308 or not video_id:
            raise ApiFailure("YouTube upload completion is unknown", code="PROVIDER_RESULT_UNKNOWN", status=uploaded.status, outcome="unknown")
        processing = ((data.get("processingDetails") or {}).get("processingStatus"))
        common = "published" if processing == "succeeded" else "submitted"
        checkpoint({"provider_id": str(video_id), "provider_status": processing or "uploaded"})
        return {"status": common, "provider_id": str(video_id), "provider_status": processing or "uploaded",
                "http_status": uploaded.status, "rate_limit": uploaded.rate_limit, "provider": {"video": data}}

    def reconcile(self, credentials, row):
        state = row.get("provider_state") or {}; video_id = row.get("provider_id") or state.get("provider_id")
        if video_id:
            result = self.read(credentials, "publish.status", {"resource_id": video_id})
            items = result.get("data") or []
            if items:
                processing = ((items[0].get("processingDetails") or {}).get("processingStatus"))
                if processing == "succeeded": return {"status": "confirmed_success", "provider_id": video_id, "provider_status": processing}
                if processing in {"failed", "terminated"}: return {"status": "confirmed_absent", "provider": {"processingStatus": processing}}
        return {"status": "unresolved", "provider": {"upload_session_hash_known": bool(state.get("upload_session_sha256"))}}


def _max(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("max_results must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 50: raise ApiFailure("YouTube max_results must be 1-50", code="INVALID_PARAMETER")
    return str(number)
