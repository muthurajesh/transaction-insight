import json
import sqlite3
import unittest
from unittest.mock import patch

from webapp.agent.chat import (
    _db_category_context,
    _maybe_direct_answer,
    chat,
)
from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.analytics import category_average_last_n_full_months


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _seed_dining(conn: sqlite3.Connection) -> None:
    amounts = {
        "2026-05": 100.0,
        "2026-04": 200.0,
        "2026-03": 300.0,
        "2026-02": 400.0,
        "2026-01": 500.0,
    }
    idx = 0
    for bm, dining_amt in amounts.items():
        for i in range(25):
            idx += 1
            amt = dining_amt if i == 0 else 1.0
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, date, budget_month, amount, merchant_key,
                    ai_category, flow_type, imported_at
                ) VALUES (?, ?, ?, ?, 'Test Cafe', 'Dining', 'Expense', 'now')
                """,
                (f"t{idx}", f"{bm}-01", bm, -amt),
            )
    conn.commit()


class LlmFirstRoutingTests(unittest.TestCase):
    def test_dining_average_not_short_circuited(self):
        conn = _conn()
        _seed_dining(conn)
        msg = "In the last 5 months, what is my average expense for eating outside"
        self.assertIsNone(_maybe_direct_answer(conn, msg))

    def test_list_custom_reports_still_short_circuits(self):
        conn = _conn()
        payload = _maybe_direct_answer(conn, "show my saved custom reports")
        self.assertIsNotNone(payload)
        self.assertIn("custom report", payload["answer"].lower())

    def test_category_context_lists_labels(self):
        conn = _conn()
        _seed_dining(conn)
        text = _db_category_context(conn)
        self.assertIn("Dining", text)


class CategoryAverageAnalyticsTests(unittest.TestCase):
    def test_average_over_last_three_full_months(self):
        conn = _conn()
        _seed_dining(conn)
        result = category_average_last_n_full_months(
            conn, categories=["Dining"], month_count=3
        )
        self.assertEqual(result["month_count"], 3)
        self.assertEqual(result["average_spend"], 224.0)


class ChatReadOnlyToolTests(unittest.TestCase):
    def test_save_custom_report_blocked(self):
        conn = _conn()
        with self.assertRaises(ValueError) as ctx:
            from webapp.agent.tools import run_tool

            run_tool(
                conn,
                "save_custom_report",
                {"name": "x", "sql_template": "SELECT 1"},
            )
        self.assertIn("read-only", str(ctx.exception).lower())

    def test_delete_custom_report_blocked(self):
        conn = _conn()
        with self.assertRaises(ValueError):
            from webapp.agent.tools import run_tool

            run_tool(conn, "delete_custom_report", {"report": "x"})


class ChatAgentLoopTests(unittest.TestCase):
    @patch("webapp.agent.chat.chat_completion")
    def test_llm_synthesizes_after_query_sql(self, mock_llm):
        conn = _conn()
        _seed_dining(conn)
        sql = """
            SELECT budget_month, ROUND(SUM(-amount), 2) AS spend
            FROM transactions
            WHERE flow_type = 'Expense' AND amount < 0 AND ai_category = 'Dining'
            GROUP BY budget_month
            ORDER BY budget_month DESC
            LIMIT 5
        """
        mock_llm.side_effect = [
            json.dumps({"tool": "query_sql", "args": {"sql": sql}}),
            json.dumps(
                {
                    "answer": (
                        "Over the last 5 full months, your average **Dining** spend "
                        "is **$224.00** per month."
                    )
                }
            ),
        ]
        result = chat(
            conn, "In the last 5 months, what is my average expense for eating outside"
        )
        self.assertIn("224", result["answer"])
        self.assertEqual(mock_llm.call_count, 2)
        self.assertEqual(result["tool_trace"][0]["tool"], "query_sql")
        self.assertNotIn("867", result["answer"])


if __name__ == "__main__":
    unittest.main()
