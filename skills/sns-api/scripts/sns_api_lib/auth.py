"""Credential resolution and migration-safe environment handling."""

from __future__ import annotations

import hashlib
import os
from typing import Iterable, Optional

from .core import ApiFailure
from .providers.base import CredentialSnapshot


def _resolved(names: Iterable[str], *, required: bool = False, label: Optional[str] = None) -> str:
    configured = [(name, os.environ.get(name, "")) for name in names if os.environ.get(name, "")]
    distinct = {value for _, value in configured}
    if len(distinct) > 1:
        raise ApiFailure(
            "conflicting new and deprecated environment variables for " + (label or next(iter(names), "value")),
            code="ENV_CONFLICT",
        )
    value = configured[0][1] if configured else ""
    if required and not value:
        raise ApiFailure("missing required environment variable: " + (label or next(iter(names), "value")), code="MISSING_CREDENTIAL")
    return value


def global_control(suffix: str, *, legacy: Optional[str] = None) -> str:
    names = ["SNS_API_" + suffix]
    if legacy:
        names.append(legacy)
    return _resolved(names, label="SNS_API_" + suffix)


def provider_env(platform: str, suffix: str, *, legacy: Iterable[str] = (), required: bool = False) -> str:
    canonical = "SNS_" + platform.upper() + "_" + suffix
    return _resolved([canonical, *legacy], required=required, label=canonical)


def provider_app_id(platform: str) -> str:
    legacy = ["X_API_APP_ID"] if platform == "x" else []
    return provider_env(platform, "APP_ID", legacy=legacy, required=True)


def fingerprint(auth_mode: str, public_id: str) -> str:
    if not public_id:
        raise ApiFailure("credential public app identity is unavailable", code="CREDENTIAL_ID_UNAVAILABLE")
    return hashlib.sha256((auth_mode + ":" + public_id).encode("utf-8")).hexdigest()


def bearer_credentials(platform: str, *, token_suffix: str = "ACCESS_TOKEN", public_suffix: str = "CLIENT_ID",
                       auth_mode: str = "oauth2", token_legacy: Iterable[str] = (),
                       public_legacy: Iterable[str] = ()) -> CredentialSnapshot:
    token = provider_env(platform, token_suffix, legacy=token_legacy, required=True)
    public_id = provider_env(platform, public_suffix, legacy=public_legacy, required=True)
    return CredentialSnapshot(auth_mode, token, public_id, fingerprint(auth_mode, public_id))


def manifest_signing_key() -> bytes:
    value = _resolved(["SNS_API_MANIFEST_SIGNING_KEY", "X_API_MANIFEST_SIGNING_KEY"], label="SNS_API_MANIFEST_SIGNING_KEY")
    if len(value.encode("utf-8")) < 32:
        raise ApiFailure("SNS_API_MANIFEST_SIGNING_KEY must be a gateway-owned secret of at least 32 bytes", code="SIGNING_KEY_INVALID")
    return value.encode("utf-8")
