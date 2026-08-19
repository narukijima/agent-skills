"""YouTube Data API v3 adapter with streamed resumable video upload."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from ..auth import bearer_credentials
from ..core import ApiFailure, parse_time
from ..http import classify, request, validate_url
from ..media import verify_assets
from .base import Provider
from . import youtube_resumable

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
HOSTS = youtube_resumable.HOSTS


def _request(*args, **kwargs):
    try:
        return request(*args, **kwargs)
    except ApiFailure as exc:
        youtube_resumable.classify_quota(exc)
        raise


class YouTubeProvider(Provider):
    name = "youtube"; account_type = "channel"; api_version = "v3"
    capabilities = ("identity.read", "video.lookup", "own.videos", "publish.video", "publish.status", "media.upload.resumable", "reconcile")
    read_operations = ("identity.read", "video.lookup", "own.videos", "publish.status")
    publish_operations = ("publish.video",)
    supports_manifest_resume = True

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
        chunks = math.ceil(int(assets[0]["size"]) / youtube_resumable.CHUNK_BYTES)
        calls = ["GET channels?mine=true", "POST resumable videos.insert (conditional)",
                 "PUT resumable status (conditional)"] + ["PUT authenticated resumable media range"] * chunks
        return {"max_calls": len(calls), "calls": calls}

    def identity(self, credentials):
        result = _request("GET", API + "/channels", allowed_hosts=HOSTS, token=credentials.token,
                         query={"part": "id,snippet,contentDetails", "mine": "true"})
        items = result.body.get("items") if isinstance(result.body, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not items[0].get("id"):
            raise ApiFailure("YouTube authenticated channel identity was not singular", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
        return {**items[0], "id": str(items[0]["id"]), "account_type": "channel"}

    def read_call_budget(self, operation, params, credentials): return 2 if operation == "own.videos" else 1

    def reconcile_call_budget(self, row): return 2

    def read(self, credentials, operation, params):
        if operation == "identity.read": data = self.identity(credentials); return {"data": data, "provider": {"api_version": "v3"}}
        if operation == "video.lookup":
            result = _request("GET", API + "/videos", allowed_hosts=HOSTS, token=credentials.token,
                             query={"part": "snippet,status,processingDetails", "id": str(params.get("ids", ""))})
        elif operation == "publish.status":
            result = _request("GET", API + "/videos", allowed_hosts=HOSTS, token=credentials.token,
                             query={"part": "status,processingDetails", "id": str(params.get("resource_id", ""))})
        elif operation == "own.videos":
            identity = self.identity(credentials)
            playlist = (((identity.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
            if not playlist: raise ApiFailure("YouTube identity missing uploads playlist", code="INVALID_PROVIDER_RESPONSE")
            result = _request("GET", API + "/playlistItems", allowed_hosts=HOSTS, token=credentials.token,
                             query={"part": "snippet,contentDetails", "playlistId": playlist,
                                    "maxResults": _max(params.get("max_results", 25)), "pageToken": params.get("page_token")})
        else: raise ApiFailure("unsupported YouTube read", code="UNSUPPORTED_CAPABILITY")
        body = result.body if isinstance(result.body, dict) else {"items": result.body}
        return {"status": classify(body), "data": body.get("items"), "errors": body.get("error", {}).get("errors", []),
                "rate_limit": result.rate_limit, "provider": {"pageInfo": body.get("pageInfo"), "nextPageToken": body.get("nextPageToken")}}

    def publish(self, credentials, manifest, checkpoint):
        asset = manifest["assets"][0]; payload = manifest["provider_payload"]
        state = _resume_state(manifest.get("_resume_state") or {})
        binding = {
            "platform": "youtube", "account_id": str(manifest.get("expected_account_id", "")),
            "intent_hash": str(manifest.get("intent_hash", "")), "asset_sha256": str(asset["sha256"]),
            "asset_size": int(asset["size"]), "asset_mime": str(asset["mime"]),
        }
        if state.get("provider_id"):
            status = self.read(credentials, "publish.status", {"resource_id": str(state["provider_id"])})
            items = status.get("data") or []
            if not items:
                raise ApiFailure("YouTube video status is not yet available", code="PROVIDER_RESULT_UNKNOWN", outcome="submitted")
            data = items[0]; processing = ((data.get("processingDetails") or {}).get("processingStatus"))
            if processing in {"failed", "terminated"}:
                raise ApiFailure("YouTube processing failed", code="PROVIDER_ASYNC_FAILED", outcome="failed", payload=data)
            common = "published" if processing == "succeeded" else "submitted"
            state.update(stage="published" if common == "published" else "processing", provider_status=processing or "uploaded")
            checkpoint(dict(state))
            return {"status": common, "provider_id": str(state["provider_id"]), "provider_status": processing or "uploaded",
                    "rate_limit": status.get("rate_limit", {}), "provider": {"video": data}}
        metadata = {"snippet": {"title": payload["title"], "description": payload["description"],
                                "tags": payload["tags"], "categoryId": payload["category_id"]},
                    "status": {"privacyStatus": payload["privacy_status"], "selfDeclaredMadeForKids": payload["made_for_kids"],
                               "containsSyntheticMedia": payload["contains_synthetic_media"]}}
        record = None
        resuming_session = bool(state.get("upload_session_handle"))
        if resuming_session:
            record = youtube_resumable.load_session(state["upload_session_handle"], binding)
        else:
            state.update(stage="session_initiating", provider_status="session_initiating", final_upload_started=False)
            checkpoint(dict(state))
            try:
                initiated = _request("POST", UPLOAD, allowed_hosts=HOSTS, token=credentials.token,
                                    query={"uploadType": "resumable", "part": "snippet,status"}, json_body=metadata,
                                    headers={"X-Upload-Content-Length": str(asset["size"]), "X-Upload-Content-Type": asset["mime"]})
            except ApiFailure as exc:
                if exc.outcome not in {"failed", "rate_limited"} and not (exc.status is not None and 400 <= exc.status < 500):
                    raise ApiFailure("YouTube session initiation may be safely repeated before media upload",
                                     code=exc.code, status=exc.status, outcome="submitted", meta=exc.meta) from exc
                raise
            session = initiated.headers.get("location")
            if not session:
                raise ApiFailure("YouTube resumable initiation missing Location", code="INVALID_PROVIDER_RESPONSE", outcome="submitted")
            validate_url(session, HOSTS, authenticated=True)
            handle, session_hash = youtube_resumable.save_session(session, binding)
            record = youtube_resumable.load_session(handle, binding)
            state.update(stage="uploading", upload_session_handle=handle, upload_session_sha256=session_hash,
                         upload_offset=0, provider_status="uploading", final_upload_started=False,
                         asset_sha256=binding["asset_sha256"], asset_size=binding["asset_size"], asset_mime=binding["asset_mime"])
            checkpoint(dict(state))

        handle = state["upload_session_handle"]
        if record.get("provider_id"):
            data = {"id": str(record["provider_id"]), "processingDetails": {"processingStatus": record.get("processing_status")}}
            uploaded_status = int(record.get("http_status", 200))
            rate_limit = {}
        else:
            session = str(record["session_url"])
            if resuming_session:
                probed = youtube_resumable.probe(session, credentials.token, int(asset["size"]))
                data = probed.body if isinstance(probed.body, dict) else {}
                offset = youtube_resumable.acknowledged_offset(probed, int(asset["size"]))
                record["upload_offset"] = offset; youtube_resumable.update_session(handle, record)
                state.update(stage="uploading", upload_offset=offset, provider_status="uploading")
                checkpoint(dict(state))
                if probed.status in {200, 201}:
                    uploaded = probed
                else:
                    uploaded = None
            else:
                offset = 0; uploaded = None; data = {}
            path = Path(asset["path"])
            while uploaded is None and offset < int(asset["size"]):
                end = min(offset + youtube_resumable.CHUNK_BYTES, int(asset["size"])) - 1
                state.update(stage="uploading", provider_status="uploading", final_upload_started=end == int(asset["size"]) - 1)
                checkpoint(dict(state))
                uploaded = youtube_resumable.upload_range(session, credentials.token, path, asset["mime"], offset, end, int(asset["size"]))
                data = uploaded.body if isinstance(uploaded.body, dict) else {}
                acknowledged = youtube_resumable.acknowledged_offset(uploaded, int(asset["size"]))
                if uploaded.status == 308 and acknowledged <= offset:
                    raise ApiFailure("YouTube resumable upload made no progress", code="INVALID_PROVIDER_RESPONSE", outcome="submitted")
                offset = acknowledged
                record["upload_offset"] = offset; youtube_resumable.update_session(handle, record)
                state.update(upload_offset=offset)
                checkpoint(dict(state))
                if uploaded.status == 308:
                    uploaded = None
            if uploaded is None:
                raise ApiFailure("YouTube upload completion remains pending", code="PROVIDER_RESULT_UNKNOWN", outcome="submitted")
            uploaded_status = uploaded.status; rate_limit = uploaded.rate_limit

        video_id = data.get("id")
        if not video_id:
            raise ApiFailure("YouTube upload completion response missing video id", code="INVALID_PROVIDER_RESPONSE",
                             status=uploaded_status, outcome="submitted")
        processing = ((data.get("processingDetails") or {}).get("processingStatus"))
        common = "published" if processing == "succeeded" else "submitted"
        record.update(provider_id=str(video_id), processing_status=processing or "uploaded", http_status=uploaded_status)
        youtube_resumable.update_session(handle, record)
        state.update(stage="published" if common == "published" else "processing", provider_id=str(video_id),
                     provider_status=processing or "uploaded", final_upload_started=True)
        # Persist the durable Provider ID before any local post-upload check: the
        # video already exists and a later local failure must never authorize a
        # second upload. Mutation remains a submitted incident for reconciliation.
        checkpoint(dict(state))
        try:
            verify_assets([asset])
        except ApiFailure as exc:
            state.update(stage="processing", provider_status="uploaded_asset_mutated")
            checkpoint(dict(state))
            raise ApiFailure("local asset changed during YouTube upload; the existing video must be reconciled",
                             code=exc.code, status=uploaded_status, outcome="submitted") from exc
        youtube_resumable.remove_session(handle)
        return {"status": common, "provider_id": str(video_id), "provider_status": processing or "uploaded",
                "http_status": uploaded_status, "rate_limit": rate_limit, "provider": {"video": data}}

    def reconcile(self, credentials, row):
        state = row.get("provider_state") or {}; video_id = row.get("provider_id") or state.get("provider_id")
        if video_id:
            result = self.read(credentials, "publish.status", {"resource_id": video_id})
            items = result.get("data") or []
            if items:
                processing = ((items[0].get("processingDetails") or {}).get("processingStatus"))
                if processing == "succeeded": return {"status": "confirmed_success", "provider_id": video_id, "provider_status": processing}
                if processing in {"failed", "terminated"}: return {"status": "confirmed_absent", "provider": {"processingStatus": processing}}
        if state.get("upload_session_handle"):
            binding = {"platform": "youtube", "account_id": str(row.get("account_id", "")),
                       "intent_hash": str(row.get("intent_hash", "")), "asset_sha256": str(state.get("asset_sha256", "")),
                       "asset_size": int(state.get("asset_size", 0)), "asset_mime": str(state.get("asset_mime", ""))}
            # Older checkpoints lack the private binding fields and remain fail-closed.
            if all(binding.values()):
                record = youtube_resumable.load_session(state["upload_session_handle"], binding)
                result = youtube_resumable.probe(str(record["session_url"]), credentials.token, int(binding["asset_size"]))
                data = result.body if isinstance(result.body, dict) else {}
                if result.status in {200, 201} and data.get("id"):
                    return {"status": "confirmed_success", "provider_id": str(data["id"]),
                            "provider_status": ((data.get("processingDetails") or {}).get("processingStatus")) or "uploaded"}
                offset = youtube_resumable.acknowledged_offset(result, int(binding["asset_size"]))
                if row.get("status") == "unknown" and _resume_ready(row):
                    return {"status": "resume_safe", "provider": {"upload_offset": offset, "session_url_private": True}}
                return {"status": "unresolved", "provider": {"upload_offset": offset}}
        if row.get("status") == "unknown" and (not state or state.get("final_upload_started") is False) and _resume_ready(row):
            return {"status": "resume_safe", "provider": {"stage": state.get("stage"), "media_upload_started": False}}
        return {"status": "unresolved", "provider": {"upload_session_hash_known": bool(state.get("upload_session_sha256"))}}


def _max(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("max_results must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 50: raise ApiFailure("YouTube max_results must be 1-50", code="INVALID_PARAMETER")
    return str(number)


def _resume_state(value: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(value)
    allowed_stages = {None, "session_initiating", "uploading", "processing", "published"}
    if state.get("stage") not in allowed_stages:
        raise ApiFailure("YouTube provider checkpoint has an invalid stage", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    handle = state.get("upload_session_handle")
    if handle is not None and not youtube_resumable.HANDLE.fullmatch(str(handle)):
        raise ApiFailure("YouTube provider checkpoint has an invalid session handle", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    if handle:
        for key in ("asset_sha256", "asset_size", "asset_mime"):
            # New states persist these bindings for credential-free audit/reconcile.
            state.setdefault(key, None)
    return state


def _resume_ready(row: Dict[str, Any], grace_seconds: int = 300) -> bool:
    return datetime.now(timezone.utc) >= parse_time(str(row.get("attempted_at", "")), "attempted_at") + timedelta(seconds=grace_seconds)
