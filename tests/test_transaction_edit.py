import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.transaction_edit import bulk_update_labels, get_transaction, list_result_columns, search_transactions


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


class TransactionEditMerchantTests(unittest.TestCase):
    def test_bulk_update_changes_merchant_key(self):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                simple_description, original_description,
                ai_category, ai_sub_category, flow_type, expense_type,
                classification, label_status, imported_at
            ) VALUES (
                't1', '2026-05-11', '2026-05', -470.08, 'Panda Express',
                'Payment to Tesla', 'Tesla Insurance Compan Fremont CA',
                'Dining', 'Fast food', 'Expense', 'Variable',
                'Personal', 'confirmed', 'now'
            )
            """
        )
        conn.commit()

        result = bulk_update_labels(
            conn,
            transaction_ids=["t1"],
            new_merchant_key="Tesla Insurance",
            ai_category="Insurance",
            ai_sub_category="Auto insurance",
            expense_type="Fixed",
            classification="Personal",
            update_merchant_label=True,
        )
        self.assertEqual(result["rows_updated"], 1)
        self.assertEqual(result["merchant_key"], "Tesla Insurance")

        row = get_transaction(conn, "t1")
        assert row is not None
        self.assertEqual(row["merchant_key"], "Tesla Insurance")
        self.assertEqual(row["ai_category"], "Insurance")

        label = conn.execute(
            "SELECT ai_category FROM merchant_labels WHERE merchant_key = ?",
            ("Tesla Insurance",),
        ).fetchone()
        self.assertIsNotNone(label)
        self.assertEqual(label["ai_category"], "Insurance")

    def test_search_includes_simple_description(self):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                simple_description, original_description,
                ai_category, flow_type, label_status, imported_at
            ) VALUES (
                't1', '2026-05-11', '2026-05', -10, 'Wrong Merchant',
                'Payment to Tesla', 'Tesla Insurance',
                'Other', 'Expense', 'confirmed', 'now'
            )
            """
        )
        conn.commit()

        data = search_transactions(conn, limit=10)
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["simple_description"], "Payment to Tesla")

    def test_search_label_status_needs_attention(self):
        conn = _conn()
        rows = [
            ("t1", "needs_review"),
            ("t2", "pending"),
            ("t3", "confirmed"),
        ]
        for tx_id, label_status in rows:
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, date, budget_month, amount, merchant_key,
                    simple_description, original_description,
                    ai_category, flow_type, label_status, imported_at
                ) VALUES (?, '2026-05-11', '2026-05', -10, ?, 'desc', 'orig',
                          'Cat', 'Expense', ?, 'now')
                """,
                (tx_id, f"Merchant {tx_id}", label_status),
            )
        conn.commit()

        data = search_transactions(conn, label_status="needs_attention", limit=10)
        ids = {r["transaction_id"] for r in data["transactions"]}
        self.assertEqual(ids, {"t1", "t2"})
        self.assertEqual(data["total"], 2)

        confirmed = search_transactions(conn, label_status="confirmed", limit=10)
        self.assertEqual(len(confirmed["transactions"]), 1)
        self.assertEqual(confirmed["transactions"][0]["transaction_id"], "t3")

    def test_search_sort_by_label_status_and_simple_description(self):
        conn = _conn()
        rows = [
            ("t1", "confirmed", "Zebra"),
            ("t2", "needs_review", "Apple"),
            ("t3", "pending", "Mango"),
        ]
        for tx_id, label_status, simple_desc in rows:
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, date, budget_month, amount, merchant_key,
                    simple_description, original_description,
                    ai_category, flow_type, label_status, imported_at
                ) VALUES (?, '2026-05-11', '2026-05', -10, ?, ?, 'orig',
                          'Cat', 'Expense', ?, 'now')
                """,
                (tx_id, f"Merchant {tx_id}", simple_desc, label_status),
            )
        conn.commit()

        by_status = search_transactions(conn, sort_by="label_status", sort_dir="asc", limit=10)
        self.assertEqual(
            [r["transaction_id"] for r in by_status["transactions"]],
            ["t1", "t2", "t3"],
        )

        by_desc = search_transactions(conn, sort_by="simple_description", sort_dir="asc", limit=10)
        self.assertEqual(
            [r["transaction_id"] for r in by_desc["transactions"]],
            ["t2", "t3", "t1"],
        )

    def test_search_flow_type_filter(self):
        conn = _conn()
        rows = [
            ("t1", "Expense"),
            ("t2", "Transfer"),
            ("t3", "Income"),
        ]
        for tx_id, flow_type in rows:
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, date, budget_month, amount, merchant_key,
                    simple_description, original_description,
                    ai_category, flow_type, label_status, imported_at
                ) VALUES (?, '2026-05-11', '2026-05', -10, ?, 'desc', 'orig',
                          'Cat', ?, 'confirmed', 'now')
                """,
                (tx_id, f"Merchant {tx_id}", flow_type),
            )
        conn.commit()

        data = search_transactions(conn, flow_type="Transfer", limit=10)
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["transaction_id"], "t2")
        self.assertEqual(data["total"], 1)


class TransactionEditColumnsTests(unittest.TestCase):
    def test_list_result_columns_includes_table_fields(self):
        cols = list_result_columns()
        keys = {c["key"] for c in cols}
        self.assertIn("date", keys)
        self.assertIn("merchant_key", keys)
        self.assertIn("source_file", keys)
        self.assertIn("rationale", keys)
        defaults = [c["key"] for c in cols if c.get("default_visible")]
        self.assertIn("date", defaults)
        self.assertIn("label_status", defaults)
        self.assertNotIn("transaction_id", defaults)

    def test_search_returns_extended_fields(self):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, source_file, date, budget_month, amount, merchant_key,
                simple_description, original_description, source_category, account_name,
                ai_category, flow_type, confidence, rationale, imported_at,
                label_status, cadence_source
            ) VALUES (
                't1', 'bank.csv', '2026-05-11', '2026-05', -10, 'Merchant A',
                'Simple', 'Original', 'Shopping', 'Checking',
                'Cat', 'Expense', 0.91, 'Test rationale', '2026-05-11T12:00:00Z',
                'confirmed', 'user'
            )
            """
        )
        conn.commit()
        row = search_transactions(conn, limit=1)["transactions"][0]
        self.assertEqual(row["source_file"], "bank.csv")
        self.assertEqual(row["source_category"], "Shopping")
        self.assertEqual(row["account_name"], "Checking")
        self.assertEqual(row["confidence"], 0.91)
        self.assertEqual(row["rationale"], "Test rationale")
        self.assertEqual(row["cadence_source"], "user")


if __name__ == "__main__":
    unittest.main()
