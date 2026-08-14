import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "sns-api" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sns_api_lib import auth, core, manifest  # noqa: E402
from sns_api_lib.providers.base import CredentialSnapshot  # noqa: E402

SIGNING_KEY = "test-only-sns-manifest-signing-key-32-bytes"
FINGERPRINT = auth.fingerprint("oauth2", "client-1")


def base_env(**extra):
    value = {
        "SNS_API_MANIFEST_SIGNING_KEY": SIGNING_KEY,
        "SNS_API_WRITE_ENABLED": "true",
        "SNS_API_READ_ENABLED": "true",
        "SNS_API_PROJECT_ID": "project-1",
        "SNS_API_AGENT_ID": "agent-1",
        "SNS_API_WRITE_MAX_CALLS": "3",
        "SNS_API_READ_MAX_CALLS": "10",
        "SNS_API_DAILY_WRITE_CALL_LIMIT": "100",
        "SNS_API_DAILY_READ_CALL_LIMIT": "100",
        "SNS_X_APP_ID": "app-1",
    }
    value.update(extra)
    return value


def prepare_args(path, *, platform="x", operation="publish.text", payload=None, content_id="content-1",
                 account_id="42", account_type=None, app_id="app-1", credential_fingerprint=FINGERPRINT,
                 approval_id="approval-1", standing_authorization_file=None, content_source=None, expires_in=900):
    return SimpleNamespace(
        platform=platform, operation=operation, payload=dict(payload or {"text": "hello"}),
        manifest=str(path), content_id=content_id, expected_account_id=account_id,
        account_type=account_type, app_id=app_id,
        expected_credential_fingerprint=credential_fingerprint,
        approval_id=approval_id if standing_authorization_file is None else None,
        standing_authorization_file=standing_authorization_file, content_source=content_source,
        expires_in=expires_in,
    )


def make_manifest(path, environment=None, **kwargs):
    with patch.dict(os.environ, environment or base_env(), clear=True):
        return core.prepare(prepare_args(path, **kwargs))


def signed(path):
    with patch.dict(os.environ, base_env(), clear=True):
        return manifest.load_manifest(path)


def credentials(platform="x", mode="oauth2"):
    return CredentialSnapshot(mode, "secret-token", "client-1", FINGERPRINT)
