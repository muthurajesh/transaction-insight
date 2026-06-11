import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from transaction_insight.core import MERCHANT_CATEGORIES_SHEET, MERCHANT_CATEGORY_COLUMNS, open_excel_workbook
from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.review_suggest import (
    REVIEW_SUGGEST_BATCH_LIMITS,
    suggest_labels_bulk,
    suggest_labels_for_item,
    suggest_labels_for_merchant,
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
            classification, label_status, simple_description, imported_at
        ) VALUES
        ('t1', '2025-01-01', '2025-01', -15.99, 'Netflix', 'Other', '', 'Expense', 'Variable', 'Personal', 'needs_review', 'NETFLIX.COM', 'now'),
        ('t2', '2025-02-01', '2025-02', -15.99, 'Spotify', 'Other', '', 'Expense', 'Variable', 'Personal', 'needs_review', 'SPOTIFY', 'now'),
        ('t3', '2025-03-01', '2025-03', -60.0, 'Check Payment', 'Other', '', 'Expense', 'Variable', 'Personal', 'needs_review', 'CHECK 123', 'now')
        """
    )
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type, label_status, updated_at
        ) VALUES ('Hulu', 'Entertainment', 'Streaming', 'Fixed', 'confirmed', 'now')
        """
    )
    conn.commit()
    return conn


def _write_merchant_lookup(path: Path, merchant_key: str, category: str) -> None:
    frame = pd.DataFrame(
        [
            {
                "Merchant Key": merchant_key,
                "AI Category": category,
                "AI Sub-Category": "Streaming",
                "Budget Tier": "Want",
                "Type": "Fixed",
                "Flow Type": "Expense",
                "Classification": "Personal",
                "Transaction Count": 1,
                "Notes": "",
            }
        ],
        columns=list(MERCHANT_CATEGORY_COLUMNS),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_excel_workbook(path) as writer:
        frame.to_excel(writer, sheet_name=MERCHANT_CATEGORIES_SHEET, index=False)


class ReviewSuggestTests(unittest.TestCase):
    def test_lookup_hit_skips_llm(self):
        conn = _conn()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookups.xlsx"
            _write_merchant_lookup(path, "Netflix", "Subscriptions")
            with patch(
                "webapp.services.review_suggest.lookup_workbook_path",
                return_value=path,
            ):
                with patch("webapp.services.review_suggest.chat_completion") as mock_llm:
                    item = {
                        "merchant_key": "Netflix",
                        "review_mode": "merchant",
                        "ai_category": "Other",
                        "expense_type": "Variable",
                        "flow_type": "Expense",
                        "classification": "Personal",
                    }
                    result = suggest_labels_for_item(conn, item)
        mock_llm.assert_not_called()
        self.assertEqual(result["labels"]["ai_category"], "Subscriptions")
        self.assertEqual(result["source"], MERCHANT_CATEGORIES_SHEET)
        self.assertEqual(result["confidence"], "high")

    def test_sqlite_confirmed_merchant_lookup(self):
        conn = _conn()
        with patch("webapp.services.review_suggest._merchant_row_from_excel", return_value=None):
            with patch("webapp.services.review_suggest.chat_completion") as mock_llm:
                item = {
                    "merchant_key": "Hulu",
                    "review_mode": "merchant",
                    "ai_category": "Other",
                    "expense_type": "Variable",
                    "flow_type": "Expense",
                    "classification": "Personal",
                }
                result = suggest_labels_for_item(conn, item)
        mock_llm.assert_not_called()
        self.assertEqual(result["labels"]["ai_category"], "Entertainment")
        self.assertEqual(result["source"], "merchant_labels")

    def test_bulk_limit_enforced(self):
        conn = _conn()
        with patch("webapp.services.review_suggest.suggest_labels_for_item") as mock_item:
            mock_item.return_value = {
                "merchant_key": "x",
                "labels": {"ai_category": "A"},
                "confidence": "high",
                "source": "merchant_labels",
                "rationale": "test",
            }
            result = suggest_labels_bulk(conn, limit=10)
        self.assertEqual(result["limit"], 10)
        self.assertEqual(result["processed"], 3)
        self.assertEqual(mock_item.call_count, 3)

    def test_bulk_invalid_limit(self):
        conn = _conn()
        with self.assertRaises(ValueError):
            suggest_labels_bulk(conn, limit=5)

    def test_bulk_accepts_50_and_100(self):
        conn = _conn()
        self.assertEqual(REVIEW_SUGGEST_BATCH_LIMITS, frozenset({10, 25, 50, 100}))
        with patch("webapp.services.review_suggest.suggest_labels_for_item") as mock_item:
            mock_item.return_value = {
                "merchant_key": "x",
                "labels": {"ai_category": "A"},
                "confidence": "high",
                "source": "merchant_labels",
                "rationale": "test",
            }
            for limit in (50, 100):
                mock_item.reset_mock()
                result = suggest_labels_bulk(conn, limit=limit)
                self.assertEqual(result["limit"], limit)
                self.assertEqual(mock_item.call_count, 3)

    def test_suggest_for_merchant_key_resolves_item(self):
        conn = _conn()
        with patch("webapp.services.review_suggest.suggest_labels_for_item") as mock_item:
            mock_item.return_value = {"merchant_key": "Spotify", "labels": {}, "source": "llm"}
            result = suggest_labels_for_merchant(conn, "Spotify")
        self.assertEqual(result["merchant_key"], "Spotify")
        mock_item.assert_called_once()

    def test_business_expenses_lookup_sets_business_classification(self):
        conn = _conn()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookups.xlsx"
            _write_merchant_lookup(path, "Netflix", "Business Expenses")
            with patch(
                "webapp.services.review_suggest.lookup_workbook_path",
                return_value=path,
            ):
                with patch("webapp.services.review_suggest.chat_completion") as mock_llm:
                    item = {
                        "merchant_key": "Netflix",
                        "review_mode": "merchant",
                        "ai_category": "Subscriptions",
                        "expense_type": "Variable",
                        "flow_type": "Expense",
                        "classification": "Personal",
                    }
                    result = suggest_labels_for_item(conn, item)
        mock_llm.assert_not_called()
        self.assertEqual(result["labels"]["ai_category"], "Business Expenses")
        self.assertEqual(result["labels"]["classification"], "Business")

    def test_suggest_for_check_payment_transaction(self):
        conn = _conn()
        with patch("webapp.services.review_suggest.suggest_labels_for_item") as mock_item:
            mock_item.return_value = {
                "merchant_key": "Check Payment",
                "transaction_id": "t3",
                "labels": {},
                "source": "llm",
            }
            result = suggest_labels_for_merchant(conn, "Check Payment", transaction_id="t3")
        self.assertEqual(result["transaction_id"], "t3")


if __name__ == "__main__":
    unittest.main()
