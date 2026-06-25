import json
import sqlite3
import unittest
from unittest.mock import patch

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services import custom_reports as cr


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


class CustomReportsServiceTests(unittest.TestCase):
    def test_save_and_run_with_month_param(self):
        conn = _conn()
        sql = """
            SELECT ai_category AS category, ROUND(SUM(-amount), 2) AS spend
            FROM transactions
            WHERE budget_month = :month AND flow_type = 'Expense' AND amount < 0
            GROUP BY ai_category
        """
        saved = cr.save_custom_report(
            conn,
            name="May by category",
            sql_template=sql,
            report_prompt="Group expenses by category for one month.",
            parameters=["month"],
        )
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                ai_category, flow_type, imported_at
            ) VALUES ('t1', '2026-05-01', '2026-05', -50, 'Cafe', 'Dining', 'Expense', 'now')
            """
        )
        conn.commit()
        result = cr.run_custom_report(conn, saved["report_id"], params={"month": "2026-05"})
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0]["category"], "Dining")
        self.assertEqual(result["rows"][0]["spend"], 50.0)

    def test_fork_increments_version(self):
        conn = _conn()
        parent = cr.save_custom_report(
            conn,
            name="Base report",
            sql_template="SELECT 1 AS n",
            report_prompt="Test",
        )
        child = cr.fork_custom_report(conn, parent["report_id"], new_name="Base report v2")
        self.assertEqual(child["parent_report_id"], parent["report_id"])
        self.assertGreater(child["version"], parent["version"])

    def test_update_rename(self):
        conn = _conn()
        saved = cr.save_custom_report(
            conn,
            name="Old name",
            sql_template="SELECT 1 AS n",
        )
        updated = cr.update_custom_report(conn, saved["report_id"], name="New name")
        self.assertEqual(updated["name"], "New name")

    def test_extract_sql_from_tool_trace(self):
        trace = [
            {
                "tool": "query_sql",
                "args": {"sql": "SELECT 1 AS n"},
                "result": {"rows": [{"n": 1}], "columns": ["n"]},
            }
        ]
        sql = cr.extract_sql_from_tool_trace(trace)
        self.assertIn("SELECT 1", sql or "")

    @patch("webapp.services.llm.chat_completion")
    def test_finalize_report_from_conversation(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {
                "report_prompt": "Monthly category rollup excluding business.",
                "description": "Core monthly expenses",
                "report_config": {
                    "expense_view": "cash",
                    "display": {"show_grand_total": True},
                    "chart": {"enabled": True, "type": "bar"},
                },
            }
        )
        out = cr.finalize_report_from_conversation(
            sql_template="SELECT ai_category, SUM(-amount) AS spend FROM transactions GROUP BY ai_category",
            conversation_summary="Show consistent monthly expense without business",
        )
        self.assertIn("business", out["report_prompt"].lower())
        self.assertTrue(out["report_config"]["chart"]["enabled"])


if __name__ == "__main__":
    unittest.main()
