"""
migrate_to_mongo.py — One-time migration of chat_history.db → MongoDB Atlas.

Run once:  python migrate_to_mongo.py
Safe to re-run: uses upsert so duplicates are ignored.
"""

import sqlite3
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from db import get_db

SQLITE_PATH = os.getenv("DB_PATH", "chat_history.db")


def _parse_dt(s):
    """Parse SQLite timestamp string to datetime, or return as-is if already datetime."""
    if not s:
        return datetime.utcnow()
    if isinstance(s, datetime):
        return s
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def _load_json(val, default):
    """Decode a JSON string from SQLite, or return default on failure."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def migrate():
    print(f"Connecting to SQLite: {SQLITE_PATH}")
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    db = get_db()

    # ── sessions ──────────────────────────────────────────────────────────────
    sessions = conn.execute("SELECT id, title, created_at FROM sessions").fetchall()
    print(f"\nMigrating {len(sessions)} sessions...")
    ok = 0
    for row in sessions:
        db.sessions.replace_one(
            {"_id": row["id"]},
            {"_id": row["id"], "title": row["title"], "created_at": _parse_dt(row["created_at"])},
            upsert=True,
        )
        ok += 1
    print(f"  ✓ {ok} sessions migrated")

    # ── messages ──────────────────────────────────────────────────────────────
    messages = conn.execute(
        "SELECT session_id, role, content, timestamp FROM messages ORDER BY timestamp ASC"
    ).fetchall()
    print(f"\nMigrating {len(messages)} messages...")
    ok = 0
    for row in messages:
        db.messages.insert_one({
            "session_id": row["session_id"],
            "role":       row["role"],
            "content":    row["content"],
            "timestamp":  _parse_dt(row["timestamp"]),
        })
        ok += 1
    print(f"  ✓ {ok} messages migrated")

    # ── case_sessions ─────────────────────────────────────────────────────────
    cases = conn.execute("SELECT * FROM case_sessions").fetchall()
    print(f"\nMigrating {len(cases)} case sessions...")
    ok = 0
    for row in cases:
        provided = _load_json(row["provided_fields"], {})
        doc = {
            "_id":               row["case_id"],
            "chat_session_id":   row["chat_session_id"],
            "case_type":         row["case_type"],
            "required_fields":   _load_json(row["required_fields"], []),
            "provided_fields":   provided,
            "missing_fields":    _load_json(row["missing_fields"], []),
            "force_draft":       bool(row["force_draft"]),
            "validation_result": _load_json(row["validation_result"], None),
            "draft_generated":   bool(row["draft_generated"]),
            "draft_text":        row["draft_text"],
            "created_at":        _parse_dt(row["created_at"]),
            "updated_at":        _parse_dt(row["updated_at"]),
        }
        db.case_sessions.replace_one({"_id": row["case_id"]}, doc, upsert=True)
        if row["draft_generated"]:
            print(f"  → complaint migrated: case_id={row['case_id'][:8]}... type={row['case_type']}")
        ok += 1
    print(f"  ✓ {ok} case sessions migrated")

    conn.close()

    # ── verification ─────────────────────────────────────────────────────────
    print("\n── Verification ─────────────────────────────────────────────")
    print(f"  sessions    in Atlas: {db.sessions.count_documents({})}")
    print(f"  messages    in Atlas: {db.messages.count_documents({})}")
    print(f"  case_sessions Atlas: {db.case_sessions.count_documents({})}")
    print(f"  drafts generated:    {db.case_sessions.count_documents({'draft_generated': True})}")
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
