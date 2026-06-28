from __future__ import annotations

import sqlite3
from typing import Any

TABLES_TO_CLEAR = (
    "transactions",
    "merchant_labels",
    "cadence_rules",
    "chat_messages",
    "agent_runs",
    "ingested_files",
    "lookup_snapshots",
    "custom_reports",
    "description_lookup",
    "category_rules",
    "pipeline_custom_rules",
    "classification_audit_runs",
    "classification_audit_findings",
    "decision_events",
    "ai_insights",
)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLES_TO_CLEAR:
        try:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            counts[table] = int(row["c"]) if row else 0
        except sqlite3.OperationalError:
            counts[table] = 0
    return counts


def clear_data_store(conn: sqlite3.Connection) -> dict[str, Any]:
    before = table_counts(conn)
    for table in TABLES_TO_CLEAR:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    return {"cleared": before, "message": "All web app data removed."}
