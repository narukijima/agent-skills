"""Narrow Domain Authorization bindings for publish manifests.

This module does not inspect or grant shell, filesystem, network, sandbox, or
provider-runtime execution permission. It only binds an already-authorized SNS
intent to the account, content source, caller, schedule, and call plan that the
Project selected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .auth import manifest_signing_key
from .core import ApiFailure, parse_time


STANDING_SCHEMA_VERSION = 1
STANDING_FIELDS = {
    "schema_version", "authorization_type", "authorization_id", "platform", "operations",
    "expected_account_id", "account_type", "app_id", "expected_credential_fingerprint",
    "allowed_content_sources", "max_provider_calls_per_intent", "daily_write_call_limit",
    "caller_scope", "not_before", "expires_at",
    "authorization_hash", "hmac_signature",
}
CALLER_FIELDS = {"project_id", "agent_id", "schedule_id"}


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ApiFailure(label + " must be a non-empty string", code="INVALID_AUTHORIZATION")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ApiFailure(label + " must be a non-empty string list", code="INVALID_AUTHORIZATION")
    if any(item != item.strip() for item in value):
        raise ApiFailure(label + " values must not have surrounding whitespace", code="INVALID_AUTHORIZATION")
    normalized = list(value)
    if len(normalized) != len(set(normalized)):
        raise ApiFailure(label + " must not contain duplicates", code="INVALID_AUTHORIZATION")
    return normalized


def _canonical(value: Dict[str, Any], excluded: set[str] | None = None) -> bytes:
    filtered = {key: item for key, item in value.items() if key not in (excluded or set())}
    return json.dumps(filtered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _authorization_hash(value: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value, {"authorization_hash", "hmac_signature"})).hexdigest()


def _authorization_signature(value: Dict[str, Any]) -> str:
    return hmac.new(manifest_signing_key(), _canonical(value, {"hmac_signature"}), hashlib.sha256).hexdigest()


def sign_standing_authorization(value: Dict[str, Any]) -> Dict[str, Any]:
    """Sign a Project-created scope; callers still need the gateway-owned key."""
    signed = {key: item for key, item in value.items() if key not in {"authorization_hash", "hmac_signature"}}
    signed["authorization_hash"] = _authorization_hash(signed)
    signed["hmac_signature"] = _authorization_signature(signed)
    return signed


def _verify_standing_signature(value: Dict[str, Any]) -> None:
    if not secrets.compare_digest(str(value.get("authorization_hash", "")), _authorization_hash(value)):
        raise ApiFailure("standing authorization hash check failed", code="AUTHORIZATION_TAMPERED")
    if not secrets.compare_digest(str(value.get("hmac_signature", "")), _authorization_signature(value)):
        raise ApiFailure("standing authorization signature check failed", code="AUTHORIZATION_TAMPERED")


def _load_standing(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiFailure("standing authorization must be a readable JSON object", code="INVALID_AUTHORIZATION") from exc
    if not isinstance(value, dict):
        raise ApiFailure("standing authorization must be a JSON object", code="INVALID_AUTHORIZATION")
    return _validate_standing(value)


def _validate_standing(value: Dict[str, Any]) -> Dict[str, Any]:
    missing = sorted(STANDING_FIELDS - set(value))
    unexpected = sorted(set(value) - STANDING_FIELDS)
    if missing or unexpected:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected: " + ", ".join(unexpected))
        raise ApiFailure("invalid standing authorization fields (" + "; ".join(detail) + ")", code="INVALID_AUTHORIZATION")
    if value["schema_version"] != STANDING_SCHEMA_VERSION or value["authorization_type"] != "standing":
        raise ApiFailure("unsupported standing authorization schema", code="INVALID_AUTHORIZATION")
    _verify_standing_signature(value)
    caller = value.get("caller_scope")
    if not isinstance(caller, dict) or set(caller) != CALLER_FIELDS:
        raise ApiFailure("caller_scope must contain project_id, agent_id, and schedule_id", code="INVALID_AUTHORIZATION")
    normalized = {
        **value,
        "authorization_id": _required_string(value["authorization_id"], "authorization_id"),
        "platform": _required_string(value["platform"], "platform"),
        "operations": _string_list(value["operations"], "operations"),
        "expected_account_id": _required_string(value["expected_account_id"], "expected_account_id"),
        "account_type": _required_string(value["account_type"], "account_type"),
        "app_id": _required_string(value["app_id"], "app_id"),
        "expected_credential_fingerprint": _required_string(
            value["expected_credential_fingerprint"], "expected_credential_fingerprint"
        ),
        "allowed_content_sources": _string_list(value["allowed_content_sources"], "allowed_content_sources"),
        "caller_scope": {
            "project_id": _required_string(caller["project_id"], "caller_scope.project_id"),
            "agent_id": _required_string(caller["agent_id"], "caller_scope.agent_id"),
            "schedule_id": _required_string(caller["schedule_id"], "caller_scope.schedule_id"),
        },
    }
    for key in ("max_provider_calls_per_intent", "daily_write_call_limit"):
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ApiFailure(key + " must be a positive integer", code="INVALID_AUTHORIZATION")
        normalized[key] = number
    not_before = parse_time(str(value["not_before"]), "standing authorization not_before")
    expires_at = parse_time(str(value["expires_at"]), "standing authorization expires_at")
    if expires_at <= not_before:
        raise ApiFailure("standing authorization expiry must follow not_before", code="INVALID_AUTHORIZATION")
    return normalized


def sign_standing_file(scope_path: Path, output_path: Path) -> Dict[str, Any]:
    """Sign and validate a Project-defined standing scope without reimplementing the HMAC."""
    from .core import write_private_json

    try:
        value = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiFailure("standing authorization scope must be a readable JSON object", code="INVALID_AUTHORIZATION") from exc
    if not isinstance(value, dict):
        raise ApiFailure("standing authorization scope must be a JSON object", code="INVALID_AUTHORIZATION")
    signed = _validate_standing(sign_standing_authorization(value))
    write_private_json(output_path, signed)
    return signed


def _current_caller() -> Dict[str, str]:
    return {
        "project_id": os.environ.get("SNS_API_PROJECT_ID", ""),
        "agent_id": os.environ.get("SNS_API_AGENT_ID", ""),
        "schedule_id": os.environ.get("SNS_API_SCHEDULE_ID", ""),
    }


def _verify_standing_scope(scope: Dict[str, Any], actual: Dict[str, Any]) -> None:
    _verify_standing_signature(scope)
    now = datetime.now(timezone.utc)
    if now < parse_time(str(scope["not_before"]), "standing authorization not_before"):
        raise ApiFailure("standing authorization is not active", code="AUTHORIZATION_NOT_ACTIVE")
    if now >= parse_time(str(scope["expires_at"]), "standing authorization expires_at"):
        raise ApiFailure("standing authorization has expired", code="AUTHORIZATION_EXPIRED")
    exact = {
        "platform": actual["platform"],
        "expected_account_id": str(actual["expected_account_id"]),
        "account_type": actual["account_type"],
        "app_id": actual["app_id"],
        "expected_credential_fingerprint": actual["expected_credential_fingerprint"],
    }
    for key, value in exact.items():
        if scope.get(key) != value:
            raise ApiFailure("standing authorization " + key + " does not match intent", code="AUTHORIZATION_SCOPE_MISMATCH")
    if actual["operation"] not in scope.get("operations", []):
        raise ApiFailure("standing authorization does not allow this operation", code="AUTHORIZATION_SCOPE_MISMATCH")
    if actual["content_source"] not in scope.get("allowed_content_sources", []):
        raise ApiFailure("standing authorization does not allow this content source", code="AUTHORIZATION_SCOPE_MISMATCH")
    if int(actual["provider_call_plan"]["max_calls"]) > int(scope.get("max_provider_calls_per_intent", 0)):
        raise ApiFailure("standing authorization call budget is smaller than provider call plan", code="AUTHORIZATION_SCOPE_MISMATCH")
    try:
        daily_limit = int(os.environ.get("SNS_API_DAILY_WRITE_CALL_LIMIT", ""))
    except ValueError as exc:
        raise ApiFailure("standing authorization requires a valid daily write call limit", code="AUTHORIZATION_SCOPE_MISMATCH") from exc
    if daily_limit != scope.get("daily_write_call_limit"):
        raise ApiFailure("standing authorization daily write call limit does not match", code="AUTHORIZATION_SCOPE_MISMATCH")
    if scope.get("caller_scope") != _current_caller():
        raise ApiFailure("standing authorization caller or schedule scope does not match", code="AUTHORIZATION_SCOPE_MISMATCH")


def build_domain_authorization(args: Any, manifest_fields: Dict[str, Any]) -> Dict[str, Any]:
    standing_path = getattr(args, "standing_authorization_file", None)
    content_source = str(getattr(args, "content_source", "") or "").strip()
    if standing_path:
        if not content_source:
            raise ApiFailure("standing authorization requires --content-source", code="INVALID_AUTHORIZATION")
        scope = _load_standing(Path(standing_path))
        _verify_standing_scope(scope, {**manifest_fields, "content_source": content_source})
        return {
            "type": "standing",
            "authorization_id": scope["authorization_id"],
            "content_source": content_source,
            "scope": scope,
        }
    authorization_id = _required_string(getattr(args, "approval_id", None), "approval_id")
    return {
        "type": "intent",
        "authorization_id": authorization_id,
        "content_source": content_source or "direct:intent",
        "scope": {
            "platform": manifest_fields["platform"],
            "operation": manifest_fields["operation"],
            "content_id": manifest_fields["content_id"],
            "expected_account_id": manifest_fields["expected_account_id"],
            "account_type": manifest_fields["account_type"],
            "app_id": manifest_fields["app_id"],
            "expected_credential_fingerprint": manifest_fields["expected_credential_fingerprint"],
            "payload_hash": manifest_fields["payload_hash"],
            "asset_hash": manifest_fields["asset_hash"],
            "max_provider_calls_per_intent": int(manifest_fields["provider_call_plan"]["max_calls"]),
        },
    }


def validate_domain_authorization(manifest: Dict[str, Any]) -> None:
    authorization = manifest.get("domain_authorization")
    if not isinstance(authorization, dict) or authorization.get("authorization_id") != manifest.get("approval_id"):
        raise ApiFailure("manifest Domain Authorization reference is invalid", code="INVALID_AUTHORIZATION")
    auth_type = authorization.get("type")
    if auth_type == "standing":
        scope = authorization.get("scope")
        if not isinstance(scope, dict):
            raise ApiFailure("standing authorization scope is missing", code="INVALID_AUTHORIZATION")
        _verify_standing_scope(scope, {**manifest, "content_source": authorization.get("content_source", "")})
        return
    if auth_type != "intent":
        raise ApiFailure("unsupported Domain Authorization type", code="INVALID_AUTHORIZATION")
    expected = {
        "platform": manifest["platform"],
        "operation": manifest["operation"],
        "content_id": manifest["content_id"],
        "expected_account_id": manifest["expected_account_id"],
        "account_type": manifest["account_type"],
        "app_id": manifest["app_id"],
        "expected_credential_fingerprint": manifest["expected_credential_fingerprint"],
        "payload_hash": manifest["payload_hash"],
        "asset_hash": manifest["asset_hash"],
        "max_provider_calls_per_intent": int(manifest["provider_call_plan"]["max_calls"]),
    }
    if authorization.get("scope") != expected:
        raise ApiFailure("intent authorization does not match signed manifest", code="AUTHORIZATION_SCOPE_MISMATCH")
