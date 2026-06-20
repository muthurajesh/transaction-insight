import unittest

from webapp.agent.answer_format import coalesce_answer_with_display, strip_markdown_tables
from webapp.agent.db_query import execute_readonly_sql
from webapp.agent.sql_intent import (
    detect_compare_intent,
    extract_budget_months,
    needs_database_answer,
    validate_query_sql,
)
from webapp.db.schema import SCHEMA_SQL, _migrate_schema
import sqlite3


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, flow_type, imported_at
        ) VALUES
        ('t1', '2026-04-01', '2026-04', -100, 'Cafe', 'Dining', 'Expense', 'now'),
        ('t2', '2026-05-01', '2026-05', -200, 'Cafe', 'Dining', 'Expense', 'now')
        """
    )
    conn.commit()
    return conn


class SqlIntentTests(unittest.TestCase):
    def test_extract_months_from_natural_language(self):
        months = extract_budget_months("compare April and May 2026 expenses by categories")
        self.assertEqual(months, ["2026-04", "2026-05"])

    def test_detect_compare_intent(self):
        self.assertTrue(
            detect_compare_intent("compare April and May 2026 expenses by categories")
        )
        self.assertFalse(detect_compare_intent("how much did I spend in May?"))

    def test_needs_database_answer(self):
        self.assertTrue(needs_database_answer("compare April and May spending"))
        self.assertFalse(needs_database_answer("show my saved custom reports"))

    def test_rejects_combined_month_query(self):
        conn = _conn()
        bad_sql = """
            SELECT ai_category, ROUND(SUM(-amount), 2) AS total
            FROM transactions
            WHERE flow_type='Expense' AND amount<0
              AND budget_month IN ('2026-04','2026-05')
            GROUP BY ai_category
        """
        result = execute_readonly_sql(conn, bad_sql)
        msg = "compare April and May 2026 expenses by categories"
        err = validate_query_sql(msg, bad_sql, result)
        self.assertIsNotNone(err)
        self.assertIn("month", err.lower())

    def test_accepts_pivot_compare_query(self):
        conn = _conn()
        good_sql = """
            SELECT ai_category,
              ROUND(SUM(CASE WHEN budget_month='2026-04' THEN -amount ELSE 0 END), 2) AS apr,
              ROUND(SUM(CASE WHEN budget_month='2026-05' THEN -amount ELSE 0 END), 2) AS may
            FROM transactions
            WHERE flow_type='Expense' AND amount<0
              AND budget_month IN ('2026-04','2026-05')
            GROUP BY ai_category
        """
        result = execute_readonly_sql(conn, good_sql)
        msg = "compare April and May 2026 expenses by categories"
        err = validate_query_sql(msg, good_sql, result)
        self.assertIsNone(err)

    def test_accepts_long_format_compare(self):
        conn = _conn()
        sql = """
            SELECT ai_category, budget_month, ROUND(SUM(-amount), 2) AS spend
            FROM transactions
            WHERE flow_type='Expense' AND amount<0
              AND budget_month IN ('2026-04','2026-05')
            GROUP BY ai_category, budget_month
        """
        result = execute_readonly_sql(conn, sql)
        msg = "compare April and May 2026 expenses by categories"
        self.assertIsNone(validate_query_sql(msg, sql, result))

    def test_strip_duplicate_markdown_table(self):
        answer = (
            "Here are the top 5 transactions:\n\n"
            "| Date | Amount |\n| --- | --- |\n| 2026-05-01 | -5.00 |\n\n"
            "These are the smallest amounts."
        )
        stripped = strip_markdown_tables(answer)
        self.assertNotIn("| Date |", stripped)
        self.assertIn("smallest amounts", stripped)

    def test_coalesce_keeps_prose_drops_table(self):
        display = {"type": "table", "title": "Query results", "rows": [{}], "summary": "5 row(s)"}
        answer = coalesce_answer_with_display(
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\nSummary text here.",
            display,
        )
        self.assertNotIn("| A |", answer)
        self.assertIn("Summary text here", answer)


if __name__ == "__main__":
    unittest.main()