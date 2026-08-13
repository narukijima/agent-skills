"""Small shared helpers for Meta-owned APIs; product semantics remain separate."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from ..core import ApiFailure, parse_time
from ..http import classify, request
from datetime import datetime, timedelta, timezone

META_VERSION = "v26.0"
META_HOST = "graph.facebook.com"
INSTAGRAM_HOST = "graph.instagram.com"
THREADS_HOST = "graph.threads.net"


def graph_call(host: str, version: str, token: str, method: str, path: str,
               *, query: Optional[Dict[str, Any]] = None, form: Optional[Dict[str, Any]] = None):
    result = request(method, f"https://{host}/{version}/{path.lstrip('/')}", allowed_hosts={host},
                     token=token, query=query, form=form)
    body = result.body if isinstance(result.body, dict) else {"data": result.body}
    if isinstance(body.get("error"), dict):
        raise ApiFailure("Meta provider returned an application error", code="PROVIDER_APPLICATION_ERROR",
                         status=result.status, payload=body.get("error"), outcome="failed")
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
