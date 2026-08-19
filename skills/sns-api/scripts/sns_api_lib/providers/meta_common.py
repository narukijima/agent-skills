"""Small shared helpers for Meta-owned APIs; product semantics remain separate."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

from ..core import ApiFailure, parse_time
from ..http import classify, request
from datetime import datetime, timedelta, timezone

META_VERSION = "v26.0"
META_HOST = "graph.facebook.com"
INSTAGRAM_HOST = "graph.instagram.com"
THREADS_HOST = "graph.threads.net"
GRAPH_ID = re.compile(r"[0-9]+(?:_[0-9]+)?")
# Official Meta rate-limit error codes: platform throttling (4, 17, 32, 613) and
# Business Use Case throttling (80000-80014).
# https://developers.facebook.com/docs/graph-api/overview/rate-limiting/
RATE_LIMIT_CODES = {4, 17, 32, 613} | set(range(80000, 80015))


def _error_code(error: Any) -> Optional[int]:
    if not isinstance(error, dict):
        return None
    try:
        return int(error.get("code"))
    except (TypeError, ValueError):
        return None


def _regain_seconds(rate_limit: Dict[str, Any]) -> Optional[int]:
    """Largest estimated_time_to_regain_access (minutes) from X-Business-Use-Case-Usage."""
    raw = rate_limit.get("business_usage")
    if not raw:
        return None
    try:
        usage = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    minutes = []
    if isinstance(usage, dict):
        for entries in usage.values():
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict):
                    try:
                        minutes.append(int(entry.get("estimated_time_to_regain_access", 0)))
                    except (TypeError, ValueError):
                        continue
    return max(minutes) * 60 if minutes else None


def _classify_rate_limit(exc: ApiFailure, error: Any) -> None:
    """Reclassify an official Meta throttling error as rate_limited so the shared reset gate applies."""
    if _error_code(error) not in RATE_LIMIT_CODES:
        return
    exc.outcome = "rate_limited"
    rate_limit = exc.meta.setdefault("rate_limit", {})
    regain = _regain_seconds(rate_limit)
    if regain is not None and "retry_after" not in rate_limit:
        rate_limit["retry_after"] = str(regain)


def graph_id(value: Any, label: str = "resource_id") -> str:
    text = str(value)
    if not GRAPH_ID.fullmatch(text):
        raise ApiFailure(label + " must be a stable Meta object ID", code="INVALID_PARAMETER")
    return quote(text, safe="")


def graph_call(host: str, version: str, token: str, method: str, path: str,
               *, query: Optional[Dict[str, Any]] = None, form: Optional[Dict[str, Any]] = None):
    try:
        result = request(method, f"https://{host}/{version}/{path.lstrip('/')}", allowed_hosts={host},
                         token=token, query=query, form=form)
    except ApiFailure as exc:
        error = exc.payload.get("error") if isinstance(exc.payload, dict) else None
        _classify_rate_limit(exc, error)
        raise
    body = result.body if isinstance(result.body, dict) else {"data": result.body}
    if isinstance(body.get("error"), dict):
        exc = ApiFailure("Meta provider returned an application error", code="PROVIDER_APPLICATION_ERROR",
                         status=result.status, payload=body.get("error"), outcome="failed",
                         meta={"rate_limit": dict(result.rate_limit)})
        _classify_rate_limit(exc, body.get("error"))
        raise exc
    return result, body


def normalized(result: Any, body: Dict[str, Any], data_key: str = "data") -> Dict[str, Any]:
    return {"status": classify(body), "data": body.get(data_key, body), "errors": body.get("errors", []),
            "rate_limit": result.rate_limit, "provider": {"paging": body.get("paging", {})}}


def require_remote(assets: list[Dict[str, Any]], *, count: Optional[int] = None,
                   minimum: Optional[int] = None, maximum: Optional[int] = None,
                   media_prefix: Optional[str] = None) -> None:
    if count is not None and len(assets) != count:
        raise ApiFailure("publish operation requires exactly %d asset(s)" % count, code="INVALID_MEDIA")
    if minimum is not None and len(assets) < minimum:
        raise ApiFailure("too few assets", code="INVALID_MEDIA")
    if maximum is not None and len(assets) > maximum:
        raise ApiFailure("too many assets", code="INVALID_MEDIA")
    for asset in assets:
        if asset.get("kind") != "remote":
            raise ApiFailure("provider requires HTTPS remote media; this Skill does not host local files", code="INVALID_MEDIA")
        if media_prefix and asset.get("mime") and not str(asset["mime"]).startswith(media_prefix):
            raise ApiFailure("remote media MIME does not match publish type", code="INVALID_MEDIA")


def prepublish_call(call: Callable[[], Any], state: Dict[str, Any], checkpoint: Any, stage: str) -> Any:
    """Persist a non-public stage; an uncertain container request is safe to repeat."""
    state.update(stage=stage, final_publish_started=False, provider_status=stage)
    checkpoint(dict(state))
    try:
        return call()
    except ApiFailure as exc:
        if exc.outcome not in {"failed", "rate_limited"} and not (exc.status is not None and 400 <= exc.status < 500):
            raise ApiFailure(
                "Meta pre-publish container result is uncertain; exact-manifest resume may recreate only a non-public container",
                code=exc.code, status=exc.status, payload=exc.payload, outcome="submitted", meta=exc.meta,
            ) from exc
        raise


def prepublish_resume_ready(row: Dict[str, Any], grace_seconds: int = 300) -> bool:
    """Avoid racing a still-running request before converting unknown to resumable."""
    attempted = parse_time(str(row.get("attempted_at", "")), "attempted_at")
    return datetime.now(timezone.utc) >= attempted + timedelta(seconds=grace_seconds)


def graph_limit(value: Any, label: str) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiFailure("limit must be integer", code="INVALID_PARAMETER") from exc
    if not 1 <= number <= 100:
        raise ApiFailure(label + " limit must be 1-100", code="INVALID_PARAMETER")
    return str(number)


def container_resume_state(value: Dict[str, Any], operation: str, asset_count: int, label: str) -> Dict[str, Any]:
    """Validate one checkpointed container publish state, inferring legacy pre-stage checkpoints."""
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
        raise ApiFailure(label + " provider checkpoint is invalid", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    children = state.get("child_container_ids", [])
    if not isinstance(children, list) or len(children) > asset_count or any(not str(item).isdigit() for item in children):
        raise ApiFailure(label + " carousel checkpoint is invalid", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    if operation != "publish.carousel" and children:
        raise ApiFailure(label + " non-carousel checkpoint has children", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    state["stage"] = stage
    return state


def container_reconcile(provider: Any, credentials: Any, row: Dict[str, Any], *,
                        status_keys: tuple[str, ...], match_meta_key: str,
                        recent_match: Callable[[Any, Dict[str, Any], Dict[str, Any]], Optional[str]]) -> Dict[str, Any]:
    """Shared container-provider reconcile: resume-safe detection, container status, owned-content match."""
    state = row.get("provider_state") or {}
    resource = state.get("container_id") or row.get("provider_id")
    if row.get("status") == "unknown" and (not state or state.get("final_publish_started") is False) and prepublish_resume_ready(row):
        return {"status": "resume_safe", "provider": {"stage": state.get("stage"), "public_publish_started": False}}
    if not resource:
        return {"status": "unresolved"}
    result = provider.read(credentials, "publish.status", {"resource_id": resource})
    data = result.get("data") or {}
    code = None
    if isinstance(data, dict):
        for key in status_keys:
            if data.get(key):
                code = data[key]
                break
    known_final = row.get("provider_id") or state.get("provider_id")
    if code == "PUBLISHED" and known_final and str(known_final) != str(resource):
        return {"status": "confirmed_success", "provider_id": str(known_final), "provider_status": code}
    if code in {"ERROR", "EXPIRED"}:
        return {"status": "confirmed_absent", "provider": {"container_status": code}}
    if state.get("final_publish_started") is True:
        match = recent_match(credentials, row, state)
        if match:
            return {"status": "confirmed_success", "provider_id": match, "provider_status": "PUBLISHED",
                    "provider": {match_meta_key: True, "container_status": code}}
    return {"status": "unresolved", "provider": {"container_status": code}}


def recent_container_match(provider: Any, credentials: Any, row: Dict[str, Any], state: Dict[str, Any], *,
                           read_operation: str, text_field: str, type_keys: tuple[str, str],
                           expected_types_by_operation: Dict[str, set[str]], label: str) -> Optional[str]:
    """Match exactly one owned recent item by signed text and native type near the final publish time."""
    result = provider.read(credentials, read_operation, {"limit": 100})
    if result.get("status") == "partial" or result.get("errors"):
        return None
    expected = str((row.get("provider_payload") or {}).get(text_field, ""))
    attempted = parse_time(str(state.get("final_publish_started_at") or row.get("attempted_at")), label + " final publish time")
    expected_types = expected_types_by_operation.get(row.get("operation"), set())
    candidates = []
    for item in result.get("data") or []:
        if not isinstance(item, dict) or not item.get("timestamp") or str(item.get(text_field, "")) != expected:
            continue
        created = parse_time(str(item["timestamp"]), label + " content timestamp")
        native_type = str(item.get(type_keys[0]) or item.get(type_keys[1]) or "").upper()
        if attempted - timedelta(seconds=30) <= created <= attempted + timedelta(minutes=5) and (not expected_types or native_type in expected_types):
            if item.get("id"):
                candidates.append(str(item["id"]))
    return candidates[0] if len(candidates) == 1 else None
