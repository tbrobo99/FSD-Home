"""Optional preference learning for Tier-2 confirmations.

Safety first: Phase 2 stays rule-driven. This module NEVER changes an action's
tier in `policy.py` and never auto-approves by default. It only records a
user's repeated, explicit "yes" for the *same* risk/action pair, and — when the
operator turns the feature on — lets that pair be executed without asking
again, with an audit row stating it was a learned decision.

Design limits:
  * disabled unless `preference.learn_enabled` is true in config
  * needs N consecutive approvals (default 3) with no decline in between
  * a single decline removes the pair from the whitelist immediately
  * only pairs that were explicitly approved can ever be trusted
  * the whitelist is persisted in SQLite and survives restart
  * every learned execution is recorded so it is visible, never silent

This is a deliberate trade-off against FR-4 and is off for the safety demo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from . import build, db

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("DATA_DIR") or (_ROOT / "data"))
DB_PATH = _DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_preferences (
    risk_kind   TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    streak      INTEGER NOT NULL DEFAULT 0,
    trusted     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (risk_kind, action_kind)
);
"""

_lock = threading.RLock()


def init() -> None:
    db.init()  # ensure the directory and the base schema exist
    with _lock, db.conn() as c:
        c.executescript(SCHEMA)


def _key(risk_kind: str, action_kind: str) -> tuple[str, str]:
    return (str(risk_kind or ""), str(action_kind or ""))


def record_decision(risk_kind: str, action_kind: str, approved: bool,
                    threshold: int = 3) -> bool:
    """Update the streak. Returns True if the pair has just become trusted."""
    r, a = _key(risk_kind, action_kind)
    if not r or not a:
        return False
    with _lock, db.conn() as c:
        row = c.execute(
            "SELECT streak, trusted FROM learned_preferences WHERE risk_kind=? AND action_kind=?",
            (r, a),
        ).fetchone()
        streak = int(row[0]) if row else 0
        trusted = int(row[1]) if row else 0
        if not approved:
            # One "no" immediately revokes trust and resets the streak.
            c.execute(
                "INSERT INTO learned_preferences (risk_kind, action_kind, streak, trusted, updated_at)"
                " VALUES (?,?,0,0,?) ON CONFLICT(risk_kind,action_kind)"
                " DO UPDATE SET streak=0, trusted=0, updated_at=excluded.updated_at",
                (r, a, db.now_iso()),
            )
            return False
        streak += 1
        became = (not trusted and streak >= max(1, threshold))
        trusted = 1 if (trusted or became) else 0
        c.execute(
            "INSERT INTO learned_preferences (risk_kind, action_kind, streak, trusted, updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(risk_kind,action_kind)"
            " DO UPDATE SET streak=excluded.streak, trusted=excluded.trusted,"
            " updated_at=excluded.updated_at",
            (r, a, streak, trusted, db.now_iso()),
        )
        return bool(became)


def is_trusted(risk_kind: str, action_kind: str) -> bool:
    r, a = _key(risk_kind, action_kind)
    with _lock, db.conn() as c:
        row = c.execute(
            "SELECT trusted FROM learned_preferences WHERE risk_kind=? AND action_kind=?",
            (r, a),
        ).fetchone()
    return bool(row and int(row[0]))


def revoke(risk_kind: str, action_kind: str) -> None:
    r, a = _key(risk_kind, action_kind)
    with _lock, db.conn() as c:
        c.execute(
            "UPDATE learned_preferences SET streak=0, trusted=0, updated_at=?"
            " WHERE risk_kind=? AND action_kind=?",
            (db.now_iso(), r, a),
        )


def all_preferences() -> list[dict[str, Any]]:
    with _lock, db.conn() as c:
        rows = c.execute(
            "SELECT risk_kind, action_kind, streak, trusted, updated_at"
            " FROM learned_preferences ORDER BY updated_at DESC"
        ).fetchall()
    return [{"risk_kind": r[0], "action_kind": r[1], "streak": r[2],
             "trusted": bool(r[3]), "updated_at": r[4]} for r in rows]


# ---- config helper: the only place that decides whether learning is active ----

def learning_enabled(cfg: dict) -> bool:
    return bool((cfg.get("preference") or {}).get("learn_enabled", False))
