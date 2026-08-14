"""Facebook Pages adapter. Personal-profile posting is intentionally absent."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..auth import bearer_credentials
from ..core import ApiFailure, parse_time, utc_now
from .base import Provider
from .meta_common import META_HOST, META_VERSION, graph_call, graph_id, normalized, require_remote


class FacebookProvider(Provider):
    name = "facebook"; account_type = "page"; api_version = META_VERSION
    capabilities = ("identity.read", "page.content", "publish.text", "publish.image", "publish.video", "publish.status", "manual.resolve")
    read_operations = ("identity.read", "page.content", "publish.status")
    publish_operations = ("publish.text", "publish.image", "publish.video")
    supports_manual_resolve = True

    def credentials(self, for_write, operation=""):
        return bearer_credentials("facebook", public_suffix="APP_PUBLIC_ID", auth_mode="page-token")

    def normalize_publish(self, operation, payload, assets):
        allowed = {"message", "caption", "description"}
        if set(payload) - allowed: raise ApiFailure("unsupported Facebook Page publish field", code="INVALID_CONTENT")
        if operation == "publish.text":
            if assets or not str(payload.get("message", "")).strip(): raise ApiFailure("Facebook text post requires message and no media", code="INVALID_CONTENT")
            return {"message": str(payload["message"])}
        if operation == "publish.image": require_remote(assets, count=1, media_prefix="image/")
        elif operation == "publish.video": require_remote(assets, count=1, media_prefix="video/")
        return {key: str(value) for key, value in payload.items()}

    def call_plan(self, operation, payload, assets):
        return {"max_calls": 2, "calls": ["GET /{page-id}", "POST Page publish endpoint"]}

    def reconcile_call_budget(self, row): return 2

    def identity(self, credentials):
        account = _account()
        _, body = graph_call(META_HOST, META_VERSION, credentials.token, "GET", account,
                             query={"fields": "id,name,category"})
        if str(body.get("id", "")) != account: raise ApiFailure("Facebook Page identity mismatch in provider response", code="INVALID_PROVIDER_RESPONSE")
        return {**body, "id": account, "account_type": "page"}

    def read(self, credentials, operation, params):
        account = _account()
        if operation == "identity.read": return {"data": self.identity(credentials), "provider": {"api_version": META_VERSION}}
        if operation == "page.content":
            result, body = graph_call(META_HOST, META_VERSION, credentials.token, "GET", account + "/feed",
                                      query={"fields": "id,message,created_time,permalink_url,attachments", "limit": _limit(params.get("limit", 25)), "after": params.get("after")})
        elif operation == "publish.status":
            result, body = graph_call(META_HOST, META_VERSION, credentials.token, "GET", graph_id(params.get("resource_id")),
                                      query={"fields": "id,status,permalink_url,created_time"})
        else: raise ApiFailure("unsupported Facebook read", code="UNSUPPORTED_CAPABILITY")
        return normalized(result, body)

    def publish(self, credentials, manifest, checkpoint):
        account = manifest["expected_account_id"]; operation = manifest["operation"]; payload = manifest["provider_payload"]
        if operation == "publish.text": path = account + "/feed"; form = {"message": payload["message"]}
        elif operation == "publish.image": path = account + "/photos"; form = {"url": manifest["assets"][0]["url"], "caption": payload.get("caption", ""), "published": "true"}
        else: path = account + "/videos"; form = {"file_url": manifest["assets"][0]["url"], "description": payload.get("description", "")}
        started_at = utc_now()
        checkpoint({"stage": "publish_started", "publish_started": True, "publish_started_at": started_at,
                    "provider_status": "publish_started"})
        result, body = graph_call(META_HOST, META_VERSION, credentials.token, "POST", path, form=form)
        provider_id = body.get("post_id") or body.get("id")
        if not provider_id: raise ApiFailure("Facebook publish response missing id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        common = "submitted" if operation == "publish.video" else "published"
        checkpoint({"stage": "processing" if common == "submitted" else "published", "publish_started": True,
                    "publish_started_at": started_at, "provider_id": str(provider_id),
                    "provider_status": "processing" if common == "submitted" else "published"})
        return {"status": common, "provider_id": str(provider_id), "provider_status": "processing" if common == "submitted" else "published",
                "http_status": result.status, "rate_limit": result.rate_limit, "provider": {"api_version": META_VERSION}}

    def reconcile(self, credentials, row):
        state = row.get("provider_state") or {}
        provider_id = row.get("provider_id") or state.get("provider_id")
        if not provider_id:
            # The checkpoint commits before graph_call. With no checkpoint and a
            # completed grace window, this process never reached the Page write.
            attempted = parse_time(str(row.get("attempted_at", "")), "Facebook publish attempted_at")
            if not state.get("publish_started"):
                if datetime.now(timezone.utc) >= attempted + timedelta(minutes=5):
                    return {"status": "confirmed_absent", "provider": {"page_write_started": False}}
                return {"status": "unresolved", "provider": {"reason": "Page write checkpoint grace window is open"}}
            expected = _expected_message(row)
            if not expected:
                return {"status": "unresolved", "provider": {"reason": "no stable Page object id or nonempty signed text"}}
            result = self.read(credentials, "page.content", {"limit": 100})
            items = result.get("data") or []
            anchor = state.get("publish_started_at") or row.get("attempted_at")
            attempted = parse_time(str(anchor), "Facebook publish attempted_at")
            candidates = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or not item.get("created_time"):
                    continue
                created = parse_time(str(item["created_time"]), "Facebook created_time")
                if attempted - timedelta(seconds=30) <= created <= attempted + timedelta(minutes=5) and str(item.get("message", "")) == expected:
                    candidates.append(item)
            if len(candidates) == 1 and candidates[0].get("id"):
                return {"status": "confirmed_success", "provider_id": str(candidates[0]["id"]),
                        "provider_status": "published", "provider": {"matched_recent_page_content": True}}
            return {"status": "unresolved", "provider": {"reason": "recent Page content was not conclusive", "candidate_count": len(candidates)}}
        try: result = self.read(credentials, "publish.status", {"resource_id": provider_id})
        except ApiFailure: return {"status": "unresolved"}
        data = result.get("data") or {}
        if row.get("operation") in {"publish.text", "publish.image"} and isinstance(data, dict) and str(data.get("id", "")) == str(provider_id):
            return {"status": "confirmed_success", "provider_id": str(provider_id), "provider_status": "published"}
        native = data.get("status", {}) if isinstance(data, dict) else {}
        video_status = str(native.get("video_status", "")).lower() if isinstance(native, dict) else str(native).lower()
        publishing = str((native.get("publishing_phase") or {}).get("status", "")).lower() if isinstance(native, dict) else ""
        if video_status in {"ready", "published"} or publishing == "complete":
            return {"status": "confirmed_success", "provider_id": provider_id, "provider_status": video_status or publishing}
        if video_status in {"error", "failed"} or publishing in {"error", "failed"}:
            return {"status": "confirmed_absent", "provider": {"video_status": video_status, "publishing_phase": publishing}}
        return {"status": "unresolved", "provider": {"video_status": video_status, "publishing_phase": publishing}}

    def valid_provider_id(self, value):
        return bool(value and re.fullmatch(r"[0-9]+(?:_[0-9]+)?", str(value)))


def _account():
    from ..auth import provider_env
    return graph_id(provider_env("facebook", "PAGE_ID", required=True), "Facebook Page ID")


def _limit(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("limit must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 100: raise ApiFailure("Facebook limit must be 1-100", code="INVALID_PARAMETER")
    return str(number)


def _expected_message(row):
    payload = row.get("provider_payload") or {}
    return str(payload.get("message") or payload.get("caption") or payload.get("description") or "")
