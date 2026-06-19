import sqlite3
import unittest

from webapp.agent.db_query import execute_readonly_sql, validate_readonly_sql
from webapp.db.schema import SCHEMA_SQL, _migrate_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            flow_type, imported_at
        ) VALUES ('t1', '2026-01-01', '2026-01', -10.0, 'Test', 'Expense', 'now')
        """
    )
    conn.commit()
    return conn


class ValidateReadonlySqlTests(unittest.TestCase):
    def test_select_allowed(self):
        sql = validate_readonly_sql(
            "SELECT budget_month, SUM(-amount) FROM transactions "
            "WHERE flow_type = 'Expense' GROUP BY budget_month"
        )
        self.assertIn("transactions", sql)

    def test_with_cte_allowed(self):
        validate_readonly_sql(
            "WITH m AS (SELECT budget_month FROM transactions) SELECT * FROM m"
        )

    def test_insert_rejected(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("INSERT INTO transactions VALUES (1)")

    def test_update_rejected(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("UPDATE transactions SET amount = 0")

    def test_delete_rejected(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("DELETE FROM transactions")

    def test_drop_rejected(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("DROP TABLE transactions")

    def test_multiple_statements_rejected(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("SELECT 1; SELECT 2")

    def test_chat_messages_table_blocked(self):
        with self.assertRaises(ValueError) as ctx:
            validate_readonly_sql("SELECT * FROM chat_messages")
        self.assertIn("not allowed", str(ctx.exception).lower())

    def test_sqlite_master_blocked(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("SELECT name FROM sqlite_master")

    def test_keyword_in_string_literal_allowed(self):
        validate_readonly_sql("SELECT 'DELETE' AS note FROM transactions LIMIT 1")

    def test_case_when_end_allowed(self):
        validate_readonly_sql(
            "SELECT SUM(CASE WHEN ai_category = 'Groceries' THEN -amount ELSE 0 END) "
            "FROM transactions WHERE flow_type = 'Expense'"
        )

    def test_begin_transaction_rejected(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("BEGIN TRANSACTION")


class ExecuteReadonlySqlTests(unittest.TestCase):
    def test_returns_rows(self):
        conn = _conn()
        result = execute_readonly_sql(
            conn,
            "SELECT COUNT(*) AS c FROM transactions WHERE flow_type = 'Expense'",
        )
        self.assertEqual(result["rows"][0]["c"], 1)


if __name__ == "__main__":
    unittest.main()
