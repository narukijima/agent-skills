"""Facebook Pages adapter. Personal-profile posting is intentionally absent."""

from __future__ import annotations

from ..auth import bearer_credentials
from ..core import ApiFailure
from .base import Provider
from .meta_common import META_HOST, META_VERSION, graph_call, normalized, require_remote


class FacebookProvider(Provider):
    name = "facebook"; account_type = "page"; api_version = META_VERSION
    capabilities = ("identity.read", "page.content", "publish.text", "publish.image", "publish.video", "publish.status")
    read_operations = ("identity.read", "page.content", "publish.status")
    publish_operations = ("publish.text", "publish.image", "publish.video")

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

    def identity(self, credentials):
        account = _account()
        _, body = graph_call(META_HOST, META_VERSION, credentials.token, "GET", account,
                             query={"fields": "id,name,category,tasks"})
        if str(body.get("id", "")) != account: raise ApiFailure("Facebook Page identity mismatch in provider response", code="INVALID_PROVIDER_RESPONSE")
        return {**body, "id": account, "account_type": "page"}

    def read(self, credentials, operation, params):
        account = _account()
        if operation == "identity.read": return {"data": self.identity(credentials), "provider": {"api_version": META_VERSION}}
        if operation == "page.content":
            result, body = graph_call(META_HOST, META_VERSION, credentials.token, "GET", account + "/feed",
                                      query={"fields": "id,message,created_time,permalink_url,attachments", "limit": _limit(params.get("limit", 25)), "after": params.get("after")})
        elif operation == "publish.status":
            result, body = graph_call(META_HOST, META_VERSION, credentials.token, "GET", str(params.get("resource_id", "")),
                                      query={"fields": "id,status,permalink_url,created_time"})
        else: raise ApiFailure("unsupported Facebook read", code="UNSUPPORTED_CAPABILITY")
        return normalized(result, body)

    def publish(self, credentials, manifest, checkpoint):
        account = manifest["expected_account_id"]; operation = manifest["operation"]; payload = manifest["provider_payload"]
        if operation == "publish.text": path = account + "/feed"; form = {"message": payload["message"]}
        elif operation == "publish.image": path = account + "/photos"; form = {"url": manifest["assets"][0]["url"], "caption": payload.get("caption", ""), "published": "true"}
        else: path = account + "/videos"; form = {"file_url": manifest["assets"][0]["url"], "description": payload.get("description", "")}
        result, body = graph_call(META_HOST, META_VERSION, credentials.token, "POST", path, form=form)
        provider_id = body.get("post_id") or body.get("id")
        if not provider_id: raise ApiFailure("Facebook publish response missing id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        common = "submitted" if operation == "publish.video" else "published"
        checkpoint({"provider_id": str(provider_id), "provider_status": "processing" if common == "submitted" else "published"})
        return {"status": common, "provider_id": str(provider_id), "provider_status": "processing" if common == "submitted" else "published",
                "http_status": result.status, "rate_limit": result.rate_limit, "provider": {"api_version": META_VERSION}}

    def reconcile(self, credentials, row):
        provider_id = row.get("provider_id") or (row.get("provider_state") or {}).get("provider_id")
        if not provider_id: return {"status": "unresolved", "provider": {"reason": "no stable Page object id"}}
        try: result = self.read(credentials, "publish.status", {"resource_id": provider_id})
        except ApiFailure: return {"status": "unresolved"}
        data = result.get("data") or {}
        native = data.get("status", {}) if isinstance(data, dict) else {}
        video_status = str(native.get("video_status", "")).lower() if isinstance(native, dict) else str(native).lower()
        publishing = str((native.get("publishing_phase") or {}).get("status", "")).lower() if isinstance(native, dict) else ""
        if video_status in {"ready", "published"} or publishing == "complete":
            return {"status": "confirmed_success", "provider_id": provider_id, "provider_status": video_status or publishing}
        if video_status in {"error", "failed"} or publishing in {"error", "failed"}:
            return {"status": "confirmed_absent", "provider": {"video_status": video_status, "publishing_phase": publishing}}
        return {"status": "unresolved", "provider": {"video_status": video_status, "publishing_phase": publishing}}


def _account():
    from ..auth import provider_env
    return provider_env("facebook", "PAGE_ID", required=True)


def _limit(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("limit must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 100: raise ApiFailure("Facebook limit must be 1-100", code="INVALID_PARAMETER")
    return str(number)
