"""Thin provider contract; intentionally not a framework hierarchy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..core import ApiFailure


@dataclass(frozen=True)
class CredentialSnapshot:
    auth_mode: str
    token: str
    public_id: str
    fingerprint: str
    extra: Dict[str, str] = field(default_factory=dict)


class Provider:
    name = ""
    status = "supported"
    account_type = ""
    api_version: Optional[str] = None
    capabilities: tuple[str, ...] = ()
    read_operations: tuple[str, ...] = ()
    publish_operations: tuple[str, ...] = ()
    supports_manual_resolve = False
    supports_manifest_resume = False
    legacy_read_gate: Optional[str] = None
    legacy_write_gate: Optional[str] = None

    def capability_document(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "platform": self.name,
            "status": self.status,
            "capabilities": list(self.capabilities),
        }
        if self.api_version:
            value["api_version"] = self.api_version
        return value

    def require_capability(self, operation: str) -> None:
        if operation not in self.capabilities:
            raise ApiFailure(
                self.name + " does not support capability: " + operation,
                code="UNSUPPORTED_CAPABILITY",
            )

    def is_read_operation(self, operation: str) -> bool:
        return operation in self.read_operations or operation == "publish.status"

    def valid_provider_id(self, value: Optional[str]) -> bool:
        return bool(value)

    def read_call_budget(self, operation: str, params: Dict[str, Any], credentials: Optional[CredentialSnapshot]) -> int:
        return 1

    def reconcile_call_budget(self, row: Dict[str, Any]) -> int:
        return 1

    def normalize_publish(self, operation: str, payload: Dict[str, Any], assets: list[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError

    def call_plan(self, operation: str, payload: Dict[str, Any], assets: list[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError

    def credentials(self, for_write: bool, operation: str = "") -> CredentialSnapshot:
        raise NotImplementedError

    def identity(self, credentials: CredentialSnapshot) -> Dict[str, Any]:
        raise NotImplementedError

    def read(self, credentials: CredentialSnapshot, operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def publish(self, credentials: CredentialSnapshot, manifest: Dict[str, Any], checkpoint: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def reconcile(self, credentials: CredentialSnapshot, row: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
