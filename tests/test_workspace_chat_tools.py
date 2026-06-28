"""Chat workspace tools — decision memory, custom rules, unified proposals."""

from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from webapp.agent.db_query import _CHAT_ALLOWED_TABLES, execute_readonly_sql
from webapp.agent.tools import run_tool
from webapp.agent.workspace_proposals import workspace_items_from_trace
from webapp.db.schema import SCHEMA_SQL, _migrate_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


class WorkspaceChatToolsTests(unittest.TestCase):
    def test_chat_query_sql_allows_decision_events(self) -> None:
        conn = _conn()
        self.assertIn("decision_events", _CHAT_ALLOWED_TABLES)
        result = execute_readonly_sql(
            conn, "SELECT COUNT(*) AS c FROM decision_events"
        )
        self.assertEqual(result["rows"][0]["c"], 0)

    def test_list_open_insights_empty(self) -> None:
        conn = _conn()
        result = run_tool(conn, "list_open_insights", {}, chat_mode=True)
        self.assertEqual(result["count"], 0)

    @patch("webapp.services.custom_rule_similarity.find_similar_custom_rule")
    @patch("webapp.services.custom_rules.preview_custom_rule")
    def test_propose_custom_rule_tool(self, mock_preview, mock_similar) -> None:
        conn = _conn()
        mock_preview.return_value = {
            "compile_error": None,
            "total": 3,
            "transactions": [],
        }
        mock_similar.return_value = None
        result = run_tool(
            conn,
            "propose_custom_rule",
            {"rule_text": "When merchant is Merchant A, set type Variable"},
            chat_mode=True,
        )
        self.assertTrue(result["recommend_save_rule"])
        self.assertEqual(result["preview"]["total"], 3)

    def test_workspace_items_from_custom_rule_trace(self) -> None:
        trace = [
            {
                "tool": "propose_custom_rule",
                "args": {"rule_text": "test rule"},
                "result": {
                    "rule_text": "test rule",
                    "recommend_save_rule": True,
                    "preview": {"total": 2},
                },
            }
        ]
        items = workspace_items_from_trace(trace)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["confirmation_type"], "custom_rule")
        self.assertEqual(items[0]["source"], "chat")

    def test_workspace_items_from_decision_analysis_trace(self) -> None:
        trace = [
            {
                "tool": "run_decision_analysis",
                "result": {
                    "insights_inserted": 1,
                    "inserted_insights": [
                        {
                            "id": 42,
                            "insight_type": "pattern_insight",
                            "title": "Pattern",
                            "pattern_summary": "Summary text",
                        }
                    ],
                },
            }
        ]
        items = workspace_items_from_trace(trace)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["reference_id"], 42)
        self.assertEqual(items[0]["source"], "learning_agent")


if __name__ == "__main__":
    unittest.main()
