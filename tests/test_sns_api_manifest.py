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
from sns_api_lib import ledger


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); core._WORKSPACE = (Path(self.temp.name), "test")
    def tearDown(self): core._WORKSPACE = None; self.temp.cleanup()

    def test_manifest_contains_common_bindings_hashes_plan_and_no_secret(self):
        path = Path(self.temp.name) / "approved.json"; result = make_manifest(path)
        value = signed(path)
        for key in ("schema_version", "platform", "operation", "content_id", "expected_account_id", "account_type", "app_id",
                    "expected_credential_fingerprint", "approval_id", "domain_authorization", "created_at", "expires_at", "provider_payload", "payload_hash",
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

    def test_legacy_v2_manifest_remains_loadable_for_inflight_compatibility(self):
        path = Path(self.temp.name) / "legacy-v2.json"; make_manifest(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["schema_version"] = 2
        value.pop("domain_authorization")
        value["manifest_hash"] = manifest.manifest_hash(value)
        with patch.dict(os.environ, base_env(), clear=True):
            value["hmac_signature"] = manifest.signature(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        with patch.dict(os.environ, base_env(), clear=True):
            loaded = manifest.load_manifest(path)
        self.assertEqual(loaded["schema_version"], 2)
        self.assertNotIn("domain_authorization", loaded)

    def test_secret_value_in_payload_is_rejected_before_write(self):
        path = Path(self.temp.name) / "m.json"; env = base_env(SNS_PRIVATE_ACCESS_TOKEN="secret-value-12345")
        args = prepare_args(path, payload={"text": "secret-value-12345"})
        with patch.dict(os.environ, env, clear=True), self.assertRaises(core.ApiFailure) as raised: core.prepare(args)
        self.assertEqual(raised.exception.code, "SECRET_IN_MANIFEST"); self.assertFalse(path.exists())

    def test_x_quote_and_image_assets_are_normalized_and_signed_at_prepare(self):
        quote_path = Path(self.temp.name) / "quote.json"
        make_manifest(quote_path, operation="publish.quote", payload={
            "text": "approved comment", "quote_url": "https://twitter.com/example/status/123?tracking=1",
        })
        quote = signed(quote_path)
        self.assertEqual(quote["provider_payload"]["quote_url"], "https://x.com/i/web/status/123")
        self.assertTrue(quote["provider_payload"]["text"].endswith("https://x.com/i/web/status/123"))
        image = Path(self.temp.name) / "photo.png"; image.write_bytes(b"approved-image-bytes")
        image_path = Path(self.temp.name) / "image.json"
        make_manifest(image_path, operation="publish.image", content_id="image-1", approval_id="image-approval", payload={
            "text": "approved caption", "alt_texts": ["approved description"],
            "assets": [{"kind": "local", "path": str(image), "mime": "image/png"}],
        })
        value = signed(image_path)
        self.assertEqual(value["assets"][0]["path"], str(image.resolve()))
        self.assertEqual(value["assets"][0]["size"], len(b"approved-image-bytes"))
        self.assertEqual(len(value["assets"][0]["sha256"]), 64)
        self.assertEqual(value["provider_payload"]["alt_texts"], ["approved description"])
        self.assertEqual(value["provider_call_plan"]["max_calls"], 6)

    def test_send_parser_surface_has_manifest_only(self):
        import sns_api
        parser = sns_api.parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): parser.parse_args(["send", "--manifest", "m.json", "--platform", "x"])
            with self.assertRaises(SystemExit): parser.parse_args(["send", "--manifest", "m.json", "--payload", "{}"])
            with self.assertRaises(SystemExit): parser.parse_args(["migrate-legacy-x", "--ledger", "/tmp/other.sqlite3"])

    def test_expired_submitted_manifest_reuses_authorization_with_new_state_bound_manifest(self):
        original_path = Path(self.temp.name) / "expired.json"
        make_manifest(original_path, platform="threads", operation="publish.text", account_type="threads-user",
                      payload={"text": "approved"}, expires_in=-1)
        with patch.dict(os.environ, base_env(), clear=True):
            original = manifest.load_manifest(original_path, allow_expired=True)
        intent = ledger.reserve_attempt({**original, "_allow_resume": True})
        ledger.update_provider_state(intent, {"stage": "processing", "container_id": "101", "provider_id": "101",
                                              "provider_status": "IN_PROGRESS", "final_publish_started": False})
        ledger.record_result(intent, "submitted", provider_id="101", provider_status="IN_PROGRESS")
        resume_path = Path(self.temp.name) / "resume.json"
        with patch.dict(os.environ, base_env(), clear=True):
            result = core.authorize_resume(original_path, resume_path, None, 900)
            resumed = manifest.load_manifest(resume_path)
        self.assertEqual(result["status"], "prepared"); self.assertEqual(resumed["authorization_type"], "resume")
        self.assertEqual(resumed["resume_of_manifest_hash"], original["manifest_hash"])
        self.assertEqual(resumed["provider_payload"], original["provider_payload"])
        self.assertEqual(resumed["approval_id"], original["approval_id"])
        self.assertEqual(ledger.reserve_attempt({**resumed, "_allow_resume": True}), intent)
        row = ledger.get_intent("threads", "42", "content-1")
        self.assertEqual(row["approval_id"], original["approval_id"]); self.assertEqual(row["manifest_hash"], resumed["manifest_hash"])

    def test_resume_manifest_refuses_changed_provider_state(self):
        original_path = Path(self.temp.name) / "original.json"
        make_manifest(original_path, platform="threads", operation="publish.text", account_type="threads-user", payload={"text": "approved"})
        original = signed(original_path); intent = ledger.reserve_attempt({**original, "_allow_resume": True})
        ledger.update_provider_state(intent, {"stage": "processing", "container_id": "101", "final_publish_started": False})
        ledger.record_result(intent, "submitted")
        resume_path = Path(self.temp.name) / "resume.json"
        with patch.dict(os.environ, base_env(), clear=True): core.authorize_resume(original_path, resume_path, None, 900)
        ledger.update_provider_state(intent, {"stage": "ready", "container_id": "101", "final_publish_started": False})
        with patch.dict(os.environ, base_env(), clear=True): resumed = manifest.load_manifest(resume_path)
        with self.assertRaises(core.ApiFailure) as raised: ledger.reserve_attempt({**resumed, "_allow_resume": True})
        self.assertEqual(raised.exception.code, "RESUME_STATE_CHANGED")

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
