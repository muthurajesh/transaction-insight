import json
import sqlite3
import unittest

from webapp.agent.chat_history import (
    export_chat_history,
    export_chat_history_csv,
    export_chat_history_json,
    export_chat_history_markdown,
    list_chat_history,
    list_chat_history_page,
)
from webapp.db.schema import SCHEMA_SQL, _migrate_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _seed_messages(conn: sqlite3.Connection, count: int) -> None:
    for i in range(1, count + 1):
        role = "user" if i % 2 else "assistant"
        conn.execute(
            """
            INSERT INTO chat_messages (role, content, tool_trace, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (role, f"Message {i}", "[]", f"2026-06-15T10:{i:02d}:00Z"),
        )
    conn.commit()


class ChatHistoryListTests(unittest.TestCase):
    def test_list_empty(self):
        conn = _conn()
        self.assertEqual(list_chat_history(conn), [])

    def test_list_ordered_by_id_asc(self):
        conn = _conn()
        _seed_messages(conn, 3)
        msgs = list_chat_history(conn)
        self.assertEqual([m["content"] for m in msgs], ["Message 1", "Message 2", "Message 3"])


class ChatHistoryPaginationTests(unittest.TestCase):
    def test_page_desc_newest_first(self):
        conn = _conn()
        _seed_messages(conn, 12)
        page1 = list_chat_history_page(conn, page=1, limit=10, order="desc")
        self.assertEqual(page1["total"], 12)
        self.assertEqual(page1["pages"], 2)
        self.assertEqual(page1["page"], 1)
        self.assertEqual(len(page1["messages"]), 10)
        self.assertEqual(page1["messages"][0]["content"], "Message 12")
        self.assertEqual(page1["messages"][-1]["content"], "Message 3")

        page2 = list_chat_history_page(conn, page=2, limit=10, order="desc")
        self.assertEqual(page2["page"], 2)
        self.assertEqual(len(page2["messages"]), 2)

    def test_page_clamps_to_last_page(self):
        conn = _conn()
        _seed_messages(conn, 5)
        data = list_chat_history_page(conn, page=99, limit=10, order="desc")
        self.assertEqual(data["page"], 1)
        self.assertEqual(len(data["messages"]), 5)


class ChatHistoryExportTests(unittest.TestCase):
    def test_export_json(self):
        conn = _conn()
        _seed_messages(conn, 2)
        body, media_type, filename = export_chat_history_json(conn)
        self.assertIn("application/json", media_type)
        self.assertTrue(filename.endswith(".json"))
        payload = json.loads(body)
        self.assertEqual(payload["message_count"], 2)
        self.assertEqual(len(payload["messages"]), 2)

    def test_export_markdown(self):
        conn = _conn()
        _seed_messages(conn, 1)
        body, media_type, filename = export_chat_history_markdown(conn)
        self.assertIn("text/markdown", media_type)
        self.assertTrue(filename.endswith(".md"))
        self.assertIn("Message 1", body)

    def test_export_csv(self):
        conn = _conn()
        _seed_messages(conn, 2)
        body, media_type, filename = export_chat_history_csv(conn)
        self.assertIn("text/csv", media_type)
        self.assertTrue(filename.endswith(".csv"))
        lines = body.strip().splitlines()
        self.assertEqual(lines[0], "id,created_at,role,content,tool_count")
        self.assertEqual(len(lines), 3)

    def test_export_dispatcher(self):
        conn = _conn()
        _seed_messages(conn, 1)
        body, _, _ = export_chat_history(conn, "json")
        self.assertIn("messages", json.loads(body))
        with self.assertRaises(ValueError):
            export_chat_history(conn, "xml")


if __name__ == "__main__":
    unittest.main()
