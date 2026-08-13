import hashlib
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.sns_api_helpers import FINGERPRINT, base_env, core, make_manifest, signed
from sns_api_lib import budget, ledger


def create_legacy_ledger(root: Path, *, status="sent", content_id="content-1", text="hello") -> Path:
    path = root / "state/x-api/x-posts.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ledger_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO ledger_meta VALUES('schema_version','2');
        CREATE TABLE intents(
          id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,app_id TEXT NOT NULL,
          app_fingerprint TEXT NOT NULL,content_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,
          text TEXT NOT NULL,approval_id TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL,
          attempted_at TEXT,updated_at TEXT NOT NULL,post_id TEXT,http_status INTEGER,
          UNIQUE(account_id,content_id),UNIQUE(account_id,content_sha256)
        );
        CREATE TABLE events(
          id INTEGER PRIMARY KEY,intent_id INTEGER NOT NULL,event TEXT NOT NULL,status TEXT NOT NULL,
          recorded_at TEXT NOT NULL,http_status INTEGER,detail TEXT
        );
        """
    )
    now = "2026-08-13T00:00:00Z"
    connection.execute(
        "INSERT INTO intents VALUES(1,'42','app-1',?,?,?,?,?,?,1,?,?,?,201)",
        (
            FINGERPRINT, content_id, hashlib.sha256(text.encode()).hexdigest(), text,
            "legacy-approval", status, now, now, "123" if status == "sent" else None,
        ),
    )
    connection.commit(); connection.close()
    return path


def create_legacy_usage(root: Path, calls: int) -> Path:
    path = root / "state/x-api/x-usage.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE usage(day TEXT,project_id TEXT,agent_id TEXT,kind TEXT,calls INTEGER,updated_at TEXT,PRIMARY KEY(day,project_id,agent_id,kind))"
    )
    connection.execute(
        "INSERT INTO usage VALUES(?,?,?,?,?,?)",
        (datetime.now(timezone.utc).date().isoformat(), "project-1", "agent-1", "write", calls, "2026-08-13T00:00:00Z"),
    )
    connection.commit(); connection.close()
    return path


class LedgerTests(unittest.TestCase):
    def setUp(self): self.temp = tempfile.TemporaryDirectory(); core._WORKSPACE = (Path(self.temp.name), "test")
    def tearDown(self): core._WORKSPACE = None; self.temp.cleanup()

    def test_platform_is_part_of_uniqueness_key(self):
        x_path = Path(self.temp.name) / "x.json"; make_manifest(x_path)
        x = signed(x_path); first = ledger.reserve_attempt(x)
        other = dict(x); other.update(platform="facebook", operation="publish.text", account_type="page")
        second = ledger.reserve_attempt(other)
        self.assertNotEqual(first, second)

    def test_identical_payload_different_content_id_is_duplicate(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = signed(path); ledger.reserve_attempt(value); ledger.record_result(1, "failed")
        other = dict(value); other["content_id"] = "other"; other["approval_id"] = "new"
        with self.assertRaises(core.ApiFailure) as raised: ledger.reserve_attempt(other)
        self.assertEqual(raised.exception.code, "DUPLICATE")

    def test_same_caption_with_different_media_is_a_distinct_intent(self):
        first_path = Path(self.temp.name) / "first.json"; second_path = Path(self.temp.name) / "second.json"
        common = {"platform": "threads", "operation": "publish.image", "account_type": "threads-user"}
        make_manifest(first_path, **common, payload={"text": "same", "assets": [{"kind": "remote", "url": "https://cdn.example/one.jpg", "mime": "image/jpeg"}]})
        make_manifest(second_path, **common, content_id="content-2", approval_id="approval-2",
                      payload={"text": "same", "assets": [{"kind": "remote", "url": "https://cdn.example/two.jpg", "mime": "image/jpeg"}]})
        first = ledger.reserve_attempt(signed(first_path)); ledger.record_result(first, "failed")
        second = ledger.reserve_attempt(signed(second_path))
        self.assertNotEqual(first, second)

    def test_provider_checkpoint_is_audited_without_tokens(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); intent = ledger.reserve_attempt(signed(path))
        ledger.update_provider_state(intent, {"provider_id": "c1", "provider_status": "ready", "access_token": "do-not-store", "client_secret": "also-no"})
        row = ledger.get_intent("x", "42", "content-1")
        self.assertNotIn("access_token", row["provider_state"]); self.assertNotIn("client_secret", row["provider_state"])

    def test_only_same_manifest_with_explicit_provider_resume_can_resume_submitted(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = signed(path); value["_allow_resume"] = True
        intent = ledger.reserve_attempt(value); ledger.update_provider_state(intent, {"container_id": "c1"}); ledger.record_result(intent, "submitted")
        self.assertEqual(ledger.reserve_attempt(value), intent)
        denied = dict(value); denied["_allow_resume"] = False
        with self.assertRaises(core.ApiFailure) as duplicate: ledger.reserve_attempt(denied)
        self.assertEqual(duplicate.exception.code, "DUPLICATE")

    def test_manual_resolve_requires_unknown_and_records_reason(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); intent = ledger.reserve_attempt(signed(path)); row = ledger.get_intent("x", "42", "content-1")
        ledger.manual_resolve(row, "confirmed_absent", "out-of-band timeline inspection", None)
        self.assertEqual(ledger.get_intent("x", "42", "content-1")["status"], "confirmed_absent")
        with self.assertRaises(core.ApiFailure): ledger.manual_resolve(ledger.get_intent("x", "42", "content-1"), "confirmed_absent", "again", None)

    def test_concurrent_reservation_has_one_winner(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = signed(path); outcomes = []
        def run():
            try: ledger.reserve_attempt(value); outcomes.append("ok")
            except core.ApiFailure: outcomes.append("refused")
        threads = [threading.Thread(target=run) for _ in range(2)]
        for item in threads: item.start()
        for item in threads: item.join()
        self.assertEqual(sorted(outcomes), ["ok", "refused"])

    def test_legacy_sent_migrates_to_published_tombstone_idempotently(self):
        create_legacy_ledger(Path(self.temp.name))
        first = ledger.ensure_legacy_x_migrated()
        self.assertEqual(first["status"], "migrated"); self.assertEqual(first["imported"], 1)
        row = ledger.get_intent("x", "42", "content-1")
        self.assertEqual(row["status"], "published"); self.assertEqual(row["provider_id"], "123")
        path = Path(self.temp.name) / "manifest.json"; make_manifest(path)
        with self.assertRaises(core.ApiFailure) as duplicate: ledger.reserve_attempt(signed(path))
        self.assertEqual(duplicate.exception.code, "DUPLICATE")
        second = ledger.ensure_legacy_x_migrated()
        self.assertEqual(second["status"], "already_migrated")
        connection = ledger.open_ledger()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_x_migrations").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE event='legacy-x-migration'").fetchone()[0], 1)
        connection.close()

    def test_legacy_unknown_blocks_new_x_content(self):
        create_legacy_ledger(Path(self.temp.name), status="unknown", content_id="legacy", text="uncertain")
        ledger.ensure_legacy_x_migrated()
        path = Path(self.temp.name) / "manifest.json"
        make_manifest(path, content_id="new", payload={"text": "new"})
        with self.assertRaises(core.ApiFailure) as blocked: ledger.reserve_attempt(signed(path))
        self.assertEqual(blocked.exception.code, "ACCOUNT_BLOCKED")
        self.assertEqual(ledger.get_intent("x", "42", "legacy")["status"], "unknown")

    def test_changed_legacy_ledger_fails_closed_after_migration(self):
        legacy_path = create_legacy_ledger(Path(self.temp.name))
        ledger.ensure_legacy_x_migrated()
        connection = sqlite3.connect(legacy_path)
        connection.execute("UPDATE intents SET http_status=202 WHERE id=1")
        connection.commit(); connection.close()
        with self.assertRaises(core.ApiFailure) as changed: ledger.ensure_legacy_x_migrated()
        self.assertEqual(changed.exception.code, "LEGACY_X_STATE_CHANGED")

    def test_removed_legacy_ledger_cannot_bypass_migration_guard(self):
        legacy_path = create_legacy_ledger(Path(self.temp.name))
        ledger.ensure_legacy_x_migrated()
        legacy_path.unlink()
        with self.assertRaises(core.ApiFailure) as missing: ledger.ensure_legacy_x_migrated()
        self.assertEqual(missing.exception.code, "LEGACY_X_STATE_CHANGED")

    def test_conflicting_existing_sns_intent_fails_closed(self):
        path = Path(self.temp.name) / "manifest.json"
        make_manifest(path, payload={"text": "different"})
        current = ledger.reserve_attempt(signed(path)); ledger.record_result(current, "failed")
        create_legacy_ledger(Path(self.temp.name), text="hello")
        with self.assertRaises(core.ApiFailure) as conflict: ledger.ensure_legacy_x_migrated()
        self.assertEqual(conflict.exception.code, "LEGACY_X_STATE_CONFLICT")

    def test_legacy_state_symlink_redirection_is_rejected(self):
        root = Path(self.temp.name)
        external = root / "external"
        create_legacy_ledger(external)
        (root / "state").mkdir(exist_ok=True)
        (root / "state/x-api").symlink_to(external / "state/x-api", target_is_directory=True)
        with self.assertRaises(core.ApiFailure) as unsafe: ledger.ensure_legacy_x_migrated()
        self.assertEqual(unsafe.exception.code, "LEGACY_X_STATE_UNSAFE")

    def test_existing_v2_sns_ledger_upgrades_additively(self):
        path = Path(self.temp.name) / "state/sns-api/ledger.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE ledger_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO ledger_meta VALUES('schema_version','2')")
        connection.commit(); connection.close()
        upgraded = ledger.open_ledger()
        self.assertEqual(upgraded.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()[0], "3")
        self.assertTrue(upgraded.execute("SELECT name FROM sqlite_master WHERE name='legacy_x_migrations'").fetchone())
        upgraded.close()

    def test_legacy_usage_is_added_once_before_new_reservation(self):
        create_legacy_usage(Path(self.temp.name), 4)
        with patch.dict(os.environ, base_env(), clear=True):
            first = budget.reserve_calls("x", "write", 3)
            self.assertEqual(first["used_calls"], 7)
            self.assertEqual(budget.ensure_legacy_x_usage_migrated()["status"], "already_migrated")
            second = budget.reserve_calls("x", "write", 3)
        self.assertEqual(second["used_calls"], 10)

    def test_explicit_migration_is_credential_free_and_uses_only_canonical_paths(self):
        result = core.migrate_legacy_x()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["operation"], "state.migrate")
        self.assertEqual(result["data"]["ledger"]["status"], "absent")
        self.assertEqual(result["data"]["usage"]["status"], "absent")

    def test_explicit_migration_does_not_hide_corrupt_canonical_state(self):
        canonical = Path(self.temp.name) / "state/sns-api/ledger.sqlite3"
        canonical.parent.mkdir(parents=True); canonical.write_bytes(b"not-a-ledger")
        with self.assertRaises(core.ApiFailure) as corrupt: core.migrate_legacy_x()
        self.assertEqual(corrupt.exception.code, "LEDGER_SCHEMA")


if __name__ == "__main__": unittest.main()
