import json
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.sns_api_helpers import base_env, core, make_manifest, manifest, prepare_args, signed


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); core._WORKSPACE = (Path(self.temp.name), "test")
    def tearDown(self): core._WORKSPACE = None; self.temp.cleanup()

    def test_manifest_contains_common_bindings_hashes_plan_and_no_secret(self):
        path = Path(self.temp.name) / "approved.json"; result = make_manifest(path)
        value = signed(path)
        for key in ("schema_version", "platform", "operation", "content_id", "expected_account_id", "account_type", "app_id",
                    "expected_credential_fingerprint", "approval_id", "created_at", "expires_at", "provider_payload", "payload_hash",
                    "assets", "asset_hash", "intent_hash", "provider_call_plan", "manifest_hash", "hmac_signature"):
            self.assertIn(key, value)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn("test-only-sns-manifest", path.read_text())
        self.assertEqual(result["status"], "prepared")

    def test_tamper_and_expiry_are_rejected(self):
        path = Path(self.temp.name) / "approved.json"; make_manifest(path)
        value = json.loads(path.read_text()); value["provider_payload"]["text"] = "changed"; path.write_text(json.dumps(value))
        with patch.dict(os.environ, base_env(), clear=True), self.assertRaises(core.ApiFailure) as tampered: manifest.load_manifest(path)
        self.assertEqual(tampered.exception.code, "MANIFEST_TAMPERED")
        make_manifest(path); value = json.loads(path.read_text()); value["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        value["manifest_hash"] = manifest.manifest_hash(value)
        with patch.dict(os.environ, base_env(), clear=True): value["hmac_signature"] = manifest.signature(value)
        path.write_text(json.dumps(value))
        with patch.dict(os.environ, base_env(), clear=True), self.assertRaises(core.ApiFailure) as expired: manifest.load_manifest(path)
        self.assertEqual(expired.exception.code, "MANIFEST_EXPIRED")

    def test_platform_is_signed_and_wrong_platform_tamper_fails(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = json.loads(path.read_text()); value["platform"] = "threads"; path.write_text(json.dumps(value))
        with patch.dict(os.environ, base_env(), clear=True), self.assertRaises(core.ApiFailure): manifest.load_manifest(path)

    def test_secret_value_in_payload_is_rejected_before_write(self):
        path = Path(self.temp.name) / "m.json"; env = base_env(SNS_PRIVATE_ACCESS_TOKEN="secret-value-12345")
        args = prepare_args(path, payload={"text": "secret-value-12345"})
        with patch.dict(os.environ, env, clear=True), self.assertRaises(core.ApiFailure) as raised: core.prepare(args)
        self.assertEqual(raised.exception.code, "SECRET_IN_MANIFEST"); self.assertFalse(path.exists())

    def test_send_parser_surface_has_manifest_only(self):
        import sns_api
        parser = sns_api.parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): parser.parse_args(["send", "--manifest", "m.json", "--platform", "x"])
            with self.assertRaises(SystemExit): parser.parse_args(["send", "--manifest", "m.json", "--payload", "{}"])
            with self.assertRaises(SystemExit): parser.parse_args(["migrate-legacy-x", "--ledger", "/tmp/other.sqlite3"])

    def test_workspace_resolution_uses_nearest_git_marker_and_fails_closed(self):
        root = Path(self.temp.name) / "repo"; script = root / "deep/scripts/file.py"; script.parent.mkdir(parents=True); script.write_text("")
        (root / ".git").write_text("gitdir: somewhere")
        self.assertEqual(core.resolve_workspace_root(script), (root.resolve(), ".git-file"))
        (root / ".git").unlink()
        with self.assertRaises(core.ApiFailure): core.resolve_workspace_root(script)

    def test_vendored_cli_help_works_and_prepare_outside_git_fails_structured(self):
        source = Path(__file__).parents[1] / "skills/sns-api"
        copy = Path(self.temp.name) / "vendor/sns-api"; shutil.copytree(source, copy)
        script = copy / "scripts/sns_api.py"
        help_run = subprocess.run([sys.executable, str(script), "--help"], cwd=self.temp.name, text=True, capture_output=True, check=False)
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        manifest_path = Path(self.temp.name) / "approved.json"
        command = [sys.executable, str(script), "prepare", "--platform", "x", "--operation", "publish.text",
                   "--payload", '{"text":"hello"}', "--manifest", str(manifest_path), "--content-id", "c1",
                   "--expected-account-id", "42", "--account-type", "user", "--app-id", "a",
                   "--expected-credential-fingerprint", "0" * 64, "--approval-id", "ap1"]
        env = {**os.environ, "SNS_API_MANIFEST_SIGNING_KEY": "a" * 32}
        failed = subprocess.run(command, cwd=self.temp.name, env=env, text=True, capture_output=True, check=False)
        self.assertEqual(failed.returncode, 1)
        error = json.loads(failed.stderr)
        self.assertEqual(error["errors"][0]["code"], "WORKSPACE_ROOT_UNAVAILABLE")
        self.assertEqual((error["platform"], error["operation"], error["data"]), ("x", "publish.text", {}))
        self.assertFalse(manifest_path.exists())


if __name__ == "__main__": unittest.main()
