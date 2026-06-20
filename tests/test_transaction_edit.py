import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.transaction_edit import bulk_update_labels, get_transaction, search_transactions


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


if __name__ == "__main__":
    unittest.main()
