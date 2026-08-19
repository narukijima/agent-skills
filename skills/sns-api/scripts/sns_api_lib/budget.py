"""Persistent Project/Agent scoped daily call budgets."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

from .auth import global_control
from .core import ApiFailure, state_path, utc_now

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


def reserve_calls(platform: str, kind: str, planned: int):
    max_name = kind.upper() + "_MAX_CALLS"
    try:
        invocation_limit = int(global_control(max_name))
    except ValueError as exc:
        raise ApiFailure("SNS_API_" + max_name + " must be an integer", code="INVALID_BUDGET") from exc
    if (kind == "write" and invocation_limit != planned) or (kind != "write" and invocation_limit < planned):
        raise ApiFailure("invocation call budget does not authorize the provider call plan", code="BUDGET_EXHAUSTED")
    project = global_control("PROJECT_ID")
    agent = global_control("AGENT_ID")
    if not project or not agent:
        raise ApiFailure("daily budget requires SNS_API_PROJECT_ID and SNS_API_AGENT_ID", code="INVALID_BUDGET")
    daily_name = "DAILY_" + kind.upper() + "_CALL_LIMIT"
    try:
        daily = int(global_control(daily_name))
    except ValueError as exc:
        raise ApiFailure("SNS_API_" + daily_name + " must be an integer", code="INVALID_BUDGET") from exc
    if daily < planned:
        raise ApiFailure("daily call limit is smaller than provider call plan", code="BUDGET_EXHAUSTED")
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
