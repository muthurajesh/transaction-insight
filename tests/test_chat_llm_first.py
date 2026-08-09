import json
import sqlite3
import unittest
from unittest.mock import patch

from webapp.agent.chat import (
    _db_category_context,
    _maybe_direct_answer,
    chat,
)
from webapp.agent.tools import run_tool
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

    def test_compare_not_short_circuited(self):
        conn = _conn()
        _seed_dining(conn)
        msg = "compare April and May 2026 expenses by categories"
        self.assertIsNone(_maybe_direct_answer(conn, msg))

    def test_list_custom_reports_still_short_circuits(self):
        conn = _conn()
        payload = _maybe_direct_answer(conn, "show my saved custom reports")
        self.assertIsNotNone(payload)
        self.assertIn("custom report", payload["answer"].lower())

    def test_check_labels_intent_short_circuits(self):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                ai_category, flow_type, label_status, imported_at
            ) VALUES (
                't1', '2026-05-01', '2026-05', -10, 'Cafe A',
                'Dining', 'Expense', 'needs_review', 'now'
            )
            """
        )
        conn.commit()
        payload = _maybe_direct_answer(conn, "help me review pending labels")
        self.assertIsNotNone(payload)
        self.assertIn("check labels", payload["answer"].lower())
        self.assertNotIn("chart", payload["answer"].lower())

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
    def test_helper_tools_blocked_in_chat_mode(self):
        conn = _conn()
        with self.assertRaises(ValueError) as ctx:
            run_tool(conn, "top_categories", {"month": "2026-04"}, chat_mode=True)
        self.assertIn("query_sql", str(ctx.exception))

    def test_save_custom_report_blocked(self):
        conn = _conn()
        with self.assertRaises(ValueError) as ctx:
            run_tool(
                conn,
                "save_custom_report",
                {"name": "x", "sql_template": "SELECT 1"},
                chat_mode=True,
            )
        self.assertIn("Unknown tool", str(ctx.exception))


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

    @patch("webapp.agent.chat.chat_completion")
    def test_compare_rejects_bad_sql_then_retries(self, mock_llm):
        conn = _conn()
        _seed_dining(conn)
        bad_sql = """
            SELECT ai_category, ROUND(SUM(-amount), 2) AS total
            FROM transactions
            WHERE flow_type='Expense' AND amount<0
              AND budget_month IN ('2026-04','2026-05')
            GROUP BY ai_category
        """
        good_sql = """
            SELECT ai_category,
              ROUND(SUM(CASE WHEN budget_month='2026-04' THEN -amount ELSE 0 END), 2) AS apr,
              ROUND(SUM(CASE WHEN budget_month='2026-05' THEN -amount ELSE 0 END), 2) AS may
            FROM transactions
            WHERE flow_type='Expense' AND amount<0
              AND budget_month IN ('2026-04','2026-05')
            GROUP BY ai_category
        """
        mock_llm.side_effect = [
            json.dumps({"tool": "query_sql", "args": {"sql": bad_sql}}),
            json.dumps({"tool": "query_sql", "args": {"sql": good_sql}}),
            json.dumps({"answer": "April vs May comparison by category is in the table."}),
        ]
        result = chat(conn, "compare April and May 2026 expenses by categories")
        self.assertEqual(result["tool_trace"][0]["result"].get("validation_rejected"), True)
        self.assertEqual(result["tool_trace"][1]["tool"], "query_sql")
        self.assertIsNone(result["tool_trace"][1]["result"].get("validation_rejected"))

    @patch("webapp.agent.chat.chat_completion")
    def test_follow_up_includes_prior_turns(self, mock_llm):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO chat_messages (role, content, tool_trace, created_at)
            VALUES ('user', 'Draft a custom rule for allview rental income', NULL, 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO chat_messages (role, content, tool_trace, created_at)
            VALUES ('assistant', 'Would you like me to draft a revised rule?', NULL, 'now')
            """
        )
        conn.commit()
        mock_llm.return_value = json.dumps(
            {"answer": "Here is a revised rule that matches description text containing allview."}
        )
        chat(conn, "Yes please")
        first_messages = mock_llm.call_args_list[0][0][0]
        self.assertGreaterEqual(len(first_messages), 4)
        self.assertEqual(first_messages[0]["role"], "system")
        self.assertEqual(first_messages[1]["content"], "Draft a custom rule for allview rental income")
        self.assertEqual(first_messages[2]["content"], "Would you like me to draft a revised rule?")
        self.assertIn("Yes please", first_messages[-1]["content"])
        self.assertIn("allview rental income", first_messages[1]["content"].lower())

    @patch("webapp.agent.chat.chat_completion")
    def test_prose_with_sql_fence_runs_the_query(self, mock_llm):
        conn = _conn()
        _seed_dining(conn)
        mock_llm.side_effect = [
            "Here are the top spending categories for April 2026:\n\n"
            "```sql\n"
            "SELECT ai_category, ROUND(SUM(-amount), 2) AS spend\n"
            "FROM transactions\n"
            "WHERE flow_type = 'Expense' AND amount < 0 AND budget_month = '2026-04'\n"
            "GROUP BY ai_category ORDER BY spend DESC;\n"
            "```\n\n"
            "- **Dining**: $9,999.00\n",
            json.dumps({"answer": "Dining was $224.00 in April 2026."}),
        ]
        result = chat(conn, "What are my top spending categories for April 2026?")
        self.assertEqual(result["tool_trace"][0]["tool"], "query_sql")
        self.assertIn("2026-04", result["tool_trace"][0]["args"]["sql"])
        self.assertTrue(result["answer"].strip())

    @patch("webapp.agent.chat.chat_completion")
    def test_answer_never_blank_when_model_will_not_call_tool(self, mock_llm):
        conn = _conn()
        _seed_dining(conn)
        mock_llm.return_value = "I think you spent a lot on food last month."
        result = chat(conn, "What are my top spending categories for April 2026?")
        self.assertTrue(result["answer"].strip())
        self.assertEqual(result["tool_trace"], [])


if __name__ == "__main__":
    unittest.main()
