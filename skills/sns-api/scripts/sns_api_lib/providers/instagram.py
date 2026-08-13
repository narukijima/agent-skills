"""Instagram Professional account adapter with explicit Login mode."""

from __future__ import annotations

from datetime import timedelta

from ..auth import bearer_credentials, provider_env
from ..core import ApiFailure, parse_time, utc_now
from .base import Provider
from .meta_common import (
    INSTAGRAM_HOST, META_HOST, META_VERSION, graph_call, graph_id, normalized, prepublish_call,
    prepublish_resume_ready, require_remote,
)

IMAGE_MIMES = {"image/jpeg"}
VIDEO_MIMES = {"video/mp4", "video/quicktime"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024


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
        _validate_media(operation, assets)
        caption = str(payload.get("caption", ""))
        if len(caption) > 2200: raise ApiFailure("Instagram caption exceeds 2200 characters", code="INVALID_CONTENT")
        return {"caption": caption, "share_to_feed": bool(payload.get("share_to_feed", False))}

    def call_plan(self, operation, payload, assets):
        calls = len(assets) + 4 if operation == "publish.carousel" else 4
        return {"max_calls": calls, "calls": ["GET identity", "POST media container(s)", "GET container status", "POST media_publish"]}

    def reconcile_call_budget(self, row): return 3

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
            result, body = graph_call(host, META_VERSION, credentials.token, "GET", graph_id(params.get("resource_id")),
                                      query={"fields": "id,status_code,status"})
        else: raise ApiFailure("unsupported Instagram read", code="UNSUPPORTED_CAPABILITY")
        return normalized(result, body)

    def _container(self, credentials, account, form):
        result, body = graph_call(self._host(), META_VERSION, credentials.token, "POST", account + "/media", form=form)
        if not body.get("id"): raise ApiFailure("Instagram container response missing id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        return str(body["id"]), result

    def publish(self, credentials, manifest, checkpoint):
        account = manifest["expected_account_id"]; operation = manifest["operation"]; payload = manifest["provider_payload"]
        state = _resume_state(manifest.get("_resume_state") or {}, operation, len(manifest["assets"]))
        container = state.get("container_id")
        if not container:
            if operation == "publish.carousel":
                children = list(state.get("child_container_ids", []))
                for index in range(len(children), len(manifest["assets"])):
                    asset = manifest["assets"][index]
                    form = {"is_carousel_item": "true"}
                    if str(asset.get("mime", "")).startswith("video/"): form.update({"media_type": "VIDEO", "video_url": asset["url"]})
                    else: form["image_url"] = asset["url"]
                    child, _ = prepublish_call(lambda form=form: self._container(credentials, account, form), state, checkpoint, "creating_children")
                    children.append(child); state.update(child_container_ids=list(children), next_child_index=len(children))
                    checkpoint(dict(state))
                parent = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": payload["caption"]}
                container, _ = prepublish_call(lambda: self._container(credentials, account, parent), state, checkpoint, "creating_parent")
            else:
                asset = manifest["assets"][0]; form = {"caption": payload["caption"]}
                if operation == "publish.image": form["image_url"] = asset["url"]
                else:
                    form.update({"media_type": "REELS" if operation == "publish.reel" else "VIDEO", "video_url": asset["url"]})
                    if operation == "publish.reel": form["share_to_feed"] = "true" if payload["share_to_feed"] else "false"
                container, _ = prepublish_call(lambda: self._container(credentials, account, form), state, checkpoint, "creating_container")
            state.update(stage="container_created", container_id=container, provider_id=container,
                         provider_status="container_created", final_publish_started=False)
            checkpoint(dict(state))
        status_result = self.read(credentials, "publish.status", {"resource_id": container})
        status_data = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        provider_status = status_data.get("status_code") or status_data.get("status")
        if provider_status not in {"FINISHED", "PUBLISHED"}:
            if provider_status in {"ERROR", "EXPIRED"}: raise ApiFailure("Instagram container failed", code="PROVIDER_ASYNC_FAILED", outcome="failed", payload=status_data)
            state.update(stage="processing", provider_status=provider_status or "IN_PROGRESS", final_publish_started=False)
            checkpoint(dict(state))
            return {"status": "submitted", "provider_id": container, "provider_status": provider_status or "IN_PROGRESS", "provider": {"container": status_data}}
        state.update(stage="ready", provider_status="ready", final_publish_started=False); checkpoint(dict(state))
        state.update(stage="final_publish_started", provider_status="final_publish_started", final_publish_started=True,
                     final_publish_started_at=utc_now()); checkpoint(dict(state))
        result, body = graph_call(self._host(), META_VERSION, credentials.token, "POST", account + "/media_publish", form={"creation_id": container})
        if not body.get("id"): raise ApiFailure("Instagram publish response missing media id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        state.update(stage="published", provider_id=str(body["id"]), provider_status="PUBLISHED", final_publish_started=True)
        checkpoint(dict(state))
        return {"status": "published", "provider_id": str(body["id"]), "provider_status": "PUBLISHED", "http_status": result.status,
                "rate_limit": result.rate_limit, "provider": {"container_id": container, "api_version": META_VERSION}}

    def reconcile(self, credentials, row):
        state = row.get("provider_state") or {}; resource = state.get("container_id") or row.get("provider_id")
        if row.get("status") == "unknown" and (not state or state.get("final_publish_started") is False) and prepublish_resume_ready(row):
            return {"status": "resume_safe", "provider": {"stage": state.get("stage"), "public_publish_started": False}}
        if not resource: return {"status": "unresolved"}
        result = self.read(credentials, "publish.status", {"resource_id": resource}); data = result.get("data") or {}
        code = data.get("status_code") or data.get("status") if isinstance(data, dict) else None
        known_final = row.get("provider_id") or state.get("provider_id")
        if code == "PUBLISHED" and known_final and str(known_final) != str(resource):
            return {"status": "confirmed_success", "provider_id": str(known_final), "provider_status": code}
        if code in {"ERROR", "EXPIRED"}: return {"status": "confirmed_absent", "provider": {"container_status": code}}
        if state.get("final_publish_started") is True:
            match = self._recent_publish_match(credentials, row, state)
            if match:
                return {"status": "confirmed_success", "provider_id": match, "provider_status": "PUBLISHED",
                        "provider": {"matched_owned_media": True, "container_status": code}}
        return {"status": "unresolved", "provider": {"container_status": code}}

    def _recent_publish_match(self, credentials, row, state):
        result = self.read(credentials, "media.read", {"limit": 100})
        if result.get("status") == "partial" or result.get("errors"):
            return None
        payload = row.get("provider_payload") or {}; expected_caption = str(payload.get("caption", ""))
        attempted = parse_time(str(state.get("final_publish_started_at") or row.get("attempted_at")), "Instagram final publish time")
        expected_types = {
            "publish.image": {"IMAGE"}, "publish.video": {"VIDEO"}, "publish.reel": {"REELS", "VIDEO"},
            "publish.carousel": {"CAROUSEL_ALBUM"},
        }.get(row.get("operation"), set())
        candidates = []
        for item in result.get("data") or []:
            if not isinstance(item, dict) or not item.get("timestamp") or str(item.get("caption", "")) != expected_caption:
                continue
            created = parse_time(str(item["timestamp"]), "Instagram media timestamp")
            native_type = str(item.get("media_product_type") or item.get("media_type") or "").upper()
            if attempted - timedelta(seconds=30) <= created <= attempted + timedelta(minutes=5) and (not expected_types or native_type in expected_types):
                if item.get("id"): candidates.append(str(item["id"]))
        return candidates[0] if len(candidates) == 1 else None


def _account(): return graph_id(provider_env("instagram", "ACCOUNT_ID", required=True), "Instagram account ID")


def _limit(value):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("limit must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 100: raise ApiFailure("Instagram limit must be 1-100", code="INVALID_PARAMETER")
    return str(number)


def _resume_state(value, operation, asset_count):
    state = dict(value)
    stage = state.get("stage")
    if stage is None and state:
        if state.get("provider_status") == "ready":
            stage = "legacy_final_publish_unknown"; state["final_publish_started"] = True
        elif state.get("container_id"):
            stage = "container_created"; state.setdefault("final_publish_started", False)
        elif state.get("child_container_ids"):
            stage = "creating_children"; state.setdefault("final_publish_started", False)
    allowed = {None, "creating_children", "creating_parent", "creating_container", "container_created",
               "processing", "ready", "final_publish_started", "published", "legacy_final_publish_unknown"}
    if stage not in allowed or state.get("final_publish_started") not in {None, False, True}:
        raise ApiFailure("Instagram provider checkpoint is invalid", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    children = state.get("child_container_ids", [])
    if not isinstance(children, list) or len(children) > asset_count or any(not str(item).isdigit() for item in children):
        raise ApiFailure("Instagram carousel checkpoint is invalid", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    if operation != "publish.carousel" and children:
        raise ApiFailure("Instagram non-carousel checkpoint has children", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    state["stage"] = stage
    return state


def _validate_media(operation, assets):
    for asset in assets:
        mime = str(asset.get("mime", "")).lower()
        video = mime.startswith("video/")
        allowed = VIDEO_MIMES if video else IMAGE_MIMES
        if mime not in allowed:
            raise ApiFailure("Instagram media must be JPEG, MP4, or MOV for this publish type", code="INVALID_MEDIA")
        size = asset.get("size")
        if size is not None:
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ApiFailure("Instagram expected media size must be a positive integer", code="INVALID_MEDIA")
            if size > (MAX_VIDEO_BYTES if video else MAX_IMAGE_BYTES):
                raise ApiFailure("Instagram expected media size exceeds the documented boundary", code="INVALID_MEDIA")
        container = str(asset.get("container", "")).lower()
        if container and container not in ({"mp4", "mov"} if video else {"jpeg", "jpg"}):
            raise ApiFailure("Instagram expected media container is unsupported", code="INVALID_MEDIA")
        codec = str(asset.get("video_codec", "")).lower().replace(".", "")
        if codec and codec not in {"h264", "hevc", "h265"}:
            raise ApiFailure("Instagram expected video codec is unsupported", code="INVALID_MEDIA")
        audio = str(asset.get("audio_codec", "")).lower()
        if audio and audio != "aac":
            raise ApiFailure("Instagram expected audio codec is unsupported", code="INVALID_MEDIA")
        fps = asset.get("fps")
        if fps is not None and (isinstance(fps, bool) or not isinstance(fps, (int, float)) or not 23 <= fps <= 60):
            raise ApiFailure("Instagram expected frame rate must be 23-60 fps", code="INVALID_MEDIA")
