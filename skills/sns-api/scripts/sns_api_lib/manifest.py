"""Signed, short-lived, provider-aware publish manifests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from .auth import manifest_signing_key
from .core import ApiFailure, parse_time, workspace_metadata
from .media import prepare_assets

SCHEMA_VERSION = 2


def canonical_bytes(value: Dict[str, Any], excluded: set[str] | None = None) -> bytes:
    filtered = {key: item for key, item in value.items() if key not in (excluded or set())}
    return json.dumps(filtered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def manifest_hash(value: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value, {"manifest_hash", "hmac_signature"})).hexdigest()


def signature(value: Dict[str, Any]) -> str:
    return hmac.new(manifest_signing_key(), canonical_bytes(value, {"hmac_signature"}), hashlib.sha256).hexdigest()


def _write_private(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()) + "." + secrets.token_hex(8))
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def create_manifest(provider: Any, args: Any) -> Dict[str, Any]:
    workspace = workspace_metadata()
    provider.require_capability(args.operation)
    if args.operation not in provider.publish_operations:
        raise ApiFailure("prepare only accepts publish capabilities", code="UNSUPPORTED_CAPABILITY")
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_credential_fingerprint or ""):
        raise ApiFailure("expected credential fingerprint must be SHA-256 hex", code="INVALID_FINGERPRINT")
    payload = args.payload
    assets = prepare_assets(payload.pop("assets", []))
    normalized = provider.normalize_publish(args.operation, payload, assets)
    call_plan = provider.call_plan(args.operation, normalized, assets)
    created = datetime.now(timezone.utc)
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "platform": provider.name,
        "operation": args.operation,
        "content_id": args.content_id,
        "expected_account_id": str(args.expected_account_id),
        "account_type": args.account_type or provider.account_type,
        "app_id": args.app_id,
        "expected_credential_fingerprint": args.expected_credential_fingerprint,
        "approval_id": args.approval_id,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "expires_at": (created + timedelta(seconds=args.expires_in)).isoformat().replace("+00:00", "Z"),
        "provider_payload": normalized,
        "payload_hash": sha256_json(normalized),
        "assets": assets,
        "asset_hash": sha256_json(assets),
        "provider_call_plan": call_plan,
    }
    manifest["intent_hash"] = sha256_json({"provider_payload": normalized, "assets": assets})
    for key in ("content_id", "expected_account_id", "account_type", "app_id", "approval_id"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ApiFailure("manifest identity and approval fields must not be empty", code="INVALID_MANIFEST")
    manifest["manifest_hash"] = manifest_hash(manifest)
    manifest["hmac_signature"] = signature(manifest)
    encoded = canonical_bytes(manifest)
    from .core import secret_values
    for secret in secret_values():
        if len(secret) >= 8 and secret.encode("utf-8") in encoded:
            raise ApiFailure("secret value detected in manifest content", code="SECRET_IN_MANIFEST")
    _write_private(Path(args.manifest), manifest)
    return {
        "status": "prepared", "platform": provider.name, "operation": args.operation,
        "data": {"manifest": str(Path(args.manifest)), **manifest, **workspace}, "errors": [],
        "_meta": {"requested_at": manifest["created_at"], "auth_mode": None, "budget": {}, "rate_limit": {},
                  "provider": {"api_version": provider.api_version}},
    }


def create_resume_manifest(original_path: Path, output_path: Path, approval_id: str, expires_in: int,
                           row: Dict[str, Any]) -> Dict[str, Any]:
    original = load_manifest(original_path, allow_expired=True)
    if row.get("status") != "submitted" or row.get("manifest_hash") != original.get("manifest_hash"):
        raise ApiFailure("resume authorization requires the current submitted manifest", code="INVALID_RESUME_STATE")
    if row.get("payload_hash") != original.get("payload_hash") or row.get("intent_hash") != original.get("intent_hash"):
        raise ApiFailure("resume authorization payload does not match canonical ledger", code="RESUME_BINDING_MISMATCH")
    if not isinstance(approval_id, str) or not approval_id.strip() or approval_id == row.get("approval_id"):
        raise ApiFailure("resume authorization requires a new approval_id", code="NEW_APPROVAL_REQUIRED")
    if not 60 <= int(expires_in) <= 3600:
        raise ApiFailure("resume authorization expiry must be 60-3600 seconds", code="INVALID_MANIFEST")
    created = datetime.now(timezone.utc)
    value = {key: item for key, item in original.items() if key not in {"manifest_hash", "hmac_signature"}}
    value.update(
        approval_id=approval_id.strip(), created_at=created.isoformat().replace("+00:00", "Z"),
        expires_at=(created + timedelta(seconds=int(expires_in))).isoformat().replace("+00:00", "Z"),
        authorization_type="resume", resume_of_manifest_hash=str(row["manifest_hash"]),
        resume_state_hash=sha256_json(row.get("provider_state") or {}),
    )
    value["manifest_hash"] = manifest_hash(value)
    value["hmac_signature"] = signature(value)
    _write_private(output_path, value)
    return value


def load_manifest(path: Path, *, allow_expired: bool = False) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiFailure("invalid publish manifest", code="INVALID_MANIFEST") from exc
    if not isinstance(value, dict):
        raise ApiFailure("publish manifest must be a JSON object", code="INVALID_MANIFEST")
    required = {
        "schema_version", "platform", "operation", "content_id", "expected_account_id", "account_type",
        "app_id", "expected_credential_fingerprint", "approval_id", "created_at", "expires_at",
        "provider_payload", "payload_hash", "assets", "asset_hash", "intent_hash", "provider_call_plan", "manifest_hash", "hmac_signature",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ApiFailure("publish manifest is missing: " + ", ".join(missing), code="INVALID_MANIFEST")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ApiFailure("unsupported manifest schema_version", code="INVALID_MANIFEST")
    if not secrets.compare_digest(str(value["manifest_hash"]), manifest_hash(value)):
        raise ApiFailure("manifest integrity check failed", code="MANIFEST_TAMPERED")
    if not secrets.compare_digest(str(value["hmac_signature"]), signature(value)):
        raise ApiFailure("manifest approval signature check failed", code="MANIFEST_TAMPERED")
    if (value["payload_hash"] != sha256_json(value["provider_payload"])
            or value["asset_hash"] != sha256_json(value["assets"])
            or value["intent_hash"] != sha256_json({"provider_payload": value["provider_payload"], "assets": value["assets"]})):
        raise ApiFailure("manifest payload or asset hash check failed", code="MANIFEST_TAMPERED")
    if value.get("authorization_type") is not None:
        if (value.get("authorization_type") != "resume"
                or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("resume_of_manifest_hash", "")))
                or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("resume_state_hash", "")))):
            raise ApiFailure("resume authorization binding is invalid", code="INVALID_MANIFEST")
    if not allow_expired and datetime.now(timezone.utc) >= parse_time(str(value["expires_at"]), "manifest expires_at"):
        raise ApiFailure("publish manifest has expired", code="MANIFEST_EXPIRED")
    return value
