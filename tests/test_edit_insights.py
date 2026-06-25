"""Tests for edit insight after bulk label apply."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.edit_insights import analyze_edit


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, flow_type, label_status, imported_at
        ) VALUES ('tx-1', '2026-01-01', '2026-01', -42.99, 'Test Merchant', 'Old', 'Expense', 'pending', 'now')
        """
    )
    conn.commit()
    return conn


class EditInsightTests(unittest.TestCase):
    def test_analyze_edit_does_not_call_list_custom_rules_without_conn(self):
        conn = _conn()
        with patch("webapp.services.edit_insights.chat_completion", side_effect=RuntimeError("no llm")):
            result = analyze_edit(
                conn,
                merchant_key="Test Merchant",
                scope="merchant",
                rows_updated=1,
                before={"ai_category": "Old", "ai_sub_category": "", "expense_type": "Variable", "classification": "Personal"},
                after={"ai_category": "New", "ai_sub_category": "Sub", "expense_type": "Variable", "classification": "Personal"},
            )
        self.assertIn("insight", result)
        self.assertEqual(result["source"], "fallback")


if __name__ == "__main__":
    unittest.main()
