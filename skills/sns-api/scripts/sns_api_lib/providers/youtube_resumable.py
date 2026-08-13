"""YouTube-owned authenticated resumable transport and private session state."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import secrets
import socket
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from ..core import ApiFailure, redact, state_path, utc_now, workspace_info
from ..http import HttpResult, MAX_RESPONSE_BYTES, validate_url

HOSTS = {"www.googleapis.com", "upload.youtube.com"}
CHUNK_BYTES = 8 * 1024 * 1024
HANDLE = re.compile(r"^[0-9a-f]{64}$")
RANGE = re.compile(r"^bytes=0-([0-9]+)$")


def save_session(session_url: str, binding: Dict[str, Any]) -> tuple[str, str]:
    validate_url(session_url, HOSTS, authenticated=True)
    handle = secrets.token_hex(32)
    value = {**binding, "session_url": session_url, "created_at": utc_now(), "upload_offset": 0}
    _write_private(_session_path(handle), value)
    return handle, hashlib.sha256(session_url.encode("utf-8")).hexdigest()


def load_session(handle: str, binding: Dict[str, Any]) -> Dict[str, Any]:
    path = _session_path(handle)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        stat = os.fstat(descriptor)
        if stat.st_uid != os.getuid() or (stat.st_mode & 0o077):
            os.close(descriptor)
            raise ApiFailure("YouTube private upload state permissions are unsafe", code="PRIVATE_STATE_UNSAFE", outcome="unknown")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle_file:
            value = json.load(handle_file)
    except ApiFailure:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiFailure("YouTube private upload state is unavailable", code="PRIVATE_STATE_UNAVAILABLE", outcome="unknown") from exc
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in binding.items()):
        raise ApiFailure("YouTube private upload state binding mismatch", code="PRIVATE_STATE_UNSAFE", outcome="unknown")
    validate_url(str(value.get("session_url", "")), HOSTS, authenticated=True)
    return value


def update_session(handle: str, value: Dict[str, Any]) -> None:
    _write_private(_session_path(handle), value)


def remove_session(handle: str) -> None:
    try:
        _session_path(handle).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # A stale capability file is safer than losing the durable provider ID.
        pass


def probe(session_url: str, token: str, total: int, *, timeout: int = 120) -> HttpResult:
    return _exchange(session_url, token, total, None, None, None, timeout)


def upload_range(session_url: str, token: str, path: Path, mime: str, start: int, end: int,
                 total: int, *, timeout: int = 120) -> HttpResult:
    return _exchange(session_url, token, total, path, start, end, timeout, mime=mime)


def acknowledged_offset(result: HttpResult, total: int) -> int:
    if result.status in {200, 201}:
        return total
    if result.status != 308:
        raise ApiFailure("YouTube resumable response status is invalid", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
    value = result.headers.get("range")
    if not value:
        return 0
    match = RANGE.fullmatch(value.strip())
    if not match:
        raise ApiFailure("YouTube resumable Range header is invalid", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
    offset = int(match.group(1)) + 1
    if not 0 <= offset <= total:
        raise ApiFailure("YouTube resumable Range exceeds asset", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
    return offset


def _exchange(session_url: str, token: str, total: int, path: Optional[Path], start: Optional[int],
              end: Optional[int], timeout: int, *, mime: Optional[str] = None) -> HttpResult:
    validate_url(session_url, HOSTS, authenticated=True)
    parsed = urlsplit(session_url)
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    target = parsed.path + (("?" + parsed.query) if parsed.query else "")
    try:
        connection.putrequest("PUT", target, skip_accept_encoding=True)
        connection.putheader("Authorization", "Bearer " + token)
        if path is None:
            connection.putheader("Content-Length", "0")
            connection.putheader("Content-Range", f"bytes */{total}")
        else:
            if start is None or end is None or start < 0 or end < start or end >= total:
                raise ApiFailure("invalid YouTube upload range", code="INVALID_MEDIA")
            length = end - start + 1
            connection.putheader("Content-Type", str(mime))
            connection.putheader("Content-Length", str(length))
            connection.putheader("Content-Range", f"bytes {start}-{end}/{total}")
        connection.endheaders()
        if path is not None:
            with path.open("rb") as handle:
                handle.seek(int(start))
                remaining = int(end) - int(start) + 1
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ApiFailure("local asset ended during YouTube upload", code="ASSET_MUTATED", outcome="submitted")
                    connection.send(chunk)
                    remaining -= len(chunk)
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ApiFailure("YouTube resumable response exceeds size limit", code="RESPONSE_TOO_LARGE", outcome="submitted")
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiFailure("YouTube resumable response was not JSON", code="INVALID_PROVIDER_JSON", outcome="submitted") from exc
        status = int(response.status)
        if status not in {200, 201, 308}:
            if status == 404:
                outcome = "failed"
                code = "UPLOAD_SESSION_EXPIRED"
            else:
                outcome = "rate_limited" if status == 429 else "failed" if 400 <= status < 500 else "submitted"
                code = "PROVIDER_HTTP_ERROR"
            raise ApiFailure("YouTube resumable endpoint returned an HTTP error", code=code, status=status,
                             payload=redact(body), outcome=outcome,
                             meta={"rate_limit": HttpResult(status, {}, headers).rate_limit})
        return HttpResult(status, body, headers)
    except ApiFailure:
        raise
    except (OSError, TimeoutError, socket.timeout, http.client.HTTPException) as exc:
        raise ApiFailure("YouTube resumable request can be resumed from private state",
                         code="PROVIDER_RESULT_UNKNOWN", outcome="submitted") from exc
    finally:
        connection.close()


def _session_path(handle: str) -> Path:
    if not HANDLE.fullmatch(str(handle)):
        raise ApiFailure("invalid YouTube upload session handle", code="PRIVATE_STATE_UNSAFE", outcome="unknown")
    directory = state_path("private/youtube-upload-sessions")
    _ensure_private_directory(directory)
    return directory / (str(handle) + ".json")


def _ensure_private_directory(path: Path) -> None:
    workspace, _ = workspace_info()
    lexical = workspace / "state" / "sns-api" / "private" / "youtube-upload-sessions"
    if path != lexical:
        raise ApiFailure("private state path is not canonical", code="PRIVATE_STATE_UNSAFE")
    for candidate in (workspace / "state", workspace / "state" / "sns-api", workspace / "state" / "sns-api" / "private", lexical):
        if candidate.exists() and candidate.is_symlink():
            raise ApiFailure("private state path must not traverse a symlink", code="PRIVATE_STATE_UNSAFE")
    current = path
    missing = []
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink():
        raise ApiFailure("private state path must not traverse a symlink", code="PRIVATE_STATE_UNSAFE")
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    for item in (path.parent, path):
        if item.exists():
            if item.is_symlink() or not item.is_dir():
                raise ApiFailure("private state directory is unsafe", code="PRIVATE_STATE_UNSAFE")
            if item.stat().st_uid != os.getuid():
                raise ApiFailure("private state directory ownership is unsafe", code="PRIVATE_STATE_UNSAFE")
            os.chmod(item, 0o700)


def _write_private(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()) + "." + secrets.token_hex(8))
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ApiFailure("could not persist YouTube private upload state", code="PRIVATE_STATE_WRITE_FAILED", outcome="submitted") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
