"""Tests for shared classification vocabulary."""

from __future__ import annotations

import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.classification_vocabulary import (
    format_vocabulary_prompt_block,
    labels_equivalent,
    load_classification_vocabulary,
    normalize_classify_labels,
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
            ai_category, ai_sub_category, flow_type, label_status, imported_at
        ) VALUES ('tx-1', '2026-01-01', '2026-01', -10.0, 'Trader Joe''s',
                  'Food/Dining', 'Groceries', 'Expense', 'pending', 'now')
        """
    )
    conn.commit()
    return conn


class ClassificationVocabularyTests(unittest.TestCase):
    def test_load_vocabulary_from_db(self):
        conn = _conn()
        vocab = load_classification_vocabulary(conn)
        self.assertIn("Food/Dining", vocab["categories"])
        self.assertIn("Groceries", vocab["sub_categories"])
        self.assertIn("Groceries", vocab["sub_categories_by_category"]["Food/Dining"])

    def test_format_prompt_block_includes_categories(self):
        block = format_vocabulary_prompt_block(
            {
                "categories": ["Food/Dining"],
                "sub_categories_by_category": {"Food/Dining": ["Groceries"]},
            }
        )
        self.assertIn("Food/Dining", block)
        self.assertIn("Groceries", block)

    def test_normalize_maps_grocery_to_groceries(self):
        vocab = {
            "categories": ["Food/Dining"],
            "sub_categories": ["Groceries"],
            "sub_categories_by_category": {"Food/Dining": ["Groceries"]},
        }
        cat, sub = normalize_classify_labels("food/dining", "Grocery", vocab)
        self.assertEqual(cat, "Food/Dining")
        self.assertEqual(sub, "Groceries")

    def test_labels_equivalent_after_normalize(self):
        vocab = {
            "categories": ["Food/Dining"],
            "sub_categories": ["Groceries"],
            "sub_categories_by_category": {"Food/Dining": ["Groceries"]},
        }
        self.assertTrue(
            labels_equivalent(
                "Food/Dining",
                "Grocery",
                "Food/Dining",
                "Groceries",
                vocab,
            )
        )
        self.assertFalse(
            labels_equivalent(
                "Food/Dining",
                "Groceries",
                "Home",
                "Groceries",
                vocab,
            )
        )


if __name__ == "__main__":
    unittest.main()
