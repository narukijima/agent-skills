"""Immutable local media and explicit remote-URL metadata."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit

from .core import ApiFailure

CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            value = handle.read(CHUNK)
            if not value:
                break
            digest.update(value)
    return digest.hexdigest()


def local_asset(value: Dict[str, Any]) -> Dict[str, Any]:
    raw = value.get("path")
    if not isinstance(raw, str) or not raw:
        raise ApiFailure("local asset requires path", code="INVALID_MEDIA")
    source = Path(raw).expanduser()
    if source.is_symlink():
        raise ApiFailure("local asset must be a regular non-symlink file", code="INVALID_MEDIA")
    try:
        path = source.resolve(strict=True)
    except OSError as exc:
        raise ApiFailure("local asset is unavailable", code="INVALID_MEDIA") from exc
    if not path.is_file() or path.is_symlink():
        raise ApiFailure("local asset must be a regular non-symlink file", code="INVALID_MEDIA")
    size = path.stat().st_size
    if size <= 0:
        raise ApiFailure("local asset must not be empty", code="INVALID_MEDIA")
    mime = value.get("mime") or mimetypes.guess_type(path.name)[0]
    if not isinstance(mime, str) or "/" not in mime:
        raise ApiFailure("local asset MIME could not be determined", code="INVALID_MEDIA")
    return {"kind": "local", "path": str(path), "mime": mime, "size": size, "sha256": file_sha256(path)}


def remote_asset(value: Dict[str, Any]) -> Dict[str, Any]:
    url = value.get("url")
    if not isinstance(url, str) or not url:
        raise ApiFailure("remote asset requires url", code="INVALID_MEDIA")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ApiFailure("remote asset must be an HTTPS URL without userinfo", code="INVALID_MEDIA")
    if parsed.query or parsed.fragment:
        raise ApiFailure("remote asset URL must be public and contain no query or fragment", code="INVALID_MEDIA")
    result = {
        "kind": "remote", "url": url, "scheme": parsed.scheme,
        "host": parsed.hostname.lower().rstrip("."),
        "mutable_after_prepare": True,
    }
    if not isinstance(value.get("mime"), str) or "/" not in value["mime"]:
        raise ApiFailure("remote asset requires expected MIME metadata", code="INVALID_MEDIA")
    if value.get("size") is not None and (isinstance(value["size"], bool) or not isinstance(value["size"], int) or value["size"] <= 0):
        raise ApiFailure("remote asset expected size must be a positive integer", code="INVALID_MEDIA")
    if value.get("sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value["sha256"])):
        raise ApiFailure("remote asset expected sha256 must be lowercase hex", code="INVALID_MEDIA")
    for key in ("container", "video_codec", "audio_codec"):
        if value.get(key) is not None and (not isinstance(value[key], str) or not value[key].strip()):
            raise ApiFailure("remote asset expected media metadata is invalid", code="INVALID_MEDIA")
    if value.get("fps") is not None and (isinstance(value["fps"], bool) or not isinstance(value["fps"], (int, float)) or value["fps"] <= 0):
        raise ApiFailure("remote asset expected fps must be positive", code="INVALID_MEDIA")
    for key in ("mime", "size", "sha256", "etag", "last_modified", "container", "video_codec", "audio_codec", "fps"):
        if value.get(key) is not None:
            result[key] = value[key]
    return result


def prepare_assets(values: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ApiFailure("each asset must be a JSON object", code="INVALID_MEDIA")
        kind = value.get("kind")
        result.append(local_asset(value) if kind == "local" else remote_asset(value) if kind == "remote" else _invalid_kind())
    return result


def _invalid_kind():
    raise ApiFailure("asset kind must be local or remote", code="INVALID_MEDIA")


def verify_assets(assets: Iterable[Dict[str, Any]]) -> None:
    for asset in assets:
        if asset.get("kind") != "local":
            continue
        path = Path(str(asset.get("path", "")))
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ApiFailure("prepared local asset is unavailable", code="ASSET_MUTATED") from exc
        if str(resolved) != asset.get("path") or not resolved.is_file() or resolved.is_symlink():
            raise ApiFailure("prepared local asset path changed", code="ASSET_MUTATED")
        if resolved.stat().st_size != asset.get("size") or file_sha256(resolved) != asset.get("sha256"):
            raise ApiFailure("prepared local asset content changed", code="ASSET_MUTATED")
