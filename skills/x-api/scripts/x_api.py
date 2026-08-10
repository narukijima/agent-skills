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
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # non-POSIX platform: locking degrades to a warning
    fcntl = None  # type: ignore[assignment]
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


USER_FIELDS = "created_at,description,location,public_metrics,profile_image_url,protected,url,verified"
POST_FIELDS = "created_at,conversation_id,lang,possibly_sensitive,public_metrics"

OAUTH1_VARIABLES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

# twitter-text v3 weighting: these code point ranges count 1, everything else 2.
LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
# URL characters are limited to the RFC 3986 ASCII set so that CJK text written
# directly after a URL (no space, the normal Japanese style) is not swallowed.
URL_PATTERN = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
WEIGHTED_LIMIT = 280

DEFAULT_BASE_URL = "https://api.x.com"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.retry_after = retry_after
        self.rate_limit_reset = rate_limit_reset
        self.credential_state = credential_state
        self.recovery_marker = recovery_marker


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
            if exc.code < 500:
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
        return "oauth2", {"X_ACCESS_TOKEN": oauth2_refreshed_access_token(oauth2_config)}
    token = os.environ.get("X_ACCESS_TOKEN", "")
    if token:
        return "oauth2", {"X_ACCESS_TOKEN": token}
    raise ApiFailure(
        "user-context auth requires one of: the OAuth 1.0a variables "
        + ", ".join(OAUTH1_VARIABLES)
        + " (non-expiring; preferred for long-running agents); the OAuth 2.0 refresh variables "
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


def choose_auth(operation: str, requested: str) -> str:
    if operation in {"me", "post"} and requested == "app":
        raise ApiFailure(operation + " requires user-context authentication; do not use --auth app")
    if requested in {"user", "app"}:
        return requested
    if operation in {"me", "post"}:
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
    print("note: sending requests to test base URL " + override, file=sys.stderr)
    return override


def api_request(
    method: str,
    path: str,
    auth_kind: str,
    params: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any]:
    url = resolve_base_url() + path
    query = ("?" + urlencode(params)) if params else ""
    request = Request(url + query, method=method)
    request.add_header("Authorization", authorization_for(auth_kind, method, url, params))
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
            return response.status, payload
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


def fetch(method: str, path: str, auth_kind: str, params: Optional[Dict[str, str]] = None) -> Any:
    return api_request(method, path, auth_kind, params)[1]


def print_json(value: Any, pretty: bool, stream: Any = None) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty), file=stream or sys.stdout)


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def weighted_length(text: str) -> int:
    """Estimate X's weighted character count (twitter-text v3): URLs count 23, CJK and emoji count 2."""
    total = 23 * len(URL_PATTERN.findall(text))
    for char in URL_PATTERN.sub("", text):
        code = ord(char)
        total += 1 if any(low <= code <= high for low, high in LIGHT_RANGES) else 2
    return total


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_ledger(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApiFailure("invalid JSON in ledger: " + str(path)) from exc
        if isinstance(record, dict):
            records.append(record)
    return records


def append_ledger(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def attempt_budget_used(matching: Iterable[Dict[str, Any]]) -> int:
    """Count attempts against the 2-attempt budget.

    Each attempt writes an "attempt" row before sending and a "result" row
    after. A rate_limited result refunds its attempt because the request was
    certainly not processed. Rows without an event field come from older
    ledgers where one row was one attempt.
    """
    used = 0
    for record in matching:
        event = record.get("event")
        if event == "attempt":
            used += 1
        elif event == "result":
            if record.get("status") == "rate_limited":
                used -= 1
        elif record.get("http_status") != 429:
            used += 1
    return max(0, used)


def post_text(args: argparse.Namespace) -> Any:
    if args.text is not None and args.file is not None:
        raise ApiFailure("use either --text or --file, not both")
    if args.text is None and args.file is None:
        raise ApiFailure("post requires --text or --file")
    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    text = text.rstrip("\n")
    if not text:
        raise ApiFailure("post text must not be empty")
    digest = content_sha256(text)
    weight = weighted_length(text)
    if args.live and args.dry_run:
        raise ApiFailure("use either --live or --dry-run, not both")
    if not args.live or args.dry_run:
        result = {
            "dry_run": True,
            "content_sha256": digest,
            "text_length": len(text),
            "weighted_length": weight,
            "weighted_limit": WEIGHTED_LIMIT,
            "text": text,
        }
        if args.content_id:
            result["content_id"] = args.content_id
        return result
    if os.environ.get("X_POSTING_ENABLED") != "true":
        raise ApiFailure("live posting requires X_POSTING_ENABLED=true")
    if not args.ledger or not args.content_id:
        raise ApiFailure("live posting requires --ledger and --content-id")
    # Resolve credentials before touching the ledger so that a configuration
    # error can never be recorded as an attempt.
    require_user_credentials()

    ledger_path = Path(args.ledger)
    base_record = {"content_id": args.content_id, "content_sha256": digest}
    # The duplicate check and the write-ahead append must be one atomic unit;
    # otherwise two concurrent runs can both pass the check and double-post.
    with file_lock(ledger_path, "post ledger", required=False):
        matching = [
            record
            for record in read_ledger(ledger_path)
            if record.get("content_sha256") == digest or record.get("content_id") == args.content_id
        ]
        if any(record.get("status") == "sent" for record in matching):
            raise ApiFailure("duplicate post refused: content is already marked sent")
        if matching and matching[-1].get("status") == "unknown" and not args.retry_unknown:
            raise ApiFailure("unknown post result refused: inspect and explicitly use --retry-unknown")
        if attempt_budget_used(matching) >= 2:
            raise ApiFailure("post attempt limit reached: maximum 2 attempts per content_id or content_sha256")
        # Write-ahead: if the process dies mid-send, this row survives and gates
        # the next run behind --retry-unknown.
        append_ledger(ledger_path, {"attempted_at": utc_now(), "event": "attempt", "status": "unknown", **base_record})

    result: Dict[str, Any] = {"attempted_at": utc_now(), "event": "result", "status": "unknown", **base_record}
    try:
        status, payload = api_request("POST", "/2/tweets", "user", body={"text": text})
    except ApiFailure as exc:
        # 429 means the request was certainly not processed and refunds the
        # attempt budget; other 4xx were rejected before processing; 5xx and
        # network failures may have published the post, so they stay "unknown".
        if exc.status == 429:
            result["status"] = "rate_limited"
        elif exc.status is not None and exc.status < 500:
            result["status"] = "failed"
        else:
            result["status"] = "unknown"
        if exc.status is not None:
            result["http_status"] = exc.status
        if exc.retry_after is not None:
            result["retry_after"] = exc.retry_after
        if exc.rate_limit_reset is not None:
            result["rate_limit_reset"] = exc.rate_limit_reset
        with file_lock(ledger_path, "post ledger", required=False):
            append_ledger(ledger_path, result)
        raise
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict) or not payload["data"].get("id"):
        result["http_status"] = status
        with file_lock(ledger_path, "post ledger", required=False):
            append_ledger(ledger_path, result)
        raise ApiFailure("post response did not contain a post id")
    post_id = str(payload["data"]["id"])
    result.update({"status": "sent", "post_id": post_id, "http_status": status})
    with file_lock(ledger_path, "post ledger", required=False):
        append_ledger(ledger_path, result)
    return {"content_sha256": digest, "ledger": str(ledger_path), "post_id": post_id, "url": "https://x.com/i/web/status/" + post_id}


def clamp_max_results(requested: int, minimum: int, maximum: int) -> str:
    value = max(minimum, min(requested, maximum))
    if value != requested:
        print("note: max_results adjusted to %d (API range %d-%d)" % (value, minimum, maximum), file=sys.stderr)
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read X API v2 data or prepare a guarded text post.")
    parser.add_argument("--auth", choices=["auto", "app", "user"], default="auto", help="read auth mode; post always uses user context")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("me", help="get the authenticated user")
    user = sub.add_parser("user", help="get a user by username")
    user.add_argument("--username", required=True)
    by_id = sub.add_parser("user-by-id", help="get a user by id")
    by_id.add_argument("--user-id", required=True)
    posts = sub.add_parser("posts", help="get posts by user id")
    posts.add_argument("--user-id", required=True)
    posts.add_argument("--max-results", type=int, default=10, help="5-100; out-of-range values are clamped")
    posts.add_argument("--pagination-token")
    tweet = sub.add_parser("post-by-id", help="get posts by comma-separated ids")
    tweet.add_argument("--ids", required=True)
    search = sub.add_parser("search-recent", help="search recent posts")
    search.add_argument("--query", required=True)
    search.add_argument("--max-results", type=int, default=10, help="10-100; out-of-range values are clamped")
    search.add_argument("--next-token")
    post = sub.add_parser("post", help="dry-run by default; optionally send one text post")
    group = post.add_mutually_exclusive_group()
    group.add_argument("--text")
    group.add_argument("--file")
    post.add_argument("--live", action="store_true")
    post.add_argument("--dry-run", action="store_true", help="explicitly select the default non-sending mode")
    post.add_argument("--content-id")
    post.add_argument("--ledger")
    post.add_argument("--retry-unknown", action="store_true")
    return parser


def dispatch(args: argparse.Namespace) -> Any:
    auth = choose_auth(args.command, args.auth)
    if args.command == "me":
        return fetch("GET", "/2/users/me", auth, {"user.fields": USER_FIELDS})
    if args.command == "user":
        return fetch("GET", "/2/users/by/username/" + quote(args.username, safe=""), auth, {"user.fields": USER_FIELDS})
    if args.command == "user-by-id":
        return fetch("GET", "/2/users/" + args.user_id, auth, {"user.fields": USER_FIELDS})
    if args.command == "posts":
        params = {"max_results": clamp_max_results(args.max_results, 5, 100), "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}
        if args.pagination_token:
            params["pagination_token"] = args.pagination_token
        return fetch("GET", "/2/users/" + args.user_id + "/tweets", auth, params)
    if args.command == "post-by-id":
        return fetch("GET", "/2/tweets", auth, {"ids": args.ids, "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS})
    if args.command == "search-recent":
        params = {"query": args.query, "max_results": clamp_max_results(args.max_results, 10, 100), "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}
        if args.next_token:
            params["next_token"] = args.next_token
        return fetch("GET", "/2/tweets/search/recent", auth, params)
    if args.command == "post":
        return post_text(args)
    raise ApiFailure("unsupported command")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print_json(dispatch(args), args.pretty)
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
        if exc.payload is not None:
            detail["response"] = exc.payload
        print_json(detail, True, sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
