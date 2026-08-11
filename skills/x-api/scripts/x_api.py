#!/usr/bin/env python3
"""Small, dependency-free X API v2 client with a guarded post path."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # non-POSIX platform: locking degrades to a warning
    fcntl = None  # type: ignore[assignment]
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_FIELDS = "created_at,description,location,public_metrics,profile_image_url,protected,url,verified"
POST_FIELDS = "created_at,conversation_id,lang,possibly_sensitive,public_metrics"

OAUTH1_VARIABLES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

# twitter-text v3 weighting: these code point ranges count 1, everything else 2.
LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_PATTERN = re.compile(
    r"(?<![@A-Za-z0-9_])(?:"
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
    r"|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
    r"(?::[0-9]{1,5})?(?:/[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*)?"
    r")",
    re.IGNORECASE,
)
CASHTAG_PATTERN = re.compile(r"(?<![A-Za-z0-9_])\$[A-Za-z][A-Za-z0-9_]*")
WEIGHTED_LIMIT = 280
MANIFEST_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 2
DEFAULT_MANIFEST_TTL_SECONDS = 900

DEFAULT_BASE_URL = "https://api.x.com"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class RejectRedirects(HTTPRedirectHandler):
    """Never forward an authenticated request to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise HTTPError(req.full_url, code, "authenticated redirect refused", headers, fp)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


_NO_REDIRECT_OPENER = build_opener(RejectRedirects())


def urlopen(request: Request, timeout: int = 30):
    """Compatibility seam for tests; production requests reject every redirect."""
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def skill_version() -> str:
    """Read metadata.claudagt.version from SKILL.md as the single source."""
    try:
        text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'^\s+claudagt\.version:\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', text, re.M)
    return match.group(1) if match else "unknown"


USER_AGENT = "agent-skills-x-api/" + skill_version()


class ApiFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        payload: Any = None,
        retry_after: Optional[str] = None,
        rate_limit_reset: Optional[str] = None,
        credential_state: Optional[str] = None,
        recovery_marker: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.retry_after = retry_after
        self.rate_limit_reset = rate_limit_reset
        self.credential_state = credential_state
        self.recovery_marker = recovery_marker
        self.outcome = outcome


def resolve_workspace_root(script_path: Optional[Path] = None) -> Tuple[Path, str]:
    """Resolve the workspace from a repository marker, never vendor depth."""
    current = (script_path or Path(__file__)).resolve().parent
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return candidate, ".git-directory"
        if marker.is_file():
            return candidate, ".git-file"
    raise ApiFailure(
        "workspace root is unavailable: no .git marker was found; "
        "refusing a vendor-depth-derived ledger path"
    )


WORKSPACE_ROOT, WORKSPACE_ROOT_RESOLUTION = resolve_workspace_root()
CANONICAL_LEDGER_PATH = WORKSPACE_ROOT / "state/x-api/x-posts.sqlite3"


def workspace_metadata() -> Dict[str, str]:
    return {
        "workspace_root": str(WORKSPACE_ROOT),
        "workspace_root_resolution": WORKSPACE_ROOT_RESOLUTION,
    }


class CredentialRotationFailure(ApiFailure):
    """OAuth refresh may have rotated, but the durable credential state is unknown."""

    def __init__(self, message: str, marker: Path) -> None:
        super().__init__(
            message,
            credential_state="reauthorization_required",
            recovery_marker=str(marker),
        )


def json_loads(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw_response": raw.decode("utf-8", errors="replace")[:2000]}


def percent_encode(value: str) -> str:
    return quote(value, safe="")


def oauth1_credentials() -> Optional[Dict[str, str]]:
    values = {name: os.environ.get(name, "") for name in OAUTH1_VARIABLES}
    oauth1_only = [name for name in OAUTH1_VARIABLES if name != "X_ACCESS_TOKEN"]
    if not any(values[name] for name in oauth1_only):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ApiFailure("OAuth 1.0a requires all of " + ", ".join(OAUTH1_VARIABLES) + "; missing: " + ", ".join(missing))
    return values


def oauth2_refresh_configuration() -> Optional[Dict[str, str]]:
    client_id = os.environ.get("X_OAUTH2_CLIENT_ID", "")
    refresh_token = os.environ.get("X_OAUTH2_REFRESH_TOKEN", "")
    store = os.environ.get("X_OAUTH2_TOKEN_STORE", "")
    if not client_id and not refresh_token and not store:
        return None
    if not client_id:
        raise ApiFailure("OAuth 2.0 refresh requires X_OAUTH2_CLIENT_ID")
    if not store:
        raise ApiFailure(
            "OAuth 2.0 refresh requires X_OAUTH2_TOKEN_STORE: rotated refresh tokens "
            "must be persisted to a private file, never printed or logged"
        )
    return {
        "client_id": client_id,
        "client_secret": os.environ.get("X_OAUTH2_CLIENT_SECRET", ""),
        "refresh_token": refresh_token,
        "store": store,
    }


def _atomic_write_private_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()) + "." + secrets.token_hex(8))
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_private_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_write_private_json(path, value)


def write_refresh_marker(path: Path, value: Dict[str, Any]) -> None:
    _atomic_write_private_json(path, value)


def clear_private_marker(path: Path) -> None:
    path.unlink()
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_json_object(path: Path, label: str) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiFailure("invalid " + label + ": " + str(path)) from exc
    if not isinstance(loaded, dict):
        raise ApiFailure("invalid " + label + ": " + str(path))
    return loaded


@contextmanager
def file_lock(path: Path, purpose: str, required: bool = True):
    """Hold an advisory lock for a complete read/modify/write transaction."""
    if fcntl is None:
        if required:
            raise ApiFailure(purpose + " locking is unavailable on this platform; refusing unsafe concurrent access")
        print("warning: " + purpose + " locking is unavailable; do not run concurrently", file=sys.stderr)
        yield
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        with lock_path.open("a+", encoding="utf-8") as handle:
            os.chmod(lock_path, 0o600)
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise ApiFailure("could not lock " + purpose + ": " + str(exc)) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except ApiFailure:
        raise
    except OSError as exc:
        raise ApiFailure("could not open lock for " + purpose + ": " + str(exc)) from exc


def oauth2_refreshed_access_token(config: Dict[str, str]) -> str:
    """Return a valid access token, refreshing and rotating through the store file.

    X rotates the refresh token on every use, so the new one is written back to
    X_OAUTH2_TOKEN_STORE atomically (0600). Tokens are never printed.
    """
    store_path = Path(config["store"])
    marker_path = store_path.with_name(store_path.name + ".refresh-pending")
    with file_lock(store_path, "OAuth 2.0 token store"):
        stored = read_json_object(store_path, "OAuth 2.0 token store")
        marker = read_json_object(marker_path, "OAuth 2.0 refresh marker")
        if marker:
            marker_rotation = marker.get("rotation_id")
            if marker_rotation and stored.get("last_rotation_id") == marker_rotation:
                try:
                    clear_private_marker(marker_path)
                except OSError:
                    pass  # The committed store proves this marker is stale.
            else:
                raise CredentialRotationFailure(
                    "OAuth 2.0 refresh state is unresolved; a refresh may have rotated without a durable store update. Re-authorization is required",
                    marker_path,
                )
        access_token = stored.get("access_token", "")
        expires_at = stored.get("access_token_expires_at", 0)
        if access_token and isinstance(expires_at, (int, float)) and time.time() < float(expires_at) - 60:
            return access_token
        refresh_token = stored.get("refresh_token") or config["refresh_token"]
        if not refresh_token:
            raise ApiFailure("no refresh token available in X_OAUTH2_TOKEN_STORE or X_OAUTH2_REFRESH_TOKEN")
        rotation_id = secrets.token_hex(16)
        try:
            write_refresh_marker(
                marker_path,
                {"schema_version": 1, "state": "refresh_pending", "rotation_id": rotation_id, "started_at": utc_now()},
            )
        except OSError as exc:
            raise ApiFailure("could not persist OAuth 2.0 refresh intent; no refresh request was sent") from exc
        fields = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        request = Request(resolve_base_url() + "/2/oauth2/token", method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("User-Agent", USER_AGENT)
        if config["client_secret"]:
            basic = base64.b64encode((config["client_id"] + ":" + config["client_secret"]).encode("utf-8")).decode("ascii")
            request.add_header("Authorization", "Basic " + basic)
        else:
            fields["client_id"] = config["client_id"]
        request.data = urlencode(fields).encode("utf-8")
        try:
            with urlopen(request, timeout=30) as response:
                payload = json_loads(response.read())
        except HTTPError as exc:
            payload = json_loads(exc.read())
            if 400 <= exc.code < 500:
                try:
                    clear_private_marker(marker_path)
                except OSError:
                    pass
                raise ApiFailure("OAuth 2.0 token refresh was rejected", exc.code, payload) from exc
            raise CredentialRotationFailure(
                "OAuth 2.0 refresh returned a server error after dispatch; the refresh token state is unknown. Re-authorization is required",
                marker_path,
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise CredentialRotationFailure(
                "OAuth 2.0 refresh result is unknown; the refresh token may have rotated. Re-authorization is required",
                marker_path,
            ) from exc
        access_token = payload.get("access_token", "") if isinstance(payload, dict) else ""
        if not access_token:
            raise CredentialRotationFailure(
                "OAuth 2.0 refresh response did not contain an access token; the refresh token may have rotated. Re-authorization is required",
                marker_path,
            )
        new_store = {
            "access_token": access_token,
            "access_token_expires_at": time.time() + float(payload.get("expires_in") or 7200),
            "refresh_token": payload.get("refresh_token") or refresh_token,
            "last_rotation_id": rotation_id,
            "updated_at": utc_now(),
        }
        try:
            write_private_json(store_path, new_store)
        except OSError as exc:
            raise CredentialRotationFailure(
                "OAuth 2.0 credential rotated but was not persisted; re-authorization is required",
                marker_path,
            ) from exc
        try:
            clear_private_marker(marker_path)
        except OSError:
            pass  # next run recognizes last_rotation_id and clears safely
        return access_token


def require_user_credentials() -> Tuple[str, Dict[str, str]]:
    credentials = oauth1_credentials()
    if credentials is not None:
        return "oauth1", credentials
    oauth2_config = oauth2_refresh_configuration()
    if oauth2_config is not None:
        return "oauth2", {
            "X_ACCESS_TOKEN": oauth2_refreshed_access_token(oauth2_config),
            "X_APP_PUBLIC_ID": oauth2_config["client_id"],
        }
    token = os.environ.get("X_ACCESS_TOKEN", "")
    if token:
        return "oauth2", {
            "X_ACCESS_TOKEN": token,
            "X_APP_PUBLIC_ID": os.environ.get("X_OAUTH2_STATIC_CLIENT_ID", ""),
        }
    raise ApiFailure(
        "user-context auth requires one of: the OAuth 1.0a variables "
        + ", ".join(OAUTH1_VARIABLES)
        + " (usually no fixed expiry but revocable); the OAuth 2.0 refresh variables "
        "X_OAUTH2_CLIENT_ID + X_OAUTH2_TOKEN_STORE (+ X_OAUTH2_REFRESH_TOKEN to bootstrap); "
        "or a pre-issued OAuth 2.0 user token in X_ACCESS_TOKEN"
    )


def oauth1_authorization(
    method: str,
    url: str,
    params: Optional[Dict[str, str]],
    credentials: Dict[str, str],
    nonce: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    oauth_params = {
        "oauth_consumer_key": credentials["X_API_KEY"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": credentials["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    signature_params = dict(params or {})
    signature_params.update(oauth_params)
    encoded_pairs = sorted((percent_encode(key), percent_encode(value)) for key, value in signature_params.items())
    parameter_string = "&".join(key + "=" + value for key, value in encoded_pairs)
    base_string = "&".join([method.upper(), percent_encode(url), percent_encode(parameter_string)])
    signing_key = percent_encode(credentials["X_API_SECRET"]) + "&" + percent_encode(credentials["X_ACCESS_TOKEN_SECRET"])
    digest = hmac.new(signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("ascii")
    header_pairs = sorted(oauth_params.items())
    return "OAuth " + ", ".join(percent_encode(key) + '="' + percent_encode(value) + '"' for key, value in header_pairs)


def bearer_token() -> str:
    value = os.environ.get("X_BEARER_TOKEN", "")
    if not value:
        raise ApiFailure("missing required environment variable: X_BEARER_TOKEN")
    return value


def authorization_for(auth_kind: str, method: str, url: str, params: Optional[Dict[str, str]]) -> str:
    if auth_kind == "app":
        return "Bearer " + bearer_token()
    mode, credentials = require_user_credentials()
    if mode == "oauth1":
        return oauth1_authorization(method, url, params, credentials)
    return "Bearer " + credentials["X_ACCESS_TOKEN"]


def authorization_for_credentials(method: str, url: str, params: Optional[Dict[str, str]], user_credentials: Tuple[str, Dict[str, str]]) -> str:
    mode, credentials = user_credentials
    if mode == "oauth1":
        return oauth1_authorization(method, url, params, credentials)
    return "Bearer " + credentials["X_ACCESS_TOKEN"]


def choose_auth(operation: str, requested: str) -> str:
    if operation in {"me", "send", "reconcile"} and requested == "app":
        raise ApiFailure(operation + " requires user-context authentication; do not use --auth app")
    if requested in {"user", "app"}:
        return requested
    if operation in {"me", "send", "reconcile"}:
        return "user"
    if os.environ.get("X_BEARER_TOKEN"):
        return "app"
    return "user"


def resolve_base_url() -> str:
    override = os.environ.get("X_API_BASE_URL", "").rstrip("/")
    if not override or override == DEFAULT_BASE_URL:
        return DEFAULT_BASE_URL
    host = urlsplit(override).hostname or ""
    if host not in LOOPBACK_HOSTS:
        raise ApiFailure(
            "X_API_BASE_URL override is limited to loopback test servers "
            "(credentials would be sent to that host); refusing: " + override
        )
    if os.environ.get("X_API_TEST_MODE") != "true" or os.environ.get("X_POSTING_ENABLED") == "true":
        raise ApiFailure("loopback X_API_BASE_URL requires X_API_TEST_MODE=true and posting disabled")
    print("note: sending requests to test base URL " + override, file=sys.stderr)
    return override


def response_metadata(headers: Any) -> Dict[str, Any]:
    rate_limit = {
        "limit": headers.get("x-rate-limit-limit") if headers else None,
        "remaining": headers.get("x-rate-limit-remaining") if headers else None,
        "reset": headers.get("x-rate-limit-reset") if headers else None,
    }
    return {"rate_limit": {key: value for key, value in rate_limit.items() if value is not None}}


def api_request(
    method: str,
    path: str,
    auth_kind: str,
    params: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    user_credentials: Optional[Tuple[str, Dict[str, str]]] = None,
) -> Tuple[int, Any, Dict[str, Any]]:
    url = resolve_base_url() + path
    query = ("?" + urlencode(params)) if params else ""
    request = Request(url + query, method=method)
    authorization = (
        authorization_for_credentials(method, url, params, user_credentials)
        if auth_kind == "user" and user_credentials is not None
        else authorization_for(auth_kind, method, url, params)
    )
    request.add_header("Authorization", authorization)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    if body is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(request, timeout=30) as response:
            payload = json_loads(response.read())
            if not 200 <= response.status < 300:
                raise ApiFailure("X API returned an error", response.status, payload)
            return response.status, payload, response_metadata(response.headers)
    except HTTPError as exc:
        payload = json_loads(exc.read())
        raise ApiFailure(
            "X API returned an HTTP error",
            exc.code,
            payload,
            retry_after=exc.headers.get("retry-after"),
            rate_limit_reset=exc.headers.get("x-rate-limit-reset"),
        ) from exc
    except URLError as exc:
        raise ApiFailure("X API request result is unknown: " + str(exc.reason)) from exc
    except TimeoutError as exc:
        raise ApiFailure("X API request result is unknown: timeout") from exc


def classify_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "success"
    has_errors = bool(payload.get("errors"))
    has_data = payload.get("data") not in (None, [], {})
    if has_errors and has_data:
        return "partial"
    if has_errors:
        return "failed"
    if not has_data:
        return "empty"
    return "success"


def fetch(method: str, path: str, auth_kind: str, params: Optional[Dict[str, str]] = None) -> Any:
    status, payload, metadata = api_request(method, path, auth_kind, params)
    provider_payload = payload if isinstance(payload, dict) else {"data": payload}
    return {
        "status": classify_payload(payload),
        "data": provider_payload.get("data"),
        "errors": provider_payload.get("errors", []),
        "meta": provider_payload.get("meta", {}),
        "includes": provider_payload.get("includes", {}),
        "_meta": {
            "endpoint": path,
            "http_status": status,
            "requested_at": utc_now(),
            "auth_mode": auth_kind,
            **metadata,
        },
    }


def print_json(value: Any, pretty: bool, stream: Any = None) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty), file=stream or sys.stdout)


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text.rstrip("\n"))


def content_sha256(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _url_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for match in URL_PATTERN.finditer(text):
        end = match.end()
        while end > match.start():
            candidate = text[match.start():end]
            trailing = text[end - 1]
            if trailing in ".,!?;:>'\"\u3001\u3002\uff01\uff1f\uff0c":
                end -= 1
            elif trailing == ")" and candidate.count(")") > candidate.count("("):
                end -= 1
            elif trailing == "]" and candidate.count("]") > candidate.count("["):
                end -= 1
            elif trailing == "}" and candidate.count("}") > candidate.count("{"):
                end -= 1
            elif trailing in "\uff09\uff3d\uff5d":
                end -= 1
            else:
                break
        if end > match.start():
            spans.append((match.start(), end))
    return spans


def _url_values(text: str) -> List[str]:
    return [text[start:end] for start, end in _url_spans(text)]


def is_quote_target_url(value: str) -> bool:
    candidate = value if "://" in value else "https://" + value
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not (host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com")):
        return False
    return bool(re.fullmatch(r"/(?:[A-Za-z0-9_]+|i/web)/status/[0-9]+/?", parsed.path))


def _is_emoji(codepoint: int) -> bool:
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2300 <= codepoint <= 0x23FF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
    )


def _emoji_cluster_end(text: str, start: int) -> int:
    codepoint = ord(text[start])
    if text[start] in "#*0123456789":
        index = start + 1
        if index < len(text) and text[index] == "\ufe0f":
            index += 1
        return index + 1 if index < len(text) and text[index] == "\u20e3" else start
    if not _is_emoji(codepoint):
        return start
    index = start + 1
    if 0x1F1E6 <= codepoint <= 0x1F1FF and index < len(text) and 0x1F1E6 <= ord(text[index]) <= 0x1F1FF:
        return index + 1
    while index < len(text) and (text[index] in {"\ufe0e", "\ufe0f", "\u20e3"} or 0x1F3FB <= ord(text[index]) <= 0x1F3FF):
        index += 1
    while index < len(text) and (0xE0020 <= ord(text[index]) <= 0xE007E or ord(text[index]) == 0xE007F):
        index += 1
    while index + 1 < len(text) and text[index] == "\u200d" and _is_emoji(ord(text[index + 1])):
        index += 2
        while index < len(text) and (text[index] in {"\ufe0e", "\ufe0f", "\u20e3"} or 0x1F3FB <= ord(text[index]) <= 0x1F3FF):
            index += 1
        while index < len(text) and (0xE0020 <= ord(text[index]) <= 0xE007E or ord(text[index]) == 0xE007F):
            index += 1
    return index


def weighted_length(text: str) -> int:
    """Count normalized text using twitter-text v3 weights and emoji clusters."""
    normalized = normalize_text(text)
    spans = iter(_url_spans(normalized))
    current_span = next(spans, None)
    total = 0
    index = 0
    while index < len(normalized):
        if current_span and index == current_span[0]:
            total += 23
            index = current_span[1]
            current_span = next(spans, None)
            continue
        cluster_end = _emoji_cluster_end(normalized, index)
        if cluster_end > index:
            total += 2
            index = cluster_end
            continue
        codepoint = ord(normalized[index])
        total += 1 if any(low <= codepoint <= high for low, high in LIGHT_RANGES) else 2
        index += 1
    return total


def validate_post_text(text: str) -> Dict[str, Any]:
    normalized = normalize_text(text)
    errors: List[str] = []
    if not normalized.strip():
        errors.append("TEXT_EMPTY")
    emoji_format_indices = set()
    index = 0
    while index < len(normalized):
        cluster_end = _emoji_cluster_end(normalized, index)
        if cluster_end > index:
            emoji_format_indices.update(range(index, cluster_end))
            index = cluster_end
        else:
            index += 1
    if any(
        unicodedata.category(char).startswith("C")
        and char not in {"\n", "\t"}
        and index not in emoji_format_indices
        for index, char in enumerate(normalized)
    ):
        errors.append("CONTROL_CHARACTER")
    weight = weighted_length(normalized)
    if weight > WEIGHTED_LIMIT:
        errors.append("TEXT_TOO_LONG")
    cashtag_count = len(CASHTAG_PATTERN.findall(normalized))
    if cashtag_count > 1:
        errors.append("TOO_MANY_CASHTAGS")
    urls = _url_values(normalized)
    quote_targets = [value for value in urls if is_quote_target_url(value)]
    if quote_targets:
        errors.append("UNDECLARED_QUOTE_TARGET")
    return {
        "valid": not errors,
        "errors": errors,
        "text": normalized,
        "text_length": len(normalized),
        "weighted_length": weight,
        "weighted_limit": WEIGHTED_LIMIT,
        "url_count": len(urls),
        "quote_target_count": len(quote_targets),
        "quote_targets": quote_targets,
        "cashtag_count": cashtag_count,
        "content_sha256": content_sha256(normalized),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ApiFailure("invalid " + label) from exc


def _manifest_digest(manifest: Dict[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key not in {"manifest_sha256", "manifest_hmac_sha256"}}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_signing_key() -> bytes:
    value = os.environ.get("X_API_MANIFEST_SIGNING_KEY", "")
    if len(value.encode("utf-8")) < 32:
        raise ApiFailure("X_API_MANIFEST_SIGNING_KEY must be a gateway-owned secret of at least 32 bytes")
    return value.encode("utf-8")


def _manifest_signature(manifest: Dict[str, Any], key: bytes) -> str:
    signed = {key_name: value for key_name, value in manifest.items() if key_name != "manifest_hmac_sha256"}
    encoded = json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def app_credential_fingerprint(auth_mode: str, public_id: str) -> str:
    if not public_id:
        raise ApiFailure("user credential app identity is unavailable; static OAuth 2.0 sends require X_OAUTH2_STATIC_CLIENT_ID")
    return hashlib.sha256((auth_mode + ":" + public_id).encode("utf-8")).hexdigest()


def configured_app_fingerprint(user_credentials: Tuple[str, Dict[str, str]]) -> str:
    mode, credentials = user_credentials
    public_id = credentials.get("X_API_KEY", "") if mode == "oauth1" else credentials.get("X_APP_PUBLIC_ID", "")
    return app_credential_fingerprint(mode, public_id)


def prepare_manifest(args: argparse.Namespace) -> Any:
    if args.text is not None and args.file is not None:
        raise ApiFailure("use either --text or --file, not both")
    if args.text is None and args.file is None:
        raise ApiFailure("prepare requires --text or --file")
    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    validation = validate_post_text(text)
    if not validation["valid"]:
        raise ApiFailure("post validation failed: " + ", ".join(validation["errors"]), payload=validation)
    created = datetime.now(timezone.utc)
    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "content_id": args.content_id,
        "app_id": args.app_id,
        "expected_app_fingerprint": args.expected_app_fingerprint,
        "expected_user_id": args.expected_user_id,
        "approval_id": args.approval_id,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "expires_at": (created + timedelta(seconds=args.expires_in)).isoformat().replace("+00:00", "Z"),
        "text": validation["text"],
        "content_sha256": validation["content_sha256"],
        "weighted_length": validation["weighted_length"],
        "budget": {"max_api_calls": 3, "calls": ["POST /2/oauth2/token (conditional)", "GET /2/users/me", "POST /2/tweets"]},
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    manifest["manifest_hmac_sha256"] = _manifest_signature(manifest, manifest_signing_key())
    write_private_json(Path(args.manifest), manifest)
    return {
        "status": "prepared",
        "manifest": str(Path(args.manifest)),
        "validation": validation,
        **manifest,
        **workspace_metadata(),
    }


def load_manifest(path: Path, allow_expired: bool = False) -> Dict[str, Any]:
    manifest = read_json_object(path, "post manifest")
    required = {
        "schema_version", "content_id", "app_id", "expected_app_fingerprint", "expected_user_id", "approval_id",
        "created_at", "expires_at", "text", "content_sha256", "weighted_length", "budget", "manifest_sha256", "manifest_hmac_sha256",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ApiFailure("post manifest is missing: " + ", ".join(missing))
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ApiFailure("unsupported post manifest schema_version")
    if not all(isinstance(manifest[key], str) and manifest[key] for key in ("content_id", "app_id", "expected_app_fingerprint", "expected_user_id", "approval_id", "text")):
        raise ApiFailure("post manifest contains an empty identity, approval, content, or text field")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest["expected_app_fingerprint"])):
        raise ApiFailure("expected_app_fingerprint must be a SHA-256 hex digest")
    if not str(manifest["expected_user_id"]).isdigit():
        raise ApiFailure("expected_user_id must be a stable numeric X user ID")
    if not secrets.compare_digest(str(manifest["manifest_sha256"]), _manifest_digest(manifest)):
        raise ApiFailure("post manifest integrity check failed")
    if not secrets.compare_digest(str(manifest["manifest_hmac_sha256"]), _manifest_signature(manifest, manifest_signing_key())):
        raise ApiFailure("post manifest approval signature check failed")
    validation = validate_post_text(str(manifest["text"]))
    if not validation["valid"] or validation["content_sha256"] != manifest["content_sha256"] or validation["weighted_length"] != manifest["weighted_length"]:
        raise ApiFailure("post manifest text validation or hash check failed", payload=validation)
    budget = manifest.get("budget")
    expected_calls = ["POST /2/oauth2/token (conditional)", "GET /2/users/me", "POST /2/tweets"]
    if not isinstance(budget, dict) or budget.get("max_api_calls") != 3 or budget.get("calls") != expected_calls:
        raise ApiFailure("post manifest must authorize exactly the refresh-if-needed, identity, and send plan")
    if not allow_expired and datetime.now(timezone.utc) >= parse_timestamp(str(manifest["expires_at"]), "manifest expires_at"):
        raise ApiFailure("post manifest has expired")
    return manifest


def require_budget(variable: str, required_calls: int, exact: bool = False) -> None:
    raw = os.environ.get(variable, "")
    try:
        allowed = int(raw)
    except ValueError as exc:
        raise ApiFailure(variable + " must be an integer call budget") from exc
    if exact and allowed != required_calls:
        raise ApiFailure(variable + " must equal exactly " + str(required_calls) + " API calls")
    if not exact and allowed < required_calls:
        raise ApiFailure(variable + " must allow at least " + str(required_calls) + " API call(s)")


def require_read_budget(required_calls: int = 1) -> None:
    if os.environ.get("X_API_READ_ENABLED") != "true":
        raise ApiFailure("paid X API reads require X_API_READ_ENABLED=true")
    require_budget("X_API_READ_MAX_CALLS", required_calls)


def reserve_daily_calls(kind: str, planned_calls: int) -> Dict[str, Any]:
    project_id = os.environ.get("X_API_PROJECT_ID", "")
    agent_id = os.environ.get("X_API_AGENT_ID", "")
    variable = "X_API_DAILY_" + kind.upper() + "_CALL_LIMIT"
    if not project_id or not agent_id:
        raise ApiFailure("daily API budget requires X_API_PROJECT_ID and X_API_AGENT_ID")
    try:
        daily_limit = int(os.environ.get(variable, ""))
    except ValueError as exc:
        raise ApiFailure(variable + " must be an integer") from exc
    if daily_limit < planned_calls:
        raise ApiFailure(variable + " is smaller than the planned API calls")
    path = CANONICAL_LEDGER_PATH.with_name("x-usage.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        for attempt in range(100):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, project_id TEXT NOT NULL, agent_id TEXT NOT NULL, kind TEXT NOT NULL, calls INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(day,project_id,agent_id,kind))"
                )
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    raise
                time.sleep(0.01)
        connection.execute("BEGIN IMMEDIATE")
        day = datetime.now(timezone.utc).date().isoformat()
        row = connection.execute(
            "SELECT calls FROM usage WHERE day=? AND project_id=? AND agent_id=? AND kind=?",
            (day, project_id, agent_id, kind),
        ).fetchone()
        used = int(row["calls"]) if row else 0
        if used + planned_calls > daily_limit:
            raise ApiFailure(variable + " exceeded for project / agent budget scope")
        connection.execute(
            "INSERT INTO usage(day,project_id,agent_id,kind,calls,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(day,project_id,agent_id,kind) DO UPDATE SET calls=excluded.calls, updated_at=excluded.updated_at",
            (day, project_id, agent_id, kind, used + planned_calls, utc_now()),
        )
        connection.commit()
        return {
            "day": day,
            "project_id": project_id,
            "agent_id": agent_id,
            "kind": kind,
            "reserved_calls": planned_calls,
            "used_calls": used + planned_calls,
            "daily_limit": daily_limit,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def open_ledger() -> sqlite3.Connection:
    CANONICAL_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CANONICAL_LEDGER_PATH, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        for attempt in range(100):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT OR IGNORE INTO ledger_meta(key, value) VALUES ('schema_version', '2');
                    CREATE TABLE IF NOT EXISTS intents (
                      id INTEGER PRIMARY KEY,
                      account_id TEXT NOT NULL,
                      app_id TEXT NOT NULL,
                      app_fingerprint TEXT NOT NULL,
                      content_id TEXT NOT NULL,
                      content_sha256 TEXT NOT NULL,
                      text TEXT NOT NULL,
                      approval_id TEXT NOT NULL,
                      status TEXT NOT NULL,
                      attempts INTEGER NOT NULL DEFAULT 0,
                      attempted_at TEXT,
                      updated_at TEXT NOT NULL,
                      post_id TEXT,
                      http_status INTEGER,
                      UNIQUE(account_id, content_id),
                      UNIQUE(account_id, content_sha256)
                    );
                    CREATE TABLE IF NOT EXISTS events (
                      id INTEGER PRIMARY KEY,
                      intent_id INTEGER NOT NULL REFERENCES intents(id),
                      event TEXT NOT NULL,
                      status TEXT NOT NULL,
                      recorded_at TEXT NOT NULL,
                      http_status INTEGER,
                      detail TEXT
                    );
                    """
                )
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    raise
                time.sleep(0.01)
        schema_row = connection.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
    except Exception:
        connection.close()
        raise
    if schema_row is None or schema_row["value"] != str(LEDGER_SCHEMA_VERSION):
        connection.close()
        raise ApiFailure("unsupported canonical ledger schema_version")
    return connection


def _event(connection: sqlite3.Connection, intent_id: int, event: str, status: str, http_status: Optional[int] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    connection.execute(
        "INSERT INTO events(intent_id,event,status,recorded_at,http_status,detail) VALUES (?,?,?,?,?,?)",
        (intent_id, event, status, utc_now(), http_status, json.dumps(detail, sort_keys=True) if detail else None),
    )


def reserve_attempt(manifest: Dict[str, Any]) -> int:
    connection = open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM intents WHERE account_id=? AND (content_id=? OR content_sha256=?)",
            (manifest["expected_user_id"], manifest["content_id"], manifest["content_sha256"]),
        ).fetchone()
        now = utc_now()
        unresolved = connection.execute(
            "SELECT content_id FROM intents WHERE account_id=? AND status='unknown' LIMIT 1",
            (manifest["expected_user_id"],),
        ).fetchone()
        if unresolved and (row is None or unresolved["content_id"] != row["content_id"]):
            raise ApiFailure("account has an unresolved unknown intent; reconcile it before any new post")
        if row:
            if row["content_id"] != manifest["content_id"]:
                raise ApiFailure("duplicate post refused: identical content is already registered under another content_id")
            if row["content_id"] == manifest["content_id"] and row["content_sha256"] != manifest["content_sha256"]:
                raise ApiFailure("content_id is already bound to different text")
            if row["status"] == "sent":
                raise ApiFailure("duplicate post refused: content is already marked sent")
            if row["status"] == "unknown":
                raise ApiFailure("unknown post result refused: run reconcile before any retry")
            if row["status"] in {"failed", "confirmed_absent"} and row["approval_id"] == manifest["approval_id"]:
                raise ApiFailure("retry after failed or confirmed_absent requires a new signed approval_id")
            if row["attempts"] >= 2:
                raise ApiFailure("post attempt limit reached: maximum 2 attempts")
            attempts = row["attempts"] + 1
            connection.execute(
                "UPDATE intents SET app_id=?, app_fingerprint=?, approval_id=?, status='unknown', attempts=?, attempted_at=?, updated_at=? WHERE id=?",
                (
                    manifest["app_id"], manifest["expected_app_fingerprint"], manifest["approval_id"],
                    attempts, now, now, row["id"],
                ),
            )
            intent_id = int(row["id"])
        else:
            cursor = connection.execute(
                "INSERT INTO intents(account_id,app_id,app_fingerprint,content_id,content_sha256,text,approval_id,status,attempts,attempted_at,updated_at) VALUES (?,?,?,?,?,?,?,'unknown',1,?,?)",
                (
                    manifest["expected_user_id"], manifest["app_id"], manifest["expected_app_fingerprint"], manifest["content_id"],
                    manifest["content_sha256"], manifest["text"], manifest["approval_id"], now, now,
                ),
            )
            intent_id = int(cursor.lastrowid)
        _event(connection, intent_id, "attempt", "unknown")
        connection.commit()
        return intent_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_result(intent_id: int, status: str, http_status: Optional[int] = None, post_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None, event: str = "result") -> None:
    connection = open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if status == "rate_limited":
            connection.execute("UPDATE intents SET status=?, attempts=MAX(0,attempts-1), http_status=?, updated_at=? WHERE id=?", (status, http_status, utc_now(), intent_id))
        else:
            connection.execute("UPDATE intents SET status=?, http_status=?, post_id=COALESCE(?,post_id), updated_at=? WHERE id=?", (status, http_status, post_id, utc_now(), intent_id))
        _event(connection, intent_id, event, status, http_status, detail)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def authenticated_identity(user_credentials: Tuple[str, Dict[str, str]]) -> Dict[str, Any]:
    status, payload, metadata = api_request(
        "GET", "/2/users/me", "user", params={"user.fields": "id,name,username"}, user_credentials=user_credentials
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        raise ApiFailure("authenticated identity response did not contain a user id", status, payload)
    return {"data": data, "http_status": status, **metadata}


def send_manifest(args: argparse.Namespace) -> Any:
    if os.environ.get("X_POSTING_ENABLED") != "true":
        raise ApiFailure("live posting requires X_POSTING_ENABLED=true")
    require_budget("X_API_WRITE_MAX_CALLS", 3, exact=True)
    manifest = load_manifest(Path(args.manifest))
    if os.environ.get("X_API_APP_ID", "") != manifest["app_id"]:
        raise ApiFailure("X_API_APP_ID does not match manifest app_id; refusing unbound credentials")
    budget_record = reserve_daily_calls("write", 3)
    user_credentials = require_user_credentials()
    if configured_app_fingerprint(user_credentials) != manifest["expected_app_fingerprint"]:
        raise ApiFailure("configured OAuth app credential does not match expected_app_fingerprint")
    identity = authenticated_identity(user_credentials)
    actual_user_id = str(identity["data"]["id"])
    if actual_user_id != manifest["expected_user_id"]:
        raise ApiFailure("authenticated X account does not match expected_user_id; no ledger attempt was recorded")
    intent_id = reserve_attempt(manifest)
    try:
        status, payload, metadata = api_request(
            "POST", "/2/tweets", "user", body={"text": manifest["text"]}, user_credentials=user_credentials
        )
    except ApiFailure as exc:
        result_status = "rate_limited" if exc.status == 429 else "failed" if exc.status is not None and 400 <= exc.status < 500 else "unknown"
        record_result(
            intent_id, result_status, exc.status,
            detail={"retry_after": exc.retry_after, "rate_limit_reset": exc.rate_limit_reset},
        )
        exc.outcome = result_status
        raise
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        record_result(intent_id, "unknown", status)
        raise ApiFailure("post response did not contain a post id", status, payload, outcome="unknown")
    post_id = str(data["id"])
    record_result(intent_id, "sent", status, post_id, metadata)
    return {
        "status": "sent",
        "account_id": actual_user_id,
        "app_id": manifest["app_id"],
        "content_id": manifest["content_id"],
        "content_sha256": manifest["content_sha256"],
        "ledger": str(CANONICAL_LEDGER_PATH),
        **workspace_metadata(),
        "post_id": post_id,
        "url": "https://x.com/i/web/status/" + post_id,
        "_meta": {"http_status": status, "budget": budget_record, **metadata},
    }


def validate_max_results(requested: int, minimum: int, maximum: int) -> str:
    if not minimum <= requested <= maximum:
        raise ApiFailure("max_results must be within API range %d-%d; refusing to increase or clamp the request" % (minimum, maximum))
    return str(requested)


def _ledger_intent(account_id: str, content_id: str) -> sqlite3.Row:
    connection = open_ledger()
    try:
        row = connection.execute("SELECT * FROM intents WHERE account_id=? AND content_id=?", (account_id, content_id)).fetchone()
        if row is None:
            raise ApiFailure("no canonical ledger intent matches account and content_id")
        return row
    finally:
        connection.close()


def reconcile(args: argparse.Namespace) -> Any:
    require_read_budget(3)
    row = _ledger_intent(args.expected_user_id, args.content_id)
    if row["status"] != "unknown":
        return {"status": row["status"], "content_id": args.content_id, "account_id": args.expected_user_id, "reconciled": False}
    budget_record = reserve_daily_calls("read", 3)
    user_credentials = require_user_credentials()
    if configured_app_fingerprint(user_credentials) != row["app_fingerprint"]:
        raise ApiFailure("configured OAuth app credential does not match the ledger app fingerprint")
    identity = authenticated_identity(user_credentials)
    if str(identity["data"]["id"]) != args.expected_user_id:
        raise ApiFailure("authenticated X account does not match reconciliation account")
    status, payload, metadata = api_request(
        "GET", "/2/users/" + quote(args.expected_user_id, safe="") + "/tweets", "user",
        params={"max_results": "100", "tweet.fields": "created_at", "exclude": "replies,retweets"},
        user_credentials=user_credentials,
    )
    posts = payload.get("data") if isinstance(payload, dict) else None
    attempted_at = parse_timestamp(str(row["attempted_at"]), "ledger attempted_at")
    match_window_start = attempted_at - timedelta(seconds=30)
    match_window_end = attempted_at + timedelta(minutes=5)
    match = None
    for post in posts or []:
        if not isinstance(post, dict) or not post.get("created_at"):
            continue
        created_at = parse_timestamp(str(post["created_at"]), "post created_at")
        if (
            match_window_start <= created_at <= match_window_end
            and content_sha256(str(post.get("text", ""))) == row["content_sha256"]
        ):
            match = post
            break
    if match and match.get("id"):
        post_id = str(match["id"])
        record_result(int(row["id"]), "sent", status, post_id, {"reconcile": "confirmed_success"}, event="reconcile")
        return {"status": "confirmed_success", "post_id": post_id, "url": "https://x.com/i/web/status/" + post_id, "_meta": {"budget": budget_record, **metadata}}
    timestamps = [parse_timestamp(str(post["created_at"]), "post created_at") for post in posts or [] if isinstance(post, dict) and post.get("created_at")]
    has_errors = bool(payload.get("errors")) if isinstance(payload, dict) else False
    has_urls = bool(_url_spans(str(row["text"])))
    if not has_errors and not has_urls and timestamps and min(timestamps) <= attempted_at <= max(timestamps):
        evidence = {
            "reconcile": "timeline_window_covered",
            "oldest_post_at": min(timestamps).isoformat(),
            "newest_post_at": max(timestamps).isoformat(),
            "attempted_at": attempted_at.isoformat(),
            "posts_examined": len(posts or []),
        }
        record_result(int(row["id"]), "confirmed_absent", status, detail=evidence, event="reconcile")
        return {"status": "confirmed_absent", "content_id": args.content_id, "account_id": args.expected_user_id, "_meta": {"budget": budget_record, "reconciliation": evidence, **metadata}}
    evidence = {
        "reconcile": "unresolved",
        "attempted_at": attempted_at.isoformat(),
        "posts_examined": len(posts or []),
        "partial_errors": has_errors,
        "contains_url": has_urls,
    }
    record_result(int(row["id"]), "unknown", status, detail=evidence, event="reconcile")
    return {"status": "unresolved", "content_id": args.content_id, "account_id": args.expected_user_id, "_meta": {"budget": budget_record, "reconciliation": evidence, **metadata}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read X API v2 data or prepare, send, and reconcile a guarded text post.")
    parser.add_argument("--auth", choices=["auto", "app", "user"], default="auto", help="read auth mode; send always uses user context")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("me", help="get the authenticated user")
    user = sub.add_parser("user", help="get a user by username")
    user.add_argument("--username", required=True)
    by_id = sub.add_parser("user-by-id", help="get a user by id")
    by_id.add_argument("--user-id", required=True)
    posts = sub.add_parser("posts", help="get posts by user id")
    posts.add_argument("--user-id", required=True)
    posts.add_argument("--max-results", type=int, default=10, help="5-100; out-of-range values are rejected")
    posts.add_argument("--pagination-token")
    tweet = sub.add_parser("post-by-id", help="get posts by comma-separated ids")
    tweet.add_argument("--ids", required=True)
    search = sub.add_parser("search-recent", help="search recent posts")
    search.add_argument("--query", required=True)
    search.add_argument("--max-results", type=int, default=10, help="10-100; out-of-range values are rejected")
    search.add_argument("--next-token")
    sub.add_parser("usage", help="get project usage; requires the explicit read budget gate")
    prepare = sub.add_parser("prepare", help="validate text and write a short-lived immutable post manifest")
    group = prepare.add_mutually_exclusive_group()
    group.add_argument("--text")
    group.add_argument("--file")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--content-id", required=True)
    prepare.add_argument("--expected-user-id", required=True)
    prepare.add_argument("--app-id", required=True)
    prepare.add_argument("--expected-app-fingerprint", required=True)
    prepare.add_argument("--approval-id", required=True)
    prepare.add_argument("--expires-in", type=int, default=DEFAULT_MANIFEST_TTL_SECONDS, choices=range(60, 3601), metavar="60-3600")
    send = sub.add_parser("send", help="send only the exact content in a valid approved manifest")
    send.add_argument("--manifest", required=True)
    reconcile_parser = sub.add_parser("reconcile", help="resolve an unknown canonical-ledger result before retry")
    reconcile_parser.add_argument("--content-id", required=True)
    reconcile_parser.add_argument("--expected-user-id", required=True)
    return parser


def dispatch(args: argparse.Namespace) -> Any:
    auth = choose_auth(args.command, args.auth)
    if args.command == "posts":
        validate_max_results(args.max_results, 5, 100)
    if args.command == "search-recent":
        validate_max_results(args.max_results, 10, 100)
    budget_record = None
    if args.command in {"me", "user", "user-by-id", "posts", "post-by-id", "search-recent", "usage"}:
        planned_calls = 1 if auth == "app" else 2
        require_read_budget(planned_calls)
        budget_record = reserve_daily_calls("read", planned_calls)

    def attach_budget(result: Any) -> Any:
        if budget_record is not None and isinstance(result, dict) and isinstance(result.get("_meta"), dict):
            result["_meta"]["budget"] = budget_record
        return result

    if args.command == "me":
        return attach_budget(fetch("GET", "/2/users/me", auth, {"user.fields": USER_FIELDS}))
    if args.command == "user":
        return attach_budget(fetch("GET", "/2/users/by/username/" + quote(args.username, safe=""), auth, {"user.fields": USER_FIELDS}))
    if args.command == "user-by-id":
        return attach_budget(fetch("GET", "/2/users/" + args.user_id, auth, {"user.fields": USER_FIELDS}))
    if args.command == "posts":
        params = {"max_results": validate_max_results(args.max_results, 5, 100), "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}
        if args.pagination_token:
            params["pagination_token"] = args.pagination_token
        return attach_budget(fetch("GET", "/2/users/" + args.user_id + "/tweets", auth, params))
    if args.command == "post-by-id":
        return attach_budget(fetch("GET", "/2/tweets", auth, {"ids": args.ids, "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}))
    if args.command == "search-recent":
        params = {"query": args.query, "max_results": validate_max_results(args.max_results, 10, 100), "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}
        if args.next_token:
            params["next_token"] = args.next_token
        return attach_budget(fetch("GET", "/2/tweets/search/recent", auth, params))
    if args.command == "usage":
        return attach_budget(fetch("GET", "/2/usage/tweets", auth))
    if args.command == "prepare":
        return prepare_manifest(args)
    if args.command == "send":
        return send_manifest(args)
    if args.command == "reconcile":
        return reconcile(args)
    raise ApiFailure("unsupported command")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
        print_json(result, args.pretty)
        if isinstance(result, dict) and result.get("status") in {"partial", "failed", "unresolved"}:
            return 2
        return 0
    except ApiFailure as exc:
        detail = {"error": str(exc)}
        if exc.status is not None:
            detail["http_status"] = exc.status
        if exc.retry_after is not None:
            detail["retry_after"] = exc.retry_after
        if exc.rate_limit_reset is not None:
            detail["rate_limit_reset"] = exc.rate_limit_reset
        if exc.credential_state is not None:
            detail["credential_state"] = exc.credential_state
        if exc.recovery_marker is not None:
            detail["recovery_marker"] = exc.recovery_marker
        if exc.outcome is not None:
            detail["status"] = exc.outcome
        if exc.payload is not None:
            detail["response"] = exc.payload
        print_json(detail, True, sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
