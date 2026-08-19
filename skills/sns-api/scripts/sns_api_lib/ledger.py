"""Canonical single-host publish ledger and audit events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Dict, Optional

from .core import ApiFailure, redact, state_path, utc_now

SCHEMA_VERSION = 3
SNS_COLUMNS = {
    "id", "platform", "account_id", "account_type", "app_id", "credential_fingerprint",
    "operation", "content_id", "payload_hash", "intent_hash", "provider_payload", "manifest_hash",
    "approval_id", "status", "attempts", "attempted_at", "updated_at", "provider_id",
    "provider_status", "provider_state", "http_status",
}


def open_ledger() -> sqlite3.Connection:
    path = state_path("ledger.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    schema = (
        """
        CREATE TABLE IF NOT EXISTS ledger_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT OR IGNORE INTO ledger_meta VALUES('schema_version','3');
        CREATE TABLE IF NOT EXISTS intents(
          id INTEGER PRIMARY KEY,
          platform TEXT NOT NULL,
          account_id TEXT NOT NULL,
          account_type TEXT NOT NULL,
          app_id TEXT NOT NULL,
          credential_fingerprint TEXT NOT NULL,
          operation TEXT NOT NULL,
          content_id TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          intent_hash TEXT NOT NULL,
          provider_payload TEXT NOT NULL,
          manifest_hash TEXT NOT NULL,
          approval_id TEXT NOT NULL,
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          attempted_at TEXT,
          updated_at TEXT NOT NULL,
          provider_id TEXT,
          provider_status TEXT,
          provider_state TEXT,
          http_status INTEGER,
          UNIQUE(platform,account_id,content_id),
          UNIQUE(platform,account_id,intent_hash)
        );
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY,intent_id INTEGER NOT NULL REFERENCES intents(id),
          event TEXT NOT NULL,status TEXT NOT NULL,recorded_at TEXT NOT NULL,
          http_status INTEGER,detail TEXT
        );
        """
    )
    try:
        for attempt in range(100):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(schema)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    raise
                time.sleep(0.01)
        row = connection.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
        columns = {item[1] for item in connection.execute("PRAGMA table_info(intents)")}
        if row is not None and row[0] == "2" and SNS_COLUMNS.issubset(columns):
            connection.execute("UPDATE ledger_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            row = connection.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
    except Exception:
        connection.close()
        raise
    if row is None or row[0] != str(SCHEMA_VERSION) or not SNS_COLUMNS.issubset(columns):
        connection.close()
        raise ApiFailure("unsupported canonical ledger schema_version", code="LEDGER_SCHEMA")
    return connection


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event(connection: sqlite3.Connection, intent_id: int, event: str, status: str,
           http_status: Optional[int] = None, detail: Any = None) -> None:
    safe = redact(detail) if detail is not None else None
    connection.execute(
        "INSERT INTO events(intent_id,event,status,recorded_at,http_status,detail) VALUES(?,?,?,?,?,?)",
        (intent_id, event, status, utc_now(), http_status,
         json.dumps(safe, ensure_ascii=False, sort_keys=True) if safe is not None else None),
    )


def reserve_attempt(manifest: Dict[str, Any]) -> int:
    connection = open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM intents WHERE platform=? AND account_id=? AND (content_id=? OR intent_hash=?)",
            (manifest["platform"], manifest["expected_account_id"], manifest["content_id"], manifest["intent_hash"]),
        ).fetchone()
        unresolved = connection.execute(
            "SELECT content_id FROM intents WHERE platform=? AND account_id=? AND status='unknown' LIMIT 1",
            (manifest["platform"], manifest["expected_account_id"]),
        ).fetchone()
        if manifest.get("authorization_type") == "resume" and (row is None or row["status"] != "submitted"):
            raise ApiFailure("resume-only manifest cannot create or retry a publish intent", code="INVALID_RESUME_STATE")
        if unresolved and (row is None or unresolved["content_id"] != row["content_id"]):
            raise ApiFailure("account has an unresolved unknown intent; reconcile before any new send", code="ACCOUNT_BLOCKED")
        now = utc_now()
        if row:
            if row["content_id"] != manifest["content_id"]:
                raise ApiFailure("duplicate payload already registered under another content_id", code="DUPLICATE")
            if row["payload_hash"] != manifest["payload_hash"]:
                raise ApiFailure("content_id is already bound to a different payload", code="DUPLICATE")
            if row["intent_hash"] != manifest["intent_hash"]:
                raise ApiFailure("content_id is already bound to different media", code="DUPLICATE")
            if row["status"] == "submitted" and manifest.get("_allow_resume") and row["manifest_hash"] == manifest["manifest_hash"] and row["provider_state"]:
                _event(connection, int(row["id"]), "resume", "submitted")
                connection.commit()
                return int(row["id"])
            if row["status"] == "submitted" and manifest.get("_allow_resume") and manifest.get("authorization_type") == "resume":
                try:
                    provider_state = json.loads(row["provider_state"] or "{}")
                except json.JSONDecodeError as exc:
                    raise ApiFailure("submitted provider state is corrupt", code="UNSAFE_PROVIDER_STATE") from exc
                if (manifest.get("resume_of_manifest_hash") != row["manifest_hash"]
                        or manifest.get("resume_state_hash") != _sha256_json(provider_state)):
                    raise ApiFailure("submitted provider state changed after resume manifest preparation", code="RESUME_STATE_CHANGED")
                connection.execute(
                    "UPDATE intents SET manifest_hash=?,approval_id=?,updated_at=? WHERE id=?",
                    (manifest["manifest_hash"], manifest["approval_id"], now, row["id"]),
                )
                _event(connection, int(row["id"]), "resume-manifest-refreshed", "submitted", detail={
                    "resume_of_manifest_hash": manifest["resume_of_manifest_hash"],
                    "resume_state_hash": manifest["resume_state_hash"], "approval_id": manifest["approval_id"],
                })
                connection.commit()
                return int(row["id"])
            if row["status"] in {"published", "submitted", "submitting"}:
                raise ApiFailure("duplicate publish refused", code="DUPLICATE")
            if row["status"] == "unknown":
                raise ApiFailure("unknown publish result refuses blind retry; run reconcile", code="BLIND_RETRY_REFUSED")
            if row["status"] in {"failed", "confirmed_absent"} and row["approval_id"] == manifest["approval_id"]:
                original_binding = (
                    row["account_type"], row["app_id"], row["credential_fingerprint"], row["operation"],
                )
                retry_binding = (
                    manifest["account_type"], manifest["app_id"],
                    manifest["expected_credential_fingerprint"], manifest["operation"],
                )
                if original_binding != retry_binding:
                    raise ApiFailure(
                        "same Domain Authorization reference cannot change retry bindings",
                        code="AUTHORIZATION_SCOPE_MISMATCH",
                    )
            if row["attempts"] >= 2:
                raise ApiFailure("publish attempt limit reached", code="ATTEMPT_LIMIT")
            attempts = row["attempts"] + 1
            connection.execute(
                "UPDATE intents SET account_type=?,app_id=?,credential_fingerprint=?,operation=?,manifest_hash=?,approval_id=?,status='unknown',attempts=?,attempted_at=?,updated_at=?,provider_state=NULL WHERE id=?",
                (manifest["account_type"], manifest["app_id"], manifest["expected_credential_fingerprint"],
                 manifest["operation"], manifest["manifest_hash"], manifest["approval_id"], attempts, now, now, row["id"]),
            )
            intent_id = int(row["id"])
        else:
            cursor = connection.execute(
                "INSERT INTO intents(platform,account_id,account_type,app_id,credential_fingerprint,operation,content_id,payload_hash,intent_hash,provider_payload,manifest_hash,approval_id,status,attempts,attempted_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'unknown',1,?,?)",
                (manifest["platform"], manifest["expected_account_id"], manifest["account_type"], manifest["app_id"],
                 manifest["expected_credential_fingerprint"], manifest["operation"], manifest["content_id"],
                 manifest["payload_hash"], manifest["intent_hash"], json.dumps(manifest["provider_payload"], ensure_ascii=False, sort_keys=True),
                 manifest["manifest_hash"], manifest["approval_id"], now, now),
            )
            intent_id = int(cursor.lastrowid)
        _event(connection, intent_id, "attempt", "unknown")
        connection.commit()
        return intent_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_provider_state(intent_id: int, state: Dict[str, Any]) -> None:
    connection = open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        safe = redact(state)
        connection.execute(
            "UPDATE intents SET provider_state=?,provider_id=COALESCE(?,provider_id),provider_status=COALESCE(?,provider_status),updated_at=? WHERE id=?",
            (json.dumps(safe, ensure_ascii=False, sort_keys=True), safe.get("provider_id"), safe.get("provider_status"), utc_now(), intent_id),
        )
        _event(connection, intent_id, "provider-checkpoint", "unknown", detail=safe)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_result(intent_id: int, status: str, *, http_status: Optional[int] = None,
                  provider_id: Optional[str] = None, provider_status: Optional[str] = None,
                  detail: Any = None, event: str = "result", refund_attempt: bool = False) -> None:
    connection = open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE intents SET status=?,attempts=MAX(0,attempts-?),http_status=?,provider_id=COALESCE(?,provider_id),provider_status=COALESCE(?,provider_status),updated_at=? WHERE id=?",
            (status, 1 if refund_attempt else 0, http_status, provider_id, provider_status, utc_now(), intent_id),
        )
        _event(connection, intent_id, event, status, http_status, detail)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_intent(platform: str, account_id: str, content_id: str) -> Dict[str, Any]:
    connection = open_ledger()
    try:
        row = connection.execute(
            "SELECT * FROM intents WHERE platform=? AND account_id=? AND content_id=?",
            (platform, account_id, content_id),
        ).fetchone()
        if row is None:
            raise ApiFailure("no canonical ledger intent matches platform/account/content_id", code="INTENT_NOT_FOUND")
        result = dict(row)
        try:
            result["provider_payload"] = json.loads(result.get("provider_payload") or "{}")
        except json.JSONDecodeError:
            result["provider_payload"] = {}
        try:
            result["provider_state"] = json.loads(result.get("provider_state") or "{}")
        except json.JSONDecodeError:
            result["provider_state"] = {}
        return result
    finally:
        connection.close()


def manual_resolve(row: Dict[str, Any], outcome: str, reason: str, provider_id: Optional[str]) -> None:
    if row["status"] != "unknown":
        raise ApiFailure("manual resolve only applies to unknown", code="INVALID_RESOLVE_STATE")
    if outcome not in {"published", "confirmed_absent"}:
        raise ApiFailure("invalid manual resolve outcome", code="INVALID_RESOLVE_OUTCOME")
    record_result(int(row["id"]), outcome, provider_id=provider_id,
                  detail={"manual_resolve": outcome, "reason": reason}, event="manual-resolve")
