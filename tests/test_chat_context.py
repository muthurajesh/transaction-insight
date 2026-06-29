import sqlite3
import unittest

from webapp.agent.chat import estimate_chat_context_usage
from webapp.agent.chat_context import (
    clear_chat_context,
    estimate_tokens,
    get_chat_context_after_id,
)
from webapp.agent.chat_history import list_chat_history, list_llm_chat_context
from webapp.db.schema import SCHEMA_SQL, _migrate_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _insert_message(conn: sqlite3.Connection, role: str, content: str) -> None:
    conn.execute(
        """
        INSERT INTO chat_messages (role, content, tool_trace, created_at)
        VALUES (?, ?, NULL, 'now')
        """,
        (role, content),
    )
    conn.commit()


class ChatContextTests(unittest.TestCase):
    def test_estimate_tokens_nonempty(self):
        self.assertGreater(estimate_tokens("hello world"), 0)
        self.assertEqual(estimate_tokens(""), 0)

    def test_clear_context_excludes_prior_messages_from_llm_history(self):
        conn = _conn()
        _insert_message(conn, "user", "older question about allview")
        _insert_message(conn, "assistant", "older answer")
        clear_chat_context(conn)
        _insert_message(conn, "user", "new thread question")
        _insert_message(conn, "assistant", "new thread answer")

        active = list_llm_chat_context(conn, limit=20)
        self.assertEqual(len(active), 2)
        self.assertEqual(active[0]["content"], "new thread question")

        all_history = list_chat_history(conn, active_only=False)
        self.assertEqual(len(all_history), 4)
        active_history = list_chat_history(conn, active_only=True)
        self.assertEqual(len(active_history), 2)

    def test_context_usage_increases_with_active_history(self):
        conn = _conn()
        empty = estimate_chat_context_usage(conn)
        self.assertEqual(empty["meter_tokens"], 0)
        self.assertGreater(empty["system_tokens"], 0)
        _insert_message(conn, "user", "x" * 4000)
        _insert_message(conn, "assistant", "y" * 4000)
        full = estimate_chat_context_usage(conn)
        self.assertGreater(full["history_tokens"], empty["history_tokens"])
        self.assertGreater(full["meter_tokens"], empty["meter_tokens"])
        self.assertEqual(full["history_message_count"], 2)

    def test_clear_resets_active_anchor(self):
        conn = _conn()
        _insert_message(conn, "user", "one")
        clear_chat_context(conn)
        self.assertGreater(get_chat_context_after_id(conn), 0)
        self.assertEqual(list_llm_chat_context(conn), [])


if __name__ == "__main__":
    unittest.main()
