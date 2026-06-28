"""Tests for decision memory, pending inbox, and learning agent."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from webapp.db.schema import get_connection, init_db
from webapp.services.decision_events import (
    infer_confirm_action,
    log_decision_event,
    list_recent_events,
)
from webapp.services.pending_confirmations import list_pending_confirmations
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


if __name__ == "__main__":
    unittest.main()
