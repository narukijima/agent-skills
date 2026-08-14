"""Threads API adapter with container-aware text/media/carousel publishing."""

from __future__ import annotations

from ..auth import bearer_credentials, provider_env
from ..core import ApiFailure, utc_now
from .base import Provider
from .meta_common import (
    THREADS_HOST, container_reconcile, container_resume_state, graph_call, graph_id, graph_limit,
    normalized, prepublish_call, recent_container_match, require_remote,
)

VERSION = "v1.0"


class ThreadsProvider(Provider):
    name = "threads"; account_type = "threads-user"; api_version = VERSION
    capabilities = ("identity.read", "own.posts", "publish.text", "publish.image", "publish.video", "publish.carousel", "publish.status", "reconcile")
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

    def reconcile_call_budget(self, row): return 3

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
            result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "GET", graph_id(params.get("resource_id")),
                                      query={"fields": "id,status,error_message"})
        else: raise ApiFailure("unsupported Threads read", code="UNSUPPORTED_CAPABILITY")
        return normalized(result, body)

    def _container(self, credentials, account, form):
        result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "POST", account + "/threads", form=form)
        if not body.get("id"): raise ApiFailure("Threads container response missing id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
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
                    video = str(asset.get("mime", "")).startswith("video/")
                    form = {"media_type": "VIDEO" if video else "IMAGE", "is_carousel_item": "true",
                            "video_url" if video else "image_url": asset["url"]}
                    child, _ = prepublish_call(lambda form=form: self._container(credentials, account, form), state, checkpoint, "creating_children")
                    children.append(child); state.update(child_container_ids=list(children), next_child_index=len(children))
                    checkpoint(dict(state))
                parent = {"media_type": "CAROUSEL", "children": ",".join(children), "text": payload["text"]}
                container, _ = prepublish_call(lambda: self._container(credentials, account, parent), state, checkpoint, "creating_parent")
            else:
                form = {"media_type": "TEXT" if operation == "publish.text" else "IMAGE" if operation == "publish.image" else "VIDEO", "text": payload["text"]}
                if operation == "publish.image": form["image_url"] = manifest["assets"][0]["url"]
                if operation == "publish.video": form["video_url"] = manifest["assets"][0]["url"]
                if payload["alt_text"]: form["alt_text"] = payload["alt_text"]
                container, _ = prepublish_call(lambda: self._container(credentials, account, form), state, checkpoint, "creating_container")
            state.update(stage="container_created", container_id=container, provider_id=container,
                         provider_status="container_created", final_publish_started=False)
            checkpoint(dict(state))
        status_result = self.read(credentials, "publish.status", {"resource_id": container})
        status_data = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        code = status_data.get("status")
        if code not in {"FINISHED", "PUBLISHED"}:
            if code in {"ERROR", "EXPIRED"}: raise ApiFailure("Threads container failed", code="PROVIDER_ASYNC_FAILED", outcome="failed", payload=status_data)
            state.update(stage="processing", provider_status=code or "IN_PROGRESS", final_publish_started=False)
            checkpoint(dict(state))
            return {"status": "submitted", "provider_id": container, "provider_status": code or "IN_PROGRESS", "provider": {"container": status_data}}
        state.update(stage="ready", provider_status="ready", final_publish_started=False); checkpoint(dict(state))
        state.update(stage="final_publish_started", provider_status="final_publish_started", final_publish_started=True,
                     final_publish_started_at=utc_now()); checkpoint(dict(state))
        result, body = graph_call(THREADS_HOST, VERSION, credentials.token, "POST", account + "/threads_publish", form={"creation_id": container})
        if not body.get("id"): raise ApiFailure("Threads publish response missing post id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        state.update(stage="published", provider_id=str(body["id"]), provider_status="PUBLISHED", final_publish_started=True)
        checkpoint(dict(state))
        return {"status": "published", "provider_id": str(body["id"]), "provider_status": "PUBLISHED", "http_status": result.status,
                "rate_limit": result.rate_limit, "provider": {"container_id": container, "api_version": VERSION}}

    def reconcile(self, credentials, row):
        return container_reconcile(self, credentials, row, status_keys=("status",),
                                   match_meta_key="matched_owned_post", recent_match=self._recent_publish_match)

    def _recent_publish_match(self, credentials, row, state):
        return recent_container_match(self, credentials, row, state, read_operation="own.posts",
                                      text_field="text", type_keys=("media_type", "media_product_type"),
                                      expected_types_by_operation={
                                          "publish.text": {"TEXT_POST", "TEXT"}, "publish.image": {"IMAGE"},
                                          "publish.video": {"VIDEO"}, "publish.carousel": {"CAROUSEL"},
                                      }, label="Threads")


def _account(): return graph_id(provider_env("threads", "ACCOUNT_ID", required=True), "Threads account ID")


def _limit(value): return graph_limit(value, "Threads")


def _resume_state(value, operation, asset_count):
    return container_resume_state(value, operation, asset_count, "Threads")
