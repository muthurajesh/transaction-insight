"""Chat context window anchor (clear screen) and token estimates."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from webapp.config import CHAT_CONTEXT_TOKEN_LIMIT

CHAT_CONTEXT_AFTER_KEY = "chat_context_after_id"


def ensure_app_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def get_chat_context_after_id(conn: sqlite3.Connection) -> int:
    ensure_app_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM app_meta WHERE key = ?",
        (CHAT_CONTEXT_AFTER_KEY,),
    ).fetchone()
    if not row:
        return 0
    raw = str(row["value"] or "").strip()
    return int(raw) if raw.isdigit() else 0


def clear_chat_context(conn: sqlite3.Connection) -> dict[str, Any]:
    """Exclude existing messages from LLM context and active chat restore."""
    ensure_app_meta_table(conn)
    after_id = int(
        conn.execute("SELECT COALESCE(MAX(id), 0) FROM chat_messages").fetchone()[0]
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO app_meta (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (CHAT_CONTEXT_AFTER_KEY, str(after_id), now),
    )
    conn.commit()
    return {"context_after_id": after_id, "cleared_at": now}


def get_context_token_limit() -> int:
    return CHAT_CONTEXT_TOKEN_LIMIT


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars/token) when no model tokenizer is available."""
    text = text or ""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(message: dict[str, str]) -> int:
    return estimate_tokens(message.get("content", "")) + 4


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)
