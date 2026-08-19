"""Credential resolution from canonical SNS_* environment variables."""

from __future__ import annotations

import hashlib
import os

from .core import ApiFailure
from .providers.base import CredentialSnapshot


def _resolved(name: str, *, required: bool = False) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        raise ApiFailure("missing required environment variable: " + name, code="MISSING_CREDENTIAL")
    return value


def global_control(suffix: str) -> str:
    return _resolved("SNS_API_" + suffix)


def provider_env(platform: str, suffix: str, *, required: bool = False) -> str:
    return _resolved("SNS_" + platform.upper() + "_" + suffix, required=required)


def provider_app_id(platform: str) -> str:
    return provider_env(platform, "APP_ID", required=True)


def fingerprint(auth_mode: str, public_id: str) -> str:
    if not public_id:
        raise ApiFailure("credential public app identity is unavailable", code="CREDENTIAL_ID_UNAVAILABLE")
    return hashlib.sha256((auth_mode + ":" + public_id).encode("utf-8")).hexdigest()


def bearer_credentials(platform: str, *, token_suffix: str = "ACCESS_TOKEN", public_suffix: str = "CLIENT_ID",
                       auth_mode: str = "oauth2") -> CredentialSnapshot:
    token = provider_env(platform, token_suffix, required=True)
    public_id = provider_env(platform, public_suffix, required=True)
    return CredentialSnapshot(auth_mode, token, public_id, fingerprint(auth_mode, public_id))


def manifest_signing_key() -> bytes:
    value = _resolved("SNS_API_MANIFEST_SIGNING_KEY")
    if len(value.encode("utf-8")) < 32:
        raise ApiFailure("SNS_API_MANIFEST_SIGNING_KEY must be a gateway-owned secret of at least 32 bytes", code="SIGNING_KEY_INVALID")
    return value.encode("utf-8")
