"""Tests for decision memory, pending inbox, and learning agent."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp.agent.db_query import LEARNING_AGENT_ALLOWED_TABLES, validate_readonly_sql
from webapp.agent.learning_analyst import run_decision_analyst
from webapp.agent.tools import run_tool
from webapp.db.schema import get_connection, init_db
from webapp.services.decision_events import (
    infer_confirm_action,
    log_decision_event,
    list_recent_events,
)
from webapp.services.pending_confirmations import (
    list_pending_confirmations,
    preview_pending_confirmation,
)
from webapp.services.learning_agent import (
    accept_insight,
    reject_insight,
    run_learning_agent,
    _category_correction_patterns,
)


class DecisionMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        init_db(self.db_path)
        self.conn = get_connection(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_log_and_list_decision_event(self) -> None:
        eid = log_decision_event(
            self.conn,
            source="confirm_categories",
            entity_type="merchant",
            entity_key="Merchant A",
            action="edited",
            ai_proposal={"ai_category": "Category X"},
            user_outcome={"ai_category": "Category Y"},
        )
        self.conn.commit()
        self.assertGreater(eid, 0)
        events = list_recent_events(self.conn, days=30)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "edited")

    def test_infer_confirm_action(self) -> None:
        suggested = {"ai_category": "A", "ai_sub_category": "", "expense_type": "Variable", "flow_type": "Expense", "classification": "Personal"}
        self.assertEqual(infer_confirm_action(suggested, suggested), "accepted")
        edited = {**suggested, "ai_category": "B"}
        self.assertEqual(infer_confirm_action(suggested, edited), "edited")
        self.assertEqual(infer_confirm_action(None, edited), "accepted")

    def test_pending_confirmations_empty(self) -> None:
        data = list_pending_confirmations(self.conn)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["items"], [])

    def test_category_correction_patterns(self) -> None:
        events = [
            {
                "action": "edited",
                "ai_proposal": {"ai_category": "Cat A"},
                "user_outcome": {"ai_category": "Cat B"},
            },
            {
                "action": "edited",
                "ai_proposal": {"ai_category": "Cat A"},
                "user_outcome": {"ai_category": "Cat B"},
            },
        ]
        patterns = _category_correction_patterns(events)
        self.assertEqual(len(patterns), 1)
        self.assertIn("Cat A", patterns[0]["pattern_summary"])

    def test_insight_accept_reject(self) -> None:
        self.conn.execute(
            """
            INSERT INTO ai_insights (
                insight_type, title, pattern_summary, rationale, confidence,
                merchant_key, proposal_json, status, created_at, updated_at
            ) VALUES ('pattern_insight', 'Test', 'summary', 'rationale', 0.8,
                      '', '{}', 'open', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """
        )
        self.conn.commit()
        row_id = self.conn.execute("SELECT id FROM ai_insights").fetchone()["id"]
        accept_insight(self.conn, int(row_id))
        status = self.conn.execute(
            "SELECT status FROM ai_insights WHERE id = ?", (row_id,)
        ).fetchone()["status"]
        self.assertEqual(status, "accepted")
        events = list_recent_events(self.conn, days=30)
        self.assertTrue(any(e["source"] == "learning_agent" for e in events))

        self.conn.execute(
            """
            INSERT INTO ai_insights (
                insight_type, title, pattern_summary, rationale, confidence,
                merchant_key, proposal_json, status, created_at, updated_at
            ) VALUES ('pattern_insight', 'Test2', 'summary2', 'rationale', 0.8,
                      '', '{}', 'open', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """
        )
        self.conn.commit()
        row_id2 = self.conn.execute("SELECT id FROM ai_insights ORDER BY id DESC").fetchone()["id"]
        reject_insight(self.conn, int(row_id2))
        status2 = self.conn.execute(
            "SELECT status FROM ai_insights WHERE id = ?", (row_id2,)
        ).fetchone()["status"]
        self.assertEqual(status2, "rejected")

    def test_run_learning_agent_skipped_when_disabled(self) -> None:
        result = run_learning_agent(self.db_path, force=False)
        if not result.get("enabled"):
            self.assertTrue(result.get("skipped"))

    def test_preview_merchant_label_item(self) -> None:
        self.conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                ai_category, ai_sub_category, expense_type, flow_type,
                classification, label_status, imported_at
            ) VALUES (
                'tx1', '2026-01-15', '2026-01', -10.0, 'Merchant A',
                'Cat Old', 'Sub Old', 'Variable', 'Expense',
                'Personal', 'needs_review', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        self.conn.commit()
        item = {
            "confirmation_type": "merchant_label",
            "entity_key": "Merchant A",
            "proposal": {
                "ai_category": "Cat New",
                "ai_sub_category": "",
                "expense_type": "Variable",
                "flow_type": "Expense",
                "classification": "Personal",
            },
        }
        preview = preview_pending_confirmation(self.conn, item)
        self.assertEqual(preview["total"], 1)
        self.assertEqual(preview["transactions"][0]["proposed"]["ai_category"], "Cat New")

    def test_preview_pattern_insight_without_merchant(self) -> None:
        preview = preview_pending_confirmation(
            self.conn,
            {"confirmation_type": "pattern_insight", "entity_key": ""},
        )
        self.assertEqual(preview["preview_kind"], "none")
        self.assertIn("message", preview)

    def test_learning_agent_query_sql_allows_decision_events(self) -> None:
        sql = "SELECT source, action FROM decision_events LIMIT 5"
        validate_readonly_sql(sql, allowed_tables=LEARNING_AGENT_ALLOWED_TABLES)
        result = run_tool(
            self.conn,
            "query_sql",
            {"sql": sql},
            learning_agent_mode=True,
        )
        self.assertEqual(result["row_count"], 0)

    @patch("webapp.agent.learning_analyst.chat_completion")
    def test_decision_analyst_insights_from_llm(self, mock_llm) -> None:
        log_decision_event(
            self.conn,
            source="confirm_categories",
            entity_type="merchant",
            entity_key="Merchant A",
            action="edited",
            ai_proposal={"ai_category": "Cat A"},
            user_outcome={"ai_category": "Cat B"},
        )
        self.conn.commit()
        mock_llm.return_value = json.dumps(
            {
                "insights": [
                    {
                        "insight_type": "pattern_insight",
                        "title": "Category flip",
                        "pattern_summary": "Often change Cat A to Cat B",
                        "rationale": "Seen in decision_events",
                        "confidence": 0.8,
                        "merchant_key": "",
                        "proposal_json": {"suggested_action": "rename_category"},
                    }
                ]
            }
        )
        events = list_recent_events(self.conn, days=30)
        result = run_decision_analyst(
            self.conn,
            events,
            lookback_days=30,
            max_insights=5,
            model="test-model",
        )
        self.assertEqual(len(result["insights"]), 1)
        self.assertEqual(result["model"], "test-model")
        mock_llm.assert_called()

    @patch("webapp.config.LEARNING_AGENT_USE_LLM", True)
    @patch("webapp.agent.learning_analyst.run_decision_analyst")
    def test_run_learning_agent_uses_llm_proposals(self, mock_analyst) -> None:
        mock_analyst.return_value = {
            "insights": [
                {
                    "insight_type": "pattern_insight",
                    "title": "LLM insight",
                    "pattern_summary": "Unique LLM pattern summary",
                    "rationale": "from analyst",
                    "confidence": 0.85,
                    "merchant_key": "",
                    "proposal_json": {},
                }
            ],
            "tool_rounds": 1,
            "model": "test-model",
            "trace": [],
        }
        from webapp.services import learning_agent as la

        la.LEARNING_AGENT_ENABLED = True
        try:
            result = run_learning_agent(self.db_path, force=True)
        finally:
            la.LEARNING_AGENT_ENABLED = False
        self.assertEqual(result["llm_insights"], 1)
        self.assertGreaterEqual(result["insights_inserted"], 1)
        row = self.conn.execute(
            "SELECT pattern_summary FROM ai_insights WHERE pattern_summary LIKE 'Unique LLM%'"
        ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
