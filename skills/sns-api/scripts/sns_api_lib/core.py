"""Common execution lifecycle. Provider modules own API semantics."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, quote_plus


class ApiFailure(RuntimeError):
    def __init__(self, message: str, *, code: str = "SNS_API_ERROR", status: Optional[int] = None,
                 payload: Any = None, outcome: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.payload = payload
        self.outcome = outcome
        self.meta = meta or {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ApiFailure("invalid " + label, code="INVALID_TIME") from exc
    if result.tzinfo is None:
        raise ApiFailure(label + " must include a timezone", code="INVALID_TIME")
    return result


def resolve_workspace_root(script_path: Optional[Path] = None, *, stop_at: Optional[Path] = None) -> tuple[Path, str]:
    override = os.environ.get("AGENT_DIRECTORY_ROOT", "").strip()
    if override:
        candidate = Path(override).resolve()
        marker = candidate / ".git"
        if marker.is_dir():
            return candidate, ".git-directory"
        if marker.is_file():
            return candidate, ".git-file"
        raise ApiFailure(
            "workspace root is unavailable: no .git marker was found; refusing a vendor-depth-derived state path",
            code="WORKSPACE_ROOT_UNAVAILABLE",
        )
    current = (script_path or Path(__file__)).resolve().parent
    stop_dir = stop_at.resolve() if stop_at else None
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return candidate, ".git-directory"
        if marker.is_file():
            return candidate, ".git-file"
        if stop_dir is not None and candidate == stop_dir:
            break
    raise ApiFailure(
        "workspace root is unavailable: no .git marker was found; refusing a vendor-depth-derived state path",
        code="WORKSPACE_ROOT_UNAVAILABLE",
    )



_WORKSPACE: Optional[tuple[Path, str]] = None


def workspace_info() -> tuple[Path, str]:
    global _WORKSPACE
    if _WORKSPACE is None:
        _WORKSPACE = resolve_workspace_root()
    return _WORKSPACE


def state_path(name: str) -> Path:
    root, _ = workspace_info()
    return root / "state" / "sns-api" / name


def workspace_metadata() -> Dict[str, str]:
    root, reason = workspace_info()
    return {"workspace_root": str(root), "workspace_root_resolution": reason}


def _providers() -> Dict[str, Any]:
    from .providers import get_providers
    return get_providers()


def provider(name: str) -> Any:
    item = _providers().get(name)
    if item is None:
        raise ApiFailure("unsupported provider: " + name, code="UNSUPPORTED_PROVIDER")
    if item.status != "supported":
        raise ApiFailure("provider is not runtime-supported: " + name, code="UNSUPPORTED_PROVIDER")
    return item


def capabilities(platform: Optional[str] = None) -> Dict[str, Any]:
    items = _providers()
    if platform:
        item = items.get(platform)
        if item is None:
            raise ApiFailure("unsupported provider: " + platform, code="UNSUPPORTED_PROVIDER")
        return item.capability_document()
    return {
        "status": "success",
        "platforms": [items[name].capability_document() for name in sorted(items)],
    }


def envelope(platform: str, operation: str, *, status_value: str = "success", data: Any = None,
             errors: Optional[list[Any]] = None, auth_mode: Optional[str] = None,
             budget: Optional[Dict[str, Any]] = None, rate_limit: Optional[Dict[str, Any]] = None,
             provider_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "status": status_value,
        "platform": platform,
        "operation": operation,
        "data": {} if data is None else data,
        "errors": errors or [],
        "_meta": {
            "requested_at": utc_now(),
            "auth_mode": auth_mode,
            "budget": budget or {},
            "rate_limit": rate_limit or {},
            "provider": provider_meta or {},
        },
    }


def prepare(args: Any) -> Dict[str, Any]:
    from .manifest import create_manifest
    item = provider(args.platform)
    return create_manifest(item, args)


def authorize_resume(original_path: Path, output_path: Path, approval_id: Optional[str], expires_in: int) -> Dict[str, Any]:
    from .ledger import get_intent
    from .manifest import create_resume_manifest, load_manifest

    original = load_manifest(original_path, allow_expired=True)
    item = provider(str(original["platform"]))
    if not item.supports_manifest_resume:
        raise ApiFailure("provider does not support manifest resume", code="UNSUPPORTED_CAPABILITY")
    row = get_intent(str(original["platform"]), str(original["expected_account_id"]), str(original["content_id"]))
    value = create_resume_manifest(original_path, output_path, approval_id, expires_in, row)
    return envelope(item.name, "authorize.resume", status_value="prepared", data={
        "manifest": str(output_path), "content_id": value["content_id"], "account_id": value["expected_account_id"],
        "approval_id": value["approval_id"], "expires_at": value["expires_at"],
        "resume_of_manifest_hash": value["resume_of_manifest_hash"], **workspace_metadata(),
    }, provider_meta={"resume_only": True, "provider_state_bound": True})


def send(manifest_path: Path) -> Dict[str, Any]:
    from .authorization import validate_domain_authorization
    from .auth import global_control, provider_app_id
    from .budget import reserve_calls
    from .ledger import ensure_legacy_x_migrated, get_intent, record_result, reserve_attempt, update_provider_state
    from .manifest import load_manifest
    from .media import verify_assets

    manifest = load_manifest(manifest_path)
    validate_domain_authorization(manifest)
    item = provider(manifest["platform"])
    item.require_capability(manifest["operation"])
    if global_control("WRITE_ENABLED", legacy=item.legacy_write_gate) != "true":
        raise ApiFailure("external write requires SNS_API_WRITE_ENABLED=true", code="WRITE_DISABLED")
    if item.name == "x":
        ensure_legacy_x_migrated()
    if provider_app_id(item.name) != manifest["app_id"]:
        raise ApiFailure("configured app ID does not match manifest app_id", code="APP_MISMATCH")
    verify_assets(manifest.get("assets", []))
    planned = int(manifest["provider_call_plan"]["max_calls"])
    budget = reserve_calls(item.name, "write", planned)
    credentials = item.credentials(for_write=True)
    if credentials.fingerprint != manifest["expected_credential_fingerprint"]:
        raise ApiFailure("credential fingerprint does not match signed manifest", code="CREDENTIAL_MISMATCH")
    actual = item.identity(credentials)
    if str(actual.get("id", "")) != manifest["expected_account_id"]:
        raise ApiFailure("authenticated account does not match expected_account_id; no attempt recorded", code="ACCOUNT_MISMATCH")
    if manifest.get("account_type") and actual.get("account_type") != manifest["account_type"]:
        raise ApiFailure("authenticated account type does not match manifest", code="ACCOUNT_TYPE_MISMATCH")
    intent_id = reserve_attempt({**manifest, "_allow_resume": item.supports_manifest_resume})
    current = get_intent(manifest["platform"], manifest["expected_account_id"], manifest["content_id"])
    manifest = {**manifest, "_resume_state": current.get("provider_state", {})}

    def checkpoint(state: Dict[str, Any]) -> None:
        update_provider_state(intent_id, state)

    try:
        result = item.publish(credentials, manifest, checkpoint)
    except ApiFailure as exc:
        if exc.status == 429 or exc.outcome == "rate_limited":
            outcome = "rate_limited"
        elif exc.outcome == "submitted":
            outcome = "submitted"
        elif exc.outcome == "failed" or (exc.status is not None and 400 <= exc.status < 500):
            outcome = "failed"
        else:
            outcome = "unknown"
        record_result(intent_id, outcome, http_status=exc.status, detail=exc.meta, refund_attempt=outcome == "rate_limited")
        exc.outcome = outcome
        raise
    common_status = result.get("status", "submitted")
    record_result(
        intent_id, common_status, http_status=result.get("http_status"),
        provider_id=result.get("provider_id"), provider_status=result.get("provider_status"),
        detail=result.get("provider", {}),
    )
    return envelope(
        item.name, manifest["operation"], status_value=common_status,
        data={
            "content_id": manifest["content_id"], "account_id": manifest["expected_account_id"],
            "app_id": manifest["app_id"], "payload_hash": manifest["payload_hash"],
            "provider_id": result.get("provider_id"), "provider_status": result.get("provider_status"),
            "ledger": str(state_path("ledger.sqlite3")), **workspace_metadata(),
        }, auth_mode=credentials.auth_mode, budget=budget,
        rate_limit=result.get("rate_limit", {}), provider_meta=result.get("provider", {}),
    )


def read(platform: str, operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
    from .auth import global_control
    from .budget import reserve_calls
    item = provider(platform)
    item.require_capability(operation)
    if not item.is_read_operation(operation):
        raise ApiFailure("operation is not a read capability", code="UNSUPPORTED_CAPABILITY")
    if global_control("READ_ENABLED", legacy=item.legacy_read_gate) != "true":
        raise ApiFailure("external read requires SNS_API_READ_ENABLED=true", code="READ_DISABLED")
    calls = item.read_call_budget(operation, params, None)
    budget = reserve_calls(platform, "read", calls)
    credentials = item.credentials(for_write=False, operation=operation)
    result = item.read(credentials, operation, params)
    return envelope(
        platform, operation, status_value=result.get("status", "success"), data=result.get("data"),
        errors=result.get("errors", []), auth_mode=credentials.auth_mode, budget=budget,
        rate_limit=result.get("rate_limit", {}), provider_meta=result.get("provider", {}),
    )


def status(platform: str, resource_id: str) -> Dict[str, Any]:
    return read(platform, "publish.status", {"resource_id": resource_id})


def reconcile(platform: str, content_id: str, expected_account_id: str) -> Dict[str, Any]:
    from .auth import global_control
    from .budget import reserve_calls
    from .ledger import ensure_legacy_x_migrated, get_intent, record_result
    item = provider(platform)
    item.require_capability("reconcile")
    if global_control("READ_ENABLED", legacy=item.legacy_read_gate) != "true":
        raise ApiFailure("reconcile requires SNS_API_READ_ENABLED=true", code="READ_DISABLED")
    if platform == "x":
        ensure_legacy_x_migrated()
    row = get_intent(platform, expected_account_id, content_id)
    if row["status"] not in {"unknown", "submitted"}:
        return envelope(platform, "reconcile", status_value=row["status"], data={"reconciled": False})
    calls = item.reconcile_call_budget(row)
    budget = reserve_calls(platform, "read", calls)
    credentials = item.credentials(for_write=False, operation="reconcile")
    if credentials.fingerprint != row["credential_fingerprint"]:
        raise ApiFailure("credential fingerprint does not match ledger", code="CREDENTIAL_MISMATCH")
    actual = item.identity(credentials)
    if str(actual.get("id", "")) != expected_account_id:
        raise ApiFailure("authenticated account does not match reconciliation account", code="ACCOUNT_MISMATCH")
    result = item.reconcile(credentials, row)
    outcome = result.get("status", "unresolved")
    if outcome == "confirmed_success":
        record_result(row["id"], "published", provider_id=result.get("provider_id"),
                      provider_status=result.get("provider_status"), detail=result.get("provider", {}), event="reconcile")
    elif outcome == "confirmed_absent":
        record_result(row["id"], "confirmed_absent", detail=result.get("provider", {}), event="reconcile")
    elif outcome == "resume_safe":
        record_result(row["id"], "submitted", provider_id=result.get("provider_id"),
                      provider_status=result.get("provider_status"), detail=result.get("provider", {}), event="reconcile")
    else:
        preserved = "submitted" if row["status"] == "submitted" else "unknown"
        record_result(row["id"], preserved, provider_id=result.get("provider_id"),
                      provider_status=result.get("provider_status"), detail=result.get("provider", {}), event="reconcile")
    response_status = "submitted" if outcome == "resume_safe" else outcome
    data = dict(result.get("data", {})) if isinstance(result.get("data", {}), dict) else {"provider_data": result.get("data")}
    if outcome == "resume_safe": data["reconcile_outcome"] = "resume_safe"
    return envelope(platform, "reconcile", status_value=response_status, data=data,
                    auth_mode=credentials.auth_mode, budget=budget, provider_meta=result.get("provider", {}))


def resolve(platform: str, content_id: str, expected_account_id: str, outcome: str,
            reason: str, provider_id: Optional[str]) -> Dict[str, Any]:
    from .auth import manifest_signing_key
    from .ledger import ensure_legacy_x_migrated, get_intent, manual_resolve
    item = provider(platform)
    if not item.supports_manual_resolve:
        raise ApiFailure("manual resolve is not supported for provider: " + platform, code="UNSUPPORTED_CAPABILITY")
    manifest_signing_key()
    if platform == "x":
        ensure_legacy_x_migrated()
    if not reason.strip():
        raise ApiFailure("manual resolve requires out-of-band evidence in --reason", code="EVIDENCE_REQUIRED")
    if outcome == "published" and not item.valid_provider_id(provider_id):
        raise ApiFailure("published manual resolve requires a valid --provider-id", code="PROVIDER_ID_REQUIRED")
    row = get_intent(platform, expected_account_id, content_id)
    manual_resolve(row, outcome, reason.strip(), provider_id)
    return envelope(platform, "manual.resolve", status_value=outcome, data={
        "content_id": content_id, "account_id": expected_account_id, "provider_id": provider_id,
        "reason": reason.strip(), "event": "manual-resolve", "ledger": str(state_path("ledger.sqlite3")),
    })


def migrate_legacy_x() -> Dict[str, Any]:
    from .budget import ensure_legacy_x_usage_migrated
    from .ledger import ensure_legacy_x_migrated

    ledger = ensure_legacy_x_migrated()
    usage = ensure_legacy_x_usage_migrated()
    status_value = "success" if ledger["status"] in {"absent", "migrated", "already_migrated"} else "failed"
    return envelope(
        "x", "state.migrate", status_value=status_value,
        data={"ledger": ledger, "usage": usage, **workspace_metadata()},
        provider_meta={"legacy_runtime_must_be_retired": True},
    )


def secret_values() -> list[str]:
    result = []
    for key, value in os.environ.items():
        upper = key.upper()
        if value and any(part in upper for part in ("TOKEN", "SECRET", "SIGNING_KEY", "PASSWORD", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY")):
            result.append(value)
    return sorted(result, key=len, reverse=True)


def redact(value: Any) -> Any:
    secrets_found = secret_values()
    if isinstance(value, dict):
        def sensitive_key(key: Any) -> bool:
            name = str(key).lower().replace("-", "_")
            return (name in {"authorization", "cookie", "set_cookie", "password", "api_key", "signature",
                             "token", "secret", "credential", "session_url", "capability_url"}
                    or name.endswith(("_token", "_secret", "_password", "_api_key", "_signature",
                                      "_credential", "_session_url", "_capability_url")))
        return {str(key): redact(item) for key, item in value.items() if not sensitive_key(key)}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for secret in secrets_found:
            for representation in {secret, quote(secret, safe=""), quote_plus(secret, safe="")}:
                text = text.replace(representation, "[REDACTED]")
        return text[:8000]
    return value


def write_private_json(path: Path, value: Dict[str, Any]) -> None:
    import secrets as secrets_module

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()) + "." + secrets_module.token_hex(8))
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


def sign_standing(scope_path: Path, output_path: Path) -> Dict[str, Any]:
    from .authorization import sign_standing_file

    signed = sign_standing_file(scope_path, output_path)
    return envelope(str(signed["platform"]), "authorization.sign", status_value="signed", data={
        "standing_authorization": str(output_path), "authorization_id": signed["authorization_id"],
        "platform": signed["platform"], "operations": signed["operations"],
        "expected_account_id": signed["expected_account_id"], "not_before": signed["not_before"],
        "expires_at": signed["expires_at"], **workspace_metadata(),
    })


def json_object(value: str, label: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ApiFailure(label + " must be valid JSON", code="INVALID_JSON") from exc
    if not isinstance(parsed, dict):
        raise ApiFailure(label + " must be a JSON object", code="INVALID_JSON")
    return parsed
