"""Instagram Professional account adapter with explicit Login mode."""

from __future__ import annotations

from ..auth import bearer_credentials, provider_env
from ..core import ApiFailure
from .base import Provider
from .meta_common import INSTAGRAM_HOST, META_HOST, META_VERSION, graph_call, normalized, require_remote


class InstagramProvider(Provider):
    name = "instagram"; account_type = "professional"; api_version = META_VERSION
    capabilities = ("identity.read", "media.read", "publish.image", "publish.video", "publish.reel", "publish.carousel", "publish.status")
    read_operations = ("identity.read", "media.read", "publish.status")
    publish_operations = ("publish.image", "publish.video", "publish.reel", "publish.carousel")
    supports_manifest_resume = True

    def _mode(self):
        mode = provider_env("instagram", "AUTH_MODE", required=True)
        if mode not in {"facebook-login", "instagram-login"}: raise ApiFailure("SNS_INSTAGRAM_AUTH_MODE must be facebook-login or instagram-login", code="WRONG_AUTH_MODE")
        return mode

    def _host(self): return META_HOST if self._mode() == "facebook-login" else INSTAGRAM_HOST

    def credentials(self, for_write, operation=""):
        mode = self._mode()
        return bearer_credentials("instagram", public_suffix="APP_PUBLIC_ID", auth_mode=mode)

    def normalize_publish(self, operation, payload, assets):
        allowed = {"caption", "share_to_feed"}
        if set(payload) - allowed: raise ApiFailure("unsupported Instagram publish field", code="INVALID_CONTENT")
        if operation == "publish.image": require_remote(assets, count=1, media_prefix="image/")
        elif operation in {"publish.video", "publish.reel"}: require_remote(assets, count=1, media_prefix="video/")
        else: require_remote(assets, minimum=2, maximum=10)
        caption = str(payload.get("caption", ""))
        if len(caption) > 2200: raise ApiFailure("Instagram caption exceeds 2200 characters", code="INVALID_CONTENT")
        return {"caption": caption, "share_to_feed": bool(payload.get("share_to_feed", False))}

    def call_plan(self, operation, payload, assets):
        calls = len(assets) + 4 if operation == "publish.carousel" else 4
        return {"max_calls": calls, "calls": ["GET identity", "POST media container(s)", "GET container status", "POST media_publish"]}

    def identity(self, credentials):
        account = _account(); _, body = graph_call(self._host(), META_VERSION, credentials.token, "GET", account,
                                                   query={"fields": "id,username,account_type,media_count"})
        if str(body.get("id", "")) != account: raise ApiFailure("Instagram identity response mismatch", code="INVALID_PROVIDER_RESPONSE")
        account_type = str(body.get("account_type", "")).upper()
        if account_type and account_type not in {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}:
            raise ApiFailure("Instagram account is not Professional", code="ACCOUNT_TYPE_MISMATCH")
        return {**body, "id": account, "account_type": "professional"}

    def read(self, credentials, operation, params):
        account = _account(); host = self._host()
        if operation == "identity.read": return {"data": self.identity(credentials), "provider": {"auth_mode": self._mode(), "api_version": META_VERSION}}
        if operation == "media.read":
            result, body = graph_call(host, META_VERSION, credentials.token, "GET", account + "/media",
                                      query={"fields": "id,caption,media_type,media_product_type,permalink,timestamp,username", "limit": _limit(params.get("limit", 25)), "after": params.get("after")})
        elif operation == "publish.status":
            result, body = graph_call(host, META_VERSION, credentials.token, "GET", str(params.get("resource_id", "")),
                                      query={"fields": "id,status_code,status"})
        else: raise ApiFailure("unsupported Instagram read", code="UNSUPPORTED_CAPABILITY")
        return normalized(result, body)

    def _container(self, credentials, account, form):
        result, body = graph_call(self._host(), META_VERSION, credentials.token, "POST", account + "/media", form=form)
        if not body.get("id"): raise ApiFailure("Instagram container response missing id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        return str(body["id"]), result

    def publish(self, credentials, manifest, checkpoint):
        account = manifest["expected_account_id"]; operation = manifest["operation"]; payload = manifest["provider_payload"]
        resume = manifest.get("_resume_state") or {}; container = resume.get("container_id")
        if not container:
            if operation == "publish.carousel":
                children = []
                for asset in manifest["assets"]:
                    form = {"is_carousel_item": "true"}
                    if str(asset.get("mime", "")).startswith("video/"): form.update({"media_type": "VIDEO", "video_url": asset["url"]})
                    else: form["image_url"] = asset["url"]
                    child, _ = self._container(credentials, account, form); children.append(child)
                    checkpoint({"child_container_ids": children, "provider_status": "creating"})
                container, _ = self._container(credentials, account, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": payload["caption"]})
            else:
                asset = manifest["assets"][0]; form = {"caption": payload["caption"]}
                if operation == "publish.image": form["image_url"] = asset["url"]
                else:
                    form.update({"media_type": "REELS" if operation == "publish.reel" else "VIDEO", "video_url": asset["url"]})
                    if operation == "publish.reel": form["share_to_feed"] = "true" if payload["share_to_feed"] else "false"
                container, _ = self._container(credentials, account, form)
            checkpoint({"container_id": container, "provider_id": container, "provider_status": "container_created"})
        status_result = self.read(credentials, "publish.status", {"resource_id": container})
        status_data = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        provider_status = status_data.get("status_code") or status_data.get("status")
        if provider_status not in {"FINISHED", "PUBLISHED"}:
            if provider_status in {"ERROR", "EXPIRED"}: raise ApiFailure("Instagram container failed", code="PROVIDER_ASYNC_FAILED", outcome="failed", payload=status_data)
            checkpoint({"container_id": container, "provider_id": container, "provider_status": provider_status or "IN_PROGRESS"})
            return {"status": "submitted", "provider_id": container, "provider_status": provider_status or "IN_PROGRESS", "provider": {"container": status_data}}
        checkpoint({"container_id": container, "provider_id": container, "provider_status": "ready"})
        result, body = graph_call(self._host(), META_VERSION, credentials.token, "POST", account + "/media_publish", form={"creation_id": container})
        if not body.get("id"): raise ApiFailure("Instagram publish response missing media id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        checkpoint({"container_id": container, "provider_id": str(body["id"]), "provider_status": "PUBLISHED"})
        return {"status": "published", "provider_id": str(body["id"]), "provider_status": "PUBLISHED", "http_status": result.status,
                "rate_limit": result.rate_limit, "provider": {"container_id": container, "api_version": META_VERSION}}

    def reconcile(self, credentials, row):
        state = row.get("provider_state") or {}; resource = state.get("container_id") or row.get("provider_id")
        if not resource: return {"status": "unresolved"}
        result = self.read(credentials, "publish.status", {"resource_id": resource}); data = result.get("data") or {}
        code = data.get("status_code") or data.get("status") if isinstance(data, dict) else None
        if code == "PUBLISHED": return {"status": "confirmed_success", "provider_id": row.get("provider_id") or state.get("provider_id") or resource, "provider_status": code}
        if code in {"ERROR", "EXPIRED"}: return {"status": "confirmed_absent", "provider": {"container_status": code}}
        return {"status": "unresolved", "provider": {"container_status": code}}


def _account(): return provider_env("instagram", "ACCOUNT_ID", required=True)


def _limit(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("limit must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 100: raise ApiFailure("Instagram limit must be 1-100", code="INVALID_PARAMETER")
    return str(number)
