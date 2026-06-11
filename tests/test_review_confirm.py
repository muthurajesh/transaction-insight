import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from transaction_insight.core import MERCHANT_CATEGORIES_SHEET, MERCHANT_CATEGORY_COLUMNS, open_excel_workbook
from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.review_confirm import (
    _labels_from_proposed,
    confirm_merchant_group,
    confirm_preview,
    find_excel_conflicts,
    sync_merchant_labels_to_workbook,
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
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookups.xlsx"
            from unittest.mock import patch

            with patch(
                "webapp.services.review_confirm.lookup_workbook_path",
                return_value=path,
            ):
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
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookups.xlsx"
            from unittest.mock import patch

            with patch(
                "webapp.services.review_confirm.lookup_workbook_path",
                return_value=path,
            ):
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

    def test_excel_conflict_requires_replace(self):
        conn = _conn()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookups.xlsx"
            frame = pd.DataFrame(
                [
                    {
                        "Merchant Key": "Netflix",
                        "AI Category": "Entertainment",
                        "AI Sub-Category": "Streaming",
                        "Budget Tier": "Want",
                        "Type": "Fixed",
                        "Flow Type": "Expense",
                        "Classification": "Personal",
                        "Transaction Count": 2,
                        "Notes": "",
                    }
                ],
                columns=list(MERCHANT_CATEGORY_COLUMNS),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            with open_excel_workbook(path) as writer:
                frame.to_excel(writer, sheet_name=MERCHANT_CATEGORIES_SHEET, index=False)

            proposed = {
                "ai_category": "Subscriptions",
                "ai_sub_category": "Streaming",
                "expense_type": "Fixed",
                "flow_type": "Expense",
                "classification": "Personal",
            }
            conflicts = find_excel_conflicts("Netflix", proposed, lookup_path=path)
            self.assertEqual(len(conflicts), 1)

            from unittest.mock import patch

            with patch(
                "webapp.services.review_confirm.lookup_workbook_path",
                return_value=path,
            ):
                with self.assertRaises(ValueError):
                    confirm_merchant_group(
                        conn,
                        "Netflix",
                        ai_category="Subscriptions",
                        ai_sub_category="Streaming",
                        scope="pending",
                        replace_excel=False,
                    )

                result = confirm_merchant_group(
                    conn,
                    "Netflix",
                    ai_category="Subscriptions",
                    ai_sub_category="Streaming",
                    scope="pending",
                    replace_excel=True,
                )
            self.assertTrue(result["excel_conflicts_replaced"])

            xl = pd.read_excel(path, sheet_name=MERCHANT_CATEGORIES_SHEET)
            self.assertEqual(str(xl.iloc[0]["AI Category"]), "Subscriptions")

    def test_sync_writes_flow_type_and_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookups.xlsx"
            proposed = {
                "ai_category": "Utilities",
                "ai_sub_category": "Electric",
                "expense_type": "Fixed",
                "flow_type": "Expense",
                "classification": "Personal",
            }
            sync_merchant_labels_to_workbook("Acme Power", proposed, lookup_path=path, tx_count=5)
            xl = pd.read_excel(path, sheet_name=MERCHANT_CATEGORIES_SHEET)
            self.assertEqual(str(xl.iloc[0]["Flow Type"]), "Expense")
            self.assertEqual(str(xl.iloc[0]["Classification"]), "Personal")


if __name__ == "__main__":
    unittest.main()
