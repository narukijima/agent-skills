"""Canonical single-host publish ledger and audit events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .core import ApiFailure, parse_time, redact, state_path, utc_now, workspace_info

SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = "2"
LEGACY_LEDGER = Path("state/x-api/x-posts.sqlite3")
LEGACY_COLUMNS = {
    "id", "account_id", "app_id", "app_fingerprint", "content_id", "content_sha256",
    "text", "approval_id", "status", "attempts", "attempted_at", "updated_at", "post_id",
    "http_status",
}
LEGACY_STATUS = {
    "sent": "published",
    "unknown": "unknown",
    "failed": "failed",
    "confirmed_absent": "confirmed_absent",
    "rate_limited": "rate_limited",
}
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
        CREATE TABLE IF NOT EXISTS legacy_x_migrations(
          legacy_intent_id INTEGER PRIMARY KEY,
          intent_id INTEGER NOT NULL UNIQUE REFERENCES intents(id),
          source_row_hash TEXT NOT NULL,
          migrated_at TEXT NOT NULL
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


def _legacy_snapshot(path: Path) -> tuple[str, list[Dict[str, Any]]]:
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        schema = connection.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(intents)")}
        if integrity is None or integrity[0] != "ok" or schema is None or schema[0] != LEGACY_SCHEMA_VERSION:
            raise ApiFailure("legacy X ledger is not a supported, intact schema", code="LEGACY_X_STATE_UNSAFE")
        if not LEGACY_COLUMNS.issubset(columns):
            raise ApiFailure("legacy X ledger is missing required safety fields", code="LEGACY_X_STATE_UNSAFE")
        rows = [dict(row) for row in connection.execute(
            "SELECT id,account_id,app_id,app_fingerprint,content_id,content_sha256,text,approval_id,status,attempts,attempted_at,updated_at,post_id,http_status FROM intents ORDER BY id"
        )]
        connection.rollback()
    except ApiFailure:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ApiFailure("legacy X ledger cannot be read safely; X operations are blocked", code="LEGACY_X_STATE_UNSAFE") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    for row in rows:
        if row["status"] not in LEGACY_STATUS:
            raise ApiFailure("legacy X ledger contains an unsupported intent state", code="LEGACY_X_STATE_UNSAFE")
        if (not str(row["account_id"]).isdigit() or not str(row["content_id"]).strip()
                or not str(row["approval_id"]).strip() or not str(row["app_id"]).strip()
                or not isinstance(row["text"], str) or not row["text"]
                or not isinstance(row["attempts"], int) or row["attempts"] < 0
                or not isinstance(row["id"], int) or row["id"] < 1
                or (row["http_status"] is not None and not isinstance(row["http_status"], int))
                or (row["status"] == "sent" and not str(row["post_id"] or "").isdigit())
                or (row["status"] == "unknown" and row["attempted_at"] is None)):
            raise ApiFailure("legacy X ledger contains an invalid intent", code="LEGACY_X_STATE_UNSAFE")
        try:
            parse_time(str(row["updated_at"]), "legacy updated_at")
            if row["attempted_at"] is not None:
                parse_time(str(row["attempted_at"]), "legacy attempted_at")
        except ApiFailure as exc:
            raise ApiFailure("legacy X ledger contains an invalid timestamp", code="LEGACY_X_STATE_UNSAFE") from exc
        expected = hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
        if (expected != row["content_sha256"] or not isinstance(row["app_fingerprint"], str)
                or len(row["app_fingerprint"]) != 64
                or any(char not in "0123456789abcdef" for char in row["app_fingerprint"])):
            raise ApiFailure("legacy X ledger intent integrity check failed", code="LEGACY_X_STATE_UNSAFE")
    snapshot = {"schema_version": LEGACY_SCHEMA_VERSION, "rows": rows}
    return _sha256_json(snapshot), rows


def _combined_status(current: str, legacy: str) -> str:
    if "unknown" in {current, legacy}:
        return "unknown"
    if current in {"submitting", "submitted"}:
        return current
    if legacy == "published" or current == "published":
        return "published"
    return current


def ensure_legacy_x_migrated() -> Dict[str, Any]:
    root, _ = workspace_info()
    legacy_path = root / LEGACY_LEDGER
    if not legacy_path.exists():
        canonical = state_path("ledger.sqlite3")
        if canonical.exists():
            try:
                check = sqlite3.connect(canonical.as_uri() + "?mode=ro", uri=True)
                marker = check.execute(
                    "SELECT value FROM ledger_meta WHERE key='legacy_x_ledger_digest'"
                ).fetchone()
            except sqlite3.Error as exc:
                raise ApiFailure("canonical SNS ledger cannot be read safely", code="LEDGER_SCHEMA") from exc
            finally:
                try:
                    check.close()
                except UnboundLocalError:
                    pass
            if marker is not None:
                raise ApiFailure("legacy X ledger disappeared after migration", code="LEGACY_X_STATE_CHANGED")
        return {"status": "absent", "source": str(LEGACY_LEDGER), "imported": 0}
    cursor = root
    redirected = False
    for component in LEGACY_LEDGER.parts:
        cursor = cursor / component
        redirected = redirected or cursor.is_symlink()
    if not legacy_path.is_file() or redirected:
        raise ApiFailure("legacy X ledger path is not a regular canonical file", code="LEGACY_X_STATE_UNSAFE")
    digest, rows = _legacy_snapshot(legacy_path)
    connection = open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        marker = connection.execute(
            "SELECT value FROM ledger_meta WHERE key='legacy_x_ledger_digest'"
        ).fetchone()
        if marker is not None:
            if marker[0] != digest:
                raise ApiFailure(
                    "legacy X ledger changed after migration; retire the legacy runtime before X operations",
                    code="LEGACY_X_STATE_CHANGED",
                )
            imported = connection.execute("SELECT COUNT(*) FROM legacy_x_migrations").fetchone()[0]
            connection.commit()
            return {"status": "already_migrated", "source": str(LEGACY_LEDGER), "imported": int(imported), "digest": digest}
        imported = 0
        for legacy in rows:
            normalized = {"text": legacy["text"]}
            payload_hash = _sha256_json(normalized)
            intent_hash = _sha256_json({"provider_payload": normalized, "assets": []})
            source_hash = _sha256_json(legacy)
            mapped_status = LEGACY_STATUS[legacy["status"]]
            existing = connection.execute(
                "SELECT * FROM intents WHERE platform='x' AND account_id=? AND (content_id=? OR intent_hash=?)",
                (str(legacy["account_id"]), legacy["content_id"], intent_hash),
            ).fetchone()
            legacy_state = json.dumps(
                {"legacy_source": str(LEGACY_LEDGER), "legacy_schema_version": 2, "legacy_intent_id": legacy["id"]},
                sort_keys=True,
            )
            if existing is not None:
                if (existing["content_id"] != legacy["content_id"] or existing["payload_hash"] != payload_hash
                        or existing["intent_hash"] != intent_hash):
                    raise ApiFailure("legacy X intent conflicts with canonical SNS state", code="LEGACY_X_STATE_CONFLICT")
                status = _combined_status(str(existing["status"]), mapped_status)
                use_legacy_unknown = mapped_status == "unknown" and existing["status"] != "unknown"
                connection.execute(
                    "UPDATE intents SET status=?,attempts=?,app_id=?,credential_fingerprint=?,approval_id=?,attempted_at=?,updated_at=?,provider_id=COALESCE(?,provider_id),provider_status=?,provider_state=? WHERE id=?",
                    (
                        status, int(existing["attempts"]) + int(legacy["attempts"]),
                        legacy["app_id"] if use_legacy_unknown else existing["app_id"],
                        legacy["app_fingerprint"] if use_legacy_unknown else existing["credential_fingerprint"],
                        legacy["approval_id"] if use_legacy_unknown else existing["approval_id"],
                        legacy["attempted_at"] if use_legacy_unknown else existing["attempted_at"],
                        max(str(existing["updated_at"]), str(legacy["updated_at"])),
                        legacy["post_id"], "legacy-x-api:" + legacy["status"],
                        legacy_state if use_legacy_unknown else existing["provider_state"], existing["id"],
                    ),
                )
                intent_id = int(existing["id"])
            else:
                cursor = connection.execute(
                    "INSERT INTO intents(platform,account_id,account_type,app_id,credential_fingerprint,operation,content_id,payload_hash,intent_hash,provider_payload,manifest_hash,approval_id,status,attempts,attempted_at,updated_at,provider_id,provider_status,provider_state,http_status) VALUES('x',?,?,?,?, 'publish.text',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(legacy["account_id"]), "user", legacy["app_id"], legacy["app_fingerprint"],
                        legacy["content_id"], payload_hash, intent_hash,
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                        hashlib.sha256(("legacy-x-api-v2:" + source_hash).encode()).hexdigest(),
                        legacy["approval_id"], mapped_status, legacy["attempts"], legacy["attempted_at"],
                        legacy["updated_at"], legacy["post_id"], "legacy-x-api:" + legacy["status"], legacy_state,
                        legacy["http_status"],
                    ),
                )
                intent_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO legacy_x_migrations(legacy_intent_id,intent_id,source_row_hash,migrated_at) VALUES(?,?,?,?)",
                (legacy["id"], intent_id, source_hash, utc_now()),
            )
            _event(connection, intent_id, "legacy-x-migration", mapped_status, legacy["http_status"], {
                "source": str(LEGACY_LEDGER), "source_schema_version": 2,
                "legacy_intent_id": legacy["id"], "legacy_status": legacy["status"],
            })
            imported += 1
        connection.execute("INSERT INTO ledger_meta(key,value) VALUES('legacy_x_ledger_digest',?)", (digest,))
        connection.execute("INSERT INTO ledger_meta(key,value) VALUES('legacy_x_migrated_at',?)", (utc_now(),))
        connection.commit()
        return {"status": "migrated", "source": str(LEGACY_LEDGER), "imported": imported, "digest": digest}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
            if row["status"] in {"published", "submitted", "submitting"}:
                raise ApiFailure("duplicate publish refused", code="DUPLICATE")
            if row["status"] == "unknown":
                raise ApiFailure("unknown publish result refuses blind retry; run reconcile", code="BLIND_RETRY_REFUSED")
            if row["status"] in {"failed", "confirmed_absent"} and row["approval_id"] == manifest["approval_id"]:
                raise ApiFailure("retry requires a new signed approval_id", code="NEW_APPROVAL_REQUIRED")
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
