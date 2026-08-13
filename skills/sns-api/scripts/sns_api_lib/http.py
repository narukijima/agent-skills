"""Authenticated HTTP boundary with host allowlists and deterministic test seams."""

from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from pathlib import Path

from .core import ApiFailure, redact

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 30


def skill_version() -> str:
    try:
        text = (Path(__file__).resolve().parents[2] / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'^\s+claudagt\.version:\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', text, re.M)
    return match.group(1) if match else "unknown"


USER_AGENT = "agent-skills-sns-api/" + skill_version()


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise HTTPError(req.full_url, code, "authenticated redirect refused", headers, fp)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


_OPENER = build_opener(RejectRedirects())


def urlopen(request: Request, timeout: int = DEFAULT_TIMEOUT):
    return _OPENER.open(request, timeout=timeout)


@dataclass
class HttpResult:
    status: int
    body: Any
    headers: Dict[str, str]

    @property
    def rate_limit(self) -> Dict[str, str]:
        result = {}
        for source, target in (
            ("x-rate-limit-limit", "limit"), ("x-rate-limit-remaining", "remaining"),
            ("x-rate-limit-reset", "reset"), ("retry-after", "retry_after"),
            ("x-app-usage", "app_usage"), ("x-page-usage", "page_usage"),
            ("x-business-use-case-usage", "business_usage"),
        ):
            if source in self.headers:
                result[target] = self.headers[source]
        return result


def _headers(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    return {str(key).lower(): str(item) for key, item in value.items()}


def _read_limited(response: Any) -> bytes:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ApiFailure("provider response exceeds size limit", code="RESPONSE_TOO_LARGE", outcome="unknown")
    return raw


def _parse(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiFailure("provider response was not valid JSON", code="INVALID_PROVIDER_JSON", outcome="unknown") from exc


def validate_url(url: str, allowed_hosts: Iterable[str], *, authenticated: bool = True) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {item.lower().rstrip(".") for item in allowed_hosts}
    if parsed.username or parsed.password:
        raise ApiFailure("userinfo is forbidden in provider URLs", code="UNSAFE_URL")
    if parsed.scheme != "https" or host not in allowed:
        test_hosts = {"localhost", "127.0.0.1", "::1"}
        test_ok = (
            os.environ.get("SNS_API_TEST_MODE") == "true"
            and os.environ.get("SNS_API_WRITE_ENABLED") != "true"
            and parsed.scheme == "http" and host in test_hosts
        )
        if not test_ok:
            kind = "credential destination" if authenticated else "provider URL"
            raise ApiFailure(kind + " is not allowlisted: " + host, code="UNSAFE_PROVIDER_HOST")


def request(method: str, url: str, *, allowed_hosts: Iterable[str], token: Optional[str] = None,
            authorization: Optional[str] = None, query: Optional[Dict[str, Any]] = None,
            json_body: Optional[Dict[str, Any]] = None, form: Optional[Dict[str, Any]] = None,
            binary: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
            timeout: int = DEFAULT_TIMEOUT) -> HttpResult:
    validate_url(url, allowed_hosts, authenticated=bool(token or authorization))
    query_value = urlencode({key: value for key, value in (query or {}).items() if value is not None}, doseq=True)
    target = url + (("&" if "?" in url else "?") + query_value if query_value else "")
    req = Request(target, method=method.upper())
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    elif authorization:
        req.add_header("Authorization", authorization)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if json_body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    elif form is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.data = urlencode(form, doseq=True).encode("utf-8")
    elif binary is not None:
        req.data = binary
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = _read_limited(response)
            body = _parse(raw)
            return HttpResult(int(response.status), body, _headers(response.headers))
    except HTTPError as exc:
        try:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            body = _parse(raw) if len(raw) <= MAX_RESPONSE_BYTES else {"error": "response too large"}
        except Exception:
            body = {"error": "unreadable provider error"}
        outcome = "rate_limited" if exc.code == 429 else "failed" if 400 <= exc.code < 500 else "unknown"
        raise ApiFailure(
            "provider returned an HTTP error", code="PROVIDER_HTTP_ERROR", status=exc.code,
            payload=redact(body), outcome=outcome,
            meta={"rate_limit": HttpResult(exc.code, {}, _headers(exc.headers)).rate_limit},
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise ApiFailure("provider request result is unknown", code="PROVIDER_RESULT_UNKNOWN", outcome="unknown") from exc


def classify(body: Any) -> str:
    if not isinstance(body, dict):
        return "success"
    errors = body.get("errors") or (body.get("error") if isinstance(body.get("error"), list) else None)
    data = body.get("data", body.get("items"))
    if errors and data not in (None, [], {}):
        return "partial"
    if errors:
        return "failed"
    if data in (None, [], {}) and not any(key in body for key in ("id", "status", "kind")):
        return "empty"
    return "success"
