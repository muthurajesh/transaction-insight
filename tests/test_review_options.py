import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.review_options import get_review_options


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


class ReviewOptionsTests(unittest.TestCase):
    def test_sub_categories_by_category_maps_pairs(self):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                ai_category, ai_sub_category, flow_type, label_status, imported_at
            ) VALUES
            ('t1', '2026-01-01', '2026-01', -10, 'Netflix', 'Entertainment', 'Streaming Services', 'Expense', 'confirmed', 'now'),
            ('t2', '2026-01-02', '2026-01', -20, 'AMC', 'Entertainment', 'Movies', 'Expense', 'confirmed', 'now'),
            ('t3', '2026-01-03', '2026-01', -30, 'PG&E', 'Utilities', 'Electric', 'Expense', 'confirmed', 'now')
            """
        )
        conn.commit()

        options = get_review_options(conn)
        by_cat = options["sub_categories_by_category"]
        self.assertEqual(by_cat["Entertainment"], ["Movies", "Streaming Services"])
        self.assertEqual(by_cat["Utilities"], ["Electric"])
        self.assertIn("Streaming Services", options["sub_categories"])
        self.assertEqual(options["classifications"], ["Business", "Personal"])

    def test_classifications_include_structural_enum_without_business_rows(self):
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                classification, flow_type, label_status, imported_at
            ) VALUES ('t1', '2026-01-01', '2026-01', -10, 'Merchant A', 'Personal', 'Expense', 'confirmed', 'now')
            """
        )
        conn.commit()
        options = get_review_options(conn)
        self.assertEqual(options["classifications"], ["Business", "Personal"])


if __name__ == "__main__":
    unittest.main()
