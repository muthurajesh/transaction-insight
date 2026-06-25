import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.review_confirm import (
    _labels_from_proposed,
    confirm_merchant_group,
    confirm_preview,
    find_lookup_conflicts,
    sync_merchant_labels_to_db,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, ai_sub_category, flow_type, expense_type,
            classification, label_status, imported_at
        ) VALUES
        ('t1', '2025-01-01', '2025-01', -10.0, 'Netflix', 'Other', '', 'Expense', 'Variable', 'Personal', 'needs_review', 'now'),
        ('t2', '2025-02-01', '2025-02', -10.0, 'Netflix', 'Entertainment', 'Streaming', 'Expense', 'Fixed', 'Personal', 'confirmed', 'now'),
        ('t3', '2025-03-01', '2025-03', -10.0, 'Netflix', 'Other', '', 'Expense', 'Variable', 'Personal', 'pending', 'now')
        """
    )
    conn.commit()
    return conn


class ReviewConfirmTests(unittest.TestCase):
    def test_business_expenses_aligns_classification(self):
        labels = _labels_from_proposed(
            ai_category="Business Expenses",
            ai_sub_category="Software subscription",
            classification="Personal",
        )
        self.assertEqual(labels["classification"], "Business")

    def test_preview_counts_pending_and_differing(self):
        conn = _conn()
        preview = confirm_preview(
            conn,
            "Netflix",
            ai_category="Subscriptions",
            ai_sub_category="Streaming",
            expense_type="Fixed",
            flow_type="Expense",
            classification="Personal",
        )
        self.assertEqual(preview["pending_count"], 2)
        self.assertEqual(preview["total_count"], 3)
        self.assertEqual(preview["differing_confirmed_count"], 1)
        self.assertTrue(preview["suggest_custom_rule"])

    def test_confirm_pending_scope(self):
        conn = _conn()
        result = confirm_merchant_group(
            conn,
            "Netflix",
            ai_category="Subscriptions",
            ai_sub_category="Streaming",
            expense_type="Fixed",
            flow_type="Expense",
            classification="Personal",
            scope="pending",
        )
        self.assertEqual(result["rows_updated"], 2)
        row = conn.execute(
            "SELECT ai_category, label_status FROM transactions WHERE transaction_id = 't2'"
        ).fetchone()
        self.assertEqual(row["ai_category"], "Entertainment")
        self.assertEqual(row["label_status"], "confirmed")

    def test_confirm_all_scope(self):
        conn = _conn()
        result = confirm_merchant_group(
            conn,
            "Netflix",
            ai_category="Subscriptions",
            ai_sub_category="Streaming",
            expense_type="Fixed",
            flow_type="Expense",
            classification="Personal",
            scope="all",
        )
        self.assertEqual(result["rows_updated"], 3)
        row = conn.execute(
            "SELECT ai_category FROM transactions WHERE transaction_id = 't2'"
        ).fetchone()
        self.assertEqual(row["ai_category"], "Subscriptions")

    def test_lookup_conflict_requires_replace(self):
        conn = _conn()
        proposed = {
            "ai_category": "Subscriptions",
            "ai_sub_category": "Streaming",
            "expense_type": "Fixed",
            "flow_type": "Expense",
            "classification": "Personal",
        }
        sync_merchant_labels_to_db(
            conn,
            "Netflix",
            {
                "ai_category": "Entertainment",
                "ai_sub_category": "Streaming",
                "expense_type": "Fixed",
                "flow_type": "Expense",
                "classification": "Personal",
            },
            tx_count=2,
        )
        conn.commit()

        conflicts = find_lookup_conflicts(conn, "Netflix", proposed)
        self.assertEqual(len(conflicts), 1)

        with self.assertRaises(ValueError):
            confirm_merchant_group(
                conn,
                "Netflix",
                ai_category="Subscriptions",
                ai_sub_category="Streaming",
                scope="pending",
                replace_conflicting_rule=False,
            )

        result = confirm_merchant_group(
            conn,
            "Netflix",
            ai_category="Subscriptions",
            ai_sub_category="Streaming",
            scope="pending",
            replace_conflicting_rule=True,
        )
        self.assertTrue(result["lookup_conflicts_replaced"])
        label = conn.execute(
            "SELECT ai_category FROM merchant_labels WHERE merchant_key = 'Netflix'"
        ).fetchone()
        self.assertEqual(label["ai_category"], "Subscriptions")

    def test_sync_writes_flow_type_and_classification(self):
        conn = _conn()
        proposed = {
            "ai_category": "Utilities",
            "ai_sub_category": "Electric",
            "expense_type": "Fixed",
            "flow_type": "Expense",
            "classification": "Personal",
        }
        sync_merchant_labels_to_db(conn, "Acme Power", proposed, tx_count=5)
        conn.commit()
        row = conn.execute(
            "SELECT flow_type, classification FROM merchant_labels WHERE merchant_key = 'Acme Power'"
        ).fetchone()
        self.assertEqual(row["flow_type"], "Expense")
        self.assertEqual(row["classification"], "Personal")


if __name__ == "__main__":
    unittest.main()
