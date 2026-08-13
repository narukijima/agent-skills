"""Threads API adapter with container-aware text/media/carousel publishing."""

from __future__ import annotations

from ..auth import bearer_credentials, provider_env
from ..core import ApiFailure
from .base import Provider
from .meta_common import THREADS_HOST, graph_call, normalized, require_remote

VERSION = "v1.0"


class ThreadsProvider(Provider):
    name = "threads"; account_type = "threads-user"; api_version = VERSION
    capabilities = ("identity.read", "own.posts", "publish.text", "publish.image", "publish.video", "publish.carousel", "publish.status")
    read_operations = ("identity.read", "own.posts", "publish.status")
    publish_operations = ("publish.text", "publish.image", "publish.video", "publish.carousel")
    supports_manifest_resume = True

    def credentials(self, for_write, operation=""):
        return bearer_credentials("threads", public_suffix="APP_PUBLIC_ID", auth_mode="threads-oauth2")

    def normalize_publish(self, operation, payload, assets):
        allowed = {"text", "alt_text"}
        if set(payload) - allowed: raise ApiFailure("unsupported Threads publish field", code="INVALID_CONTENT")
        text = str(payload.get("text", ""))
        if len(text) > 500: raise ApiFailure("Threads text exceeds 500 characters", code="INVALID_CONTENT")
        if operation == "publish.text":
            if assets or not text.strip(): raise ApiFailure("Threads text publish requires text and no media", code="INVALID_CONTENT")
        elif operation == "publish.image": require_remote(assets, count=1, media_prefix="image/")
        elif operation == "publish.video": require_remote(assets, count=1, media_prefix="video/")
        else: require_remote(assets, minimum=2, maximum=20)
        return {"text": text, "alt_text": str(payload.get("alt_text", ""))}

    def call_plan(self, operation, payload, assets):
        calls = len(assets) + 4 if operation == "publish.carousel" else 4
        return {"max_calls": calls, "calls": ["GET /me", "POST /threads container(s)", "GET container status", "POST /threads_publish"]}

    def identity(self, credentials):
        _, body = graph_call(THREADS_HOST, VERSION, credentials.token, "GET", "me",
                             query={"fields": "id,username,threads_profile_picture_url,threads_biography"})
        if not body.get("id"): raise ApiFailure("Threads identity response missing id", code="INVALID_PROVIDER_RESPONSE")
        return {**body, "id": str(body["id"]), "account_type": "threads-user"}

    def read(self, credentials, operation, params):
        if operation == "identity.read": return {"data": self.identity(credentials), "provider": {"api_version": VERSION}}
        if operation == "own.posts":
            result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "GET", _account() + "/threads",
                                      query={"fields": "id,media_product_type,media_type,media_url,permalink,username,text,timestamp,shortcode,children", "limit": _limit(params.get("limit", 25)), "after": params.get("after")})
        elif operation == "publish.status":
            result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "GET", str(params.get("resource_id", "")),
                                      query={"fields": "id,status,error_message"})
        else: raise ApiFailure("unsupported Threads read", code="UNSUPPORTED_CAPABILITY")
        return normalized(result, body)

    def _container(self, credentials, account, form):
        result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "POST", account + "/threads", form=form)
        if not body.get("id"): raise ApiFailure("Threads container response missing id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        return str(body["id"]), result

    def publish(self, credentials, manifest, checkpoint):
        account = manifest["expected_account_id"]; operation = manifest["operation"]; payload = manifest["provider_payload"]
        resume = manifest.get("_resume_state") or {}; container = resume.get("container_id")
        if not container:
            if operation == "publish.carousel":
                children = []
                for asset in manifest["assets"]:
                    video = str(asset.get("mime", "")).startswith("video/")
                    form = {"media_type": "VIDEO" if video else "IMAGE", "is_carousel_item": "true",
                            "video_url" if video else "image_url": asset["url"]}
                    child, _ = self._container(credentials, account, form); children.append(child)
                    checkpoint({"child_container_ids": children, "provider_status": "creating"})
                container, _ = self._container(credentials, account, {"media_type": "CAROUSEL", "children": ",".join(children), "text": payload["text"]})
            else:
                form = {"media_type": "TEXT" if operation == "publish.text" else "IMAGE" if operation == "publish.image" else "VIDEO", "text": payload["text"]}
                if operation == "publish.image": form["image_url"] = manifest["assets"][0]["url"]
                if operation == "publish.video": form["video_url"] = manifest["assets"][0]["url"]
                if payload["alt_text"]: form["alt_text"] = payload["alt_text"]
                container, _ = self._container(credentials, account, form)
            checkpoint({"container_id": container, "provider_id": container, "provider_status": "container_created"})
        status_result = self.read(credentials, "publish.status", {"resource_id": container})
        status_data = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        code = status_data.get("status")
        if code not in {"FINISHED", "PUBLISHED"}:
            if code in {"ERROR", "EXPIRED"}: raise ApiFailure("Threads container failed", code="PROVIDER_ASYNC_FAILED", outcome="failed", payload=status_data)
            checkpoint({"container_id": container, "provider_id": container, "provider_status": code or "IN_PROGRESS"})
            return {"status": "submitted", "provider_id": container, "provider_status": code or "IN_PROGRESS", "provider": {"container": status_data}}
        checkpoint({"container_id": container, "provider_id": container, "provider_status": "ready"})
        result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "POST", account + "/threads_publish", form={"creation_id": container})
        if not body.get("id"): raise ApiFailure("Threads publish response missing post id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        checkpoint({"container_id": container, "provider_id": str(body["id"]), "provider_status": "PUBLISHED"})
        return {"status": "published", "provider_id": str(body["id"]), "provider_status": "PUBLISHED", "http_status": result.status,
                "rate_limit": result.rate_limit, "provider": {"container_id": container, "api_version": VERSION}}

    def reconcile(self, credentials, row):
        state = row.get("provider_state") or {}; resource = state.get("container_id") or row.get("provider_id")
        if not resource: return {"status": "unresolved"}
        result = self.read(credentials, "publish.status", {"resource_id": resource}); data = result.get("data") or {}
        code = data.get("status") if isinstance(data, dict) else None
        if code == "PUBLISHED": return {"status": "confirmed_success", "provider_id": row.get("provider_id") or state.get("provider_id") or resource, "provider_status": code}
        if code in {"ERROR", "EXPIRED"}: return {"status": "confirmed_absent", "provider": {"container_status": code}}
        return {"status": "unresolved", "provider": {"container_status": code}}


def _account(): return provider_env("threads", "ACCOUNT_ID", required=True)


def _limit(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("limit must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 100: raise ApiFailure("Threads limit must be 1-100", code="INVALID_PARAMETER")
    return str(number)
