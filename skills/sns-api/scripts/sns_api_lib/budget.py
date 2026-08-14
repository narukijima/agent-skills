"""Persistent Project/Agent scoped daily call budgets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .auth import global_control
from .core import ApiFailure, parse_time, state_path, utc_now, workspace_info

LEGACY_USAGE = Path("state/x-api/x-usage.sqlite3")
LEGACY_USAGE_COLUMNS = {"day", "project_id", "agent_id", "kind", "calls", "updated_at"}
USAGE_COLUMNS = {"day", "platform", "project_id", "agent_id", "kind", "calls", "updated_at"}


def _open_usage() -> sqlite3.Connection:
    path = state_path("usage.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        for attempt in range(100):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS usage(
                      day TEXT,platform TEXT,project_id TEXT,agent_id TEXT,kind TEXT,calls INTEGER,updated_at TEXT,
                      PRIMARY KEY(day,platform,project_id,agent_id,kind)
                    );
                    CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    """
                )
                columns = {row[1] for row in connection.execute("PRAGMA table_info(usage)")}
                if not USAGE_COLUMNS.issubset(columns):
                    raise ApiFailure("unsupported canonical usage ledger schema", code="LEDGER_SCHEMA")
                return connection
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    raise
                time.sleep(0.01)
    except Exception:
        connection.close()
        raise


def _legacy_usage_snapshot(path: Path):
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(usage)")}
        if integrity is None or integrity[0] != "ok" or not LEGACY_USAGE_COLUMNS.issubset(columns):
            raise ApiFailure("legacy X usage ledger is not a supported, intact schema", code="LEGACY_X_STATE_UNSAFE")
        rows = [dict(row) for row in connection.execute(
            "SELECT day,project_id,agent_id,kind,calls,updated_at FROM usage ORDER BY day,project_id,agent_id,kind"
        )]
        connection.rollback()
    except ApiFailure:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ApiFailure("legacy X usage ledger cannot be read safely; X operations are blocked", code="LEGACY_X_STATE_UNSAFE") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    for row in rows:
        if (row["kind"] not in {"read", "write"} or not str(row["day"]).strip()
                or not str(row["project_id"]).strip() or not str(row["agent_id"]).strip()
                or not isinstance(row["calls"], int) or row["calls"] < 0):
            raise ApiFailure("legacy X usage ledger contains an invalid reservation", code="LEGACY_X_STATE_UNSAFE")
        try:
            datetime.strptime(str(row["day"]), "%Y-%m-%d")
            parse_time(str(row["updated_at"]), "legacy usage updated_at")
        except (ApiFailure, ValueError) as exc:
            raise ApiFailure("legacy X usage ledger contains an invalid timestamp", code="LEGACY_X_STATE_UNSAFE") from exc
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), rows


def ensure_legacy_x_usage_migrated():
    root, _ = workspace_info()
    legacy_path = root / LEGACY_USAGE
    if not legacy_path.exists():
        canonical = state_path("usage.sqlite3")
        if canonical.exists():
            try:
                check = sqlite3.connect(canonical.as_uri() + "?mode=ro", uri=True)
                marker = check.execute("SELECT value FROM usage_meta WHERE key='legacy_x_usage_digest'").fetchone()
            except sqlite3.Error as exc:
                raise ApiFailure("canonical SNS usage ledger cannot be read safely", code="LEDGER_SCHEMA") from exc
            finally:
                try:
                    check.close()
                except UnboundLocalError:
                    pass
            if marker is not None:
                raise ApiFailure("legacy X usage ledger disappeared after migration", code="LEGACY_X_STATE_CHANGED")
        return {"status": "absent", "source": str(LEGACY_USAGE), "imported": 0}
    cursor = root
    redirected = False
    for component in LEGACY_USAGE.parts:
        cursor = cursor / component
        redirected = redirected or cursor.is_symlink()
    if not legacy_path.is_file() or redirected:
        raise ApiFailure("legacy X usage path is not a regular canonical file", code="LEGACY_X_STATE_UNSAFE")
    digest, rows = _legacy_usage_snapshot(legacy_path)
    connection = _open_usage()
    try:
        connection.execute("BEGIN IMMEDIATE")
        marker = connection.execute("SELECT value FROM usage_meta WHERE key='legacy_x_usage_digest'").fetchone()
        if marker is not None:
            if marker[0] != digest:
                raise ApiFailure(
                    "legacy X usage ledger changed after migration; retire the legacy runtime before X operations",
                    code="LEGACY_X_STATE_CHANGED",
                )
            connection.commit()
            return {"status": "already_migrated", "source": str(LEGACY_USAGE), "imported": len(rows), "digest": digest}
        for row in rows:
            current = connection.execute(
                "SELECT calls FROM usage WHERE day=? AND platform='x' AND project_id=? AND agent_id=? AND kind=?",
                (row["day"], row["project_id"], row["agent_id"], row["kind"]),
            ).fetchone()
            calls = int(row["calls"]) + (int(current[0]) if current else 0)
            connection.execute(
                "INSERT INTO usage(day,platform,project_id,agent_id,kind,calls,updated_at) VALUES(?,'x',?,?,?,?,?) ON CONFLICT(day,platform,project_id,agent_id,kind) DO UPDATE SET calls=excluded.calls,updated_at=excluded.updated_at",
                (row["day"], row["project_id"], row["agent_id"], row["kind"], calls, utc_now()),
            )
        connection.execute("INSERT INTO usage_meta(key,value) VALUES('legacy_x_usage_digest',?)", (digest,))
        connection.execute("INSERT INTO usage_meta(key,value) VALUES('legacy_x_usage_migrated_at',?)", (utc_now(),))
        connection.commit()
        return {"status": "migrated", "source": str(LEGACY_USAGE), "imported": len(rows), "digest": digest}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reserve_calls(platform: str, kind: str, planned: int):
    max_name = kind.upper() + "_MAX_CALLS"
    try:
        invocation_limit = int(global_control(max_name, legacy=_legacy(platform, max_name)))
    except ValueError as exc:
        raise ApiFailure("SNS_API_" + max_name + " must be an integer", code="INVALID_BUDGET") from exc
    if (kind == "write" and invocation_limit != planned) or (kind != "write" and invocation_limit < planned):
        raise ApiFailure("invocation call budget does not authorize the provider call plan", code="BUDGET_EXHAUSTED")
    project = global_control("PROJECT_ID", legacy=_legacy(platform, "PROJECT_ID"))
    agent = global_control("AGENT_ID", legacy=_legacy(platform, "AGENT_ID"))
    if not project or not agent:
        raise ApiFailure("daily budget requires SNS_API_PROJECT_ID and SNS_API_AGENT_ID", code="INVALID_BUDGET")
    daily_name = "DAILY_" + kind.upper() + "_CALL_LIMIT"
    try:
        daily = int(global_control(daily_name, legacy=_legacy(platform, daily_name)))
    except ValueError as exc:
        raise ApiFailure("SNS_API_" + daily_name + " must be an integer", code="INVALID_BUDGET") from exc
    if daily < planned:
        raise ApiFailure("daily call limit is smaller than provider call plan", code="BUDGET_EXHAUSTED")
    if platform == "x":
        ensure_legacy_x_usage_migrated()
    connection = _open_usage()
    try:
        connection.execute("BEGIN IMMEDIATE")
        day = datetime.now(timezone.utc).date().isoformat()
        row = connection.execute(
            "SELECT calls FROM usage WHERE day=? AND platform=? AND project_id=? AND agent_id=? AND kind=?",
            (day, platform, project, agent, kind),
        ).fetchone()
        used = int(row[0]) if row else 0
        if used + planned > daily:
            raise ApiFailure("daily Project/Agent call budget exhausted", code="BUDGET_EXHAUSTED")
        connection.execute(
            "INSERT INTO usage VALUES (?,?,?,?,?,?,?) ON CONFLICT(day,platform,project_id,agent_id,kind) DO UPDATE SET calls=excluded.calls,updated_at=excluded.updated_at",
            (day, platform, project, agent, kind, used + planned, utc_now()),
        )
        connection.commit()
        return {"day": day, "platform": platform, "project_id": project, "agent_id": agent,
                "kind": kind, "reserved_calls": planned, "used_calls": used + planned, "daily_limit": daily}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _legacy(platform: str, suffix: str):
    if platform != "x":
        return None
    return {
        "READ_MAX_CALLS": "X_API_READ_MAX_CALLS",
        "WRITE_MAX_CALLS": "X_API_WRITE_MAX_CALLS",
        "PROJECT_ID": "X_API_PROJECT_ID",
        "AGENT_ID": "X_API_AGENT_ID",
        "DAILY_READ_CALL_LIMIT": "X_API_DAILY_READ_CALL_LIMIT",
        "DAILY_WRITE_CALL_LIMIT": "X_API_DAILY_WRITE_CALL_LIMIT",
    }.get(suffix)
