"""X-owned local media upload and processing lifecycle."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Optional

from ..core import ApiFailure
from ..media import verify_assets

MEDIA_KEY = re.compile(r"^[0-9]+_[0-9]+$")
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/pjpeg", "image/bmp", "image/tiff"}
VIDEO_MIMES = {"video/mp4"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_GIF_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024


def validate_assets(operation: str, assets: list[Dict[str, Any]]) -> None:
    if operation == "publish.image":
        minimum, maximum, mimes, maximum_size = 1, 4, IMAGE_MIMES, MAX_IMAGE_BYTES
    elif operation == "publish.video":
        minimum, maximum, mimes, maximum_size = 1, 1, VIDEO_MIMES, MAX_VIDEO_BYTES
    elif operation == "publish.gif":
        minimum, maximum, mimes, maximum_size = 1, 1, {"image/gif"}, MAX_GIF_BYTES
    else:
        raise ApiFailure("unsupported X media operation", code="UNSUPPORTED_CAPABILITY")
    if not minimum <= len(assets) <= maximum:
        raise ApiFailure("X publish operation has an invalid asset count", code="INVALID_MEDIA")
    for asset in assets:
        if asset.get("kind") != "local":
            raise ApiFailure("X media upload requires local immutable assets", code="INVALID_MEDIA")
        if str(asset.get("mime", "")).lower() not in mimes:
            raise ApiFailure("X media MIME is not supported for this operation", code="INVALID_MEDIA")
        if not isinstance(asset.get("size"), int) or not 0 < asset["size"] <= maximum_size:
            raise ApiFailure("X media size exceeds the operation limit", code="INVALID_MEDIA")


def call_plan(operation: str, payload: Dict[str, Any], assets: list[Dict[str, Any]]) -> list[str]:
    calls = []
    if operation == "publish.image":
        calls.extend(["POST /2/media/upload"] * len(assets))
        calls.extend(["GET /2/media/upload (conditional)"] * len(assets))
        calls.extend(["POST /2/media/metadata"] * len(payload.get("alt_texts", [])))
    elif operation in {"publish.video", "publish.gif"}:
        segments = (int(assets[0]["size"]) + UPLOAD_CHUNK_BYTES - 1) // UPLOAD_CHUNK_BYTES
        calls.append("POST /2/media/upload/initialize")
        calls.extend(["POST /2/media/upload/{id}/append"] * segments)
        calls.extend(["POST /2/media/upload/{id}/finalize", "GET /2/media/upload"])
    return calls


def resume_state(value: Dict[str, Any], asset_count: int) -> Dict[str, Any]:
    state = {
        "media_ids": list(value.get("media_ids", [])),
        "media_keys": list(value.get("media_keys", [])),
        "media_states": list(value.get("media_states", [])),
        "metadata_applied": list(value.get("metadata_applied", [])),
        "post_create_started": value.get("post_create_started"),
    }
    for key in ("uploaded_segments", "post_create_started_at", "provider_status"):
        if key in value:
            state[key] = value[key]
    if (len(state["media_ids"]) != len(state["media_keys"]) or len(state["media_ids"]) != len(state["media_states"])
            or len(state["media_ids"]) > asset_count or any(not str(item).isdigit() for item in state["media_ids"])
            or any(not MEDIA_KEY.fullmatch(str(item)) for item in state["media_keys"])
            or any(item not in {"uploading", "pending", "in_progress", "succeeded", "failed"} for item in state["media_states"])
            or any(not isinstance(item, int) or item < 0 or item >= asset_count for item in state["metadata_applied"])):
        raise ApiFailure("X provider checkpoint is invalid", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    if state["post_create_started"] not in {None, False, True}:
        raise ApiFailure("X post checkpoint is invalid", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    return state


def process(provider: Any, credentials: Any, operation: str, payload: Dict[str, Any], assets: list[Dict[str, Any]],
            state: Dict[str, Any], checkpoint: Any) -> tuple[Dict[str, Any], list[str]]:
    if not assets:
        return state, []
    if not state["media_ids"]:
        state["post_create_started"] = False
        checkpoint(dict(state))
        if operation == "publish.image":
            _upload_images(provider, credentials, assets, state, checkpoint)
        else:
            _upload_chunked(provider, credentials, operation, assets[0], state, checkpoint)
    if len(state["media_ids"]) != len(assets):
        raise ApiFailure("X media checkpoint is incomplete", code="UNSAFE_PROVIDER_STATE", outcome="unknown")
    pending = _refresh_processing(provider, credentials, state, checkpoint)
    if pending:
        return state, pending
    _apply_metadata(provider, credentials, payload.get("alt_texts", []), state, checkpoint)
    verify_assets(assets)
    return state, []


def status_data(result: Any, label: str) -> Dict[str, Any]:
    if isinstance(result.body, dict) and result.body.get("errors"):
        raise ApiFailure(label + " returned partial errors", code="INVALID_PROVIDER_RESPONSE",
                         status=getattr(result, "status", None), payload=result.body.get("errors"), outcome="unknown")
    data = result.body.get("data") if isinstance(result.body, dict) else None
    if not isinstance(data, dict) or not str(data.get("id", "")).isdigit() or not MEDIA_KEY.fullmatch(str(data.get("media_key", ""))):
        raise ApiFailure(label + " response missing media identity", code="INVALID_PROVIDER_RESPONSE",
                         status=getattr(result, "status", None), outcome="unknown")
    return data


def processing_state(data: Dict[str, Any]) -> str:
    return str(((data.get("processing_info") or {}).get("state")) or "succeeded").lower()


def _asset_bytes(asset: Dict[str, Any], *, start: int = 0, length: Optional[int] = None) -> bytes:
    path = Path(str(asset["path"]))
    expected = int(asset["size"]) - start if length is None else min(length, int(asset["size"]) - start)
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            value = handle.read(expected)
    except OSError as exc:
        raise ApiFailure("prepared local asset became unreadable", code="ASSET_MUTATED", outcome="unknown") from exc
    if len(value) != expected:
        raise ApiFailure("prepared local asset changed during upload", code="ASSET_MUTATED", outcome="unknown")
    return value


def _upload_images(provider: Any, credentials: Any, assets: list[Dict[str, Any]], state: Dict[str, Any], checkpoint: Any) -> None:
    for asset in assets:
        raw = _asset_bytes(asset)
        if hashlib.sha256(raw).hexdigest() != asset["sha256"]:
            raise ApiFailure("prepared image bytes changed before upload", code="ASSET_MUTATED", outcome="unknown")
        result = provider._call(credentials, "POST", "/2/media/upload", body={
            "media": base64.b64encode(raw).decode("ascii"), "media_category": "tweet_image",
        })
        data = status_data(result, "X image upload")
        state["media_ids"].append(str(data["id"]))
        state["media_keys"].append(str(data["media_key"]))
        state["media_states"].append(processing_state(data))
        state["provider_status"] = "media_uploaded"
        checkpoint(dict(state))


def _upload_chunked(provider: Any, credentials: Any, operation: str, asset: Dict[str, Any], state: Dict[str, Any], checkpoint: Any) -> None:
    category = "tweet_video" if operation == "publish.video" else "tweet_gif"
    initiated = provider._call(credentials, "POST", "/2/media/upload/initialize", body={
        "media_category": category, "media_type": asset["mime"], "shared": False, "total_bytes": asset["size"],
    })
    data = status_data(initiated, "X chunked upload initialize")
    media_id = str(data["id"])
    state.update(media_ids=[media_id], media_keys=[str(data["media_key"])], media_states=["uploading"], provider_status="uploading")
    checkpoint(dict(state))
    segments = (int(asset["size"]) + UPLOAD_CHUNK_BYTES - 1) // UPLOAD_CHUNK_BYTES
    uploaded_digest = hashlib.sha256()
    for segment in range(segments):
        raw = _asset_bytes(asset, start=segment * UPLOAD_CHUNK_BYTES, length=UPLOAD_CHUNK_BYTES)
        uploaded_digest.update(raw)
        appended = provider._call(credentials, "POST", "/2/media/upload/" + media_id + "/append", body={
            "media": base64.b64encode(raw).decode("ascii"), "segment_index": segment,
        })
        _require_clean_result(appended, "X chunk append")
        state.update(uploaded_segments=segment + 1, provider_status="uploading")
        checkpoint(dict(state))
    if uploaded_digest.hexdigest() != asset["sha256"]:
        raise ApiFailure("prepared media bytes changed during chunk upload", code="ASSET_MUTATED", outcome="unknown")
    finalized = provider._call(credentials, "POST", "/2/media/upload/" + media_id + "/finalize")
    final_data = status_data(finalized, "X chunked upload finalize")
    if str(final_data["id"]) != media_id or str(final_data["media_key"]) != state["media_keys"][0]:
        raise ApiFailure("X finalize media identity changed", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
    state.update(media_states=[processing_state(final_data)], provider_status="media_finalized")
    checkpoint(dict(state))


def _refresh_processing(provider: Any, credentials: Any, state: Dict[str, Any], checkpoint: Any) -> list[str]:
    pending = []
    for index, media_state in enumerate(state["media_states"]):
        if media_state == "failed":
            state["provider_status"] = "media_failed"; checkpoint(dict(state))
            raise ApiFailure("X media processing failed", code="PROVIDER_ASYNC_FAILED", outcome="failed")
        if media_state != "succeeded":
            result = provider._call(credentials, "GET", "/2/media/upload", query={"media_id": state["media_ids"][index]})
            data = status_data(result, "X media status")
            if str(data["id"]) != state["media_ids"][index]:
                raise ApiFailure("X media status identity mismatch", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
            state["media_states"][index] = processing_state(data)
        if state["media_states"][index] == "failed":
            state["provider_status"] = "media_failed"; checkpoint(dict(state))
            raise ApiFailure("X media processing failed", code="PROVIDER_ASYNC_FAILED", outcome="failed")
        if state["media_states"][index] != "succeeded":
            pending.append(state["media_states"][index])
    state["provider_status"] = "media_processing" if pending else "media_ready"
    checkpoint(dict(state))
    return pending


def _apply_metadata(provider: Any, credentials: Any, alt_texts: list[str], state: Dict[str, Any], checkpoint: Any) -> None:
    for index, alt_text in enumerate(alt_texts):
        if index in state["metadata_applied"]:
            continue
        result = provider._call(credentials, "POST", "/2/media/metadata", body={
            "id": state["media_ids"][index], "metadata": {"alt_text": {"text": alt_text}},
        })
        _require_clean_result(result, "X media metadata")
        metadata = result.body.get("data")
        if isinstance(metadata, dict) and str(metadata.get("id", state["media_ids"][index])) != state["media_ids"][index]:
            raise ApiFailure("X media metadata identity mismatch", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
        state["metadata_applied"].append(index)
        state["provider_status"] = "metadata_applied"
        checkpoint(dict(state))


def _require_clean_result(result: Any, label: str) -> None:
    if not isinstance(result.body, dict) or result.body.get("errors"):
        raise ApiFailure(label + " returned an invalid or partial response", code="INVALID_PROVIDER_RESPONSE",
                         status=getattr(result, "status", None),
                         payload=result.body.get("errors") if isinstance(result.body, dict) else None, outcome="unknown")
