import sqlite3
import unittest
from unittest.mock import patch

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.cadence_insights import gather_merchant_cadence_pattern
from webapp.services.edit_cadence_suggest import (
    count_merchants_needing_cadence,
    list_merchants_needing_cadence,
    suggest_cadence_bulk,
    suggest_cadence_for_merchant,
)
from webapp.services.expense_cadence import (
    CADENCE_KIND_LUMP,
    CADENCE_KIND_RECURRING,
    upsert_cadence_rule,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _seed_annual_insurance(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, flow_type, cadence_kind, cadence_source, imported_at
        ) VALUES
        ('t1', '2025-04-01', '2025-04', -900.00, 'InsurerCo Prem Pay', 'Insurance', 'Expense', 'unknown', 'pipeline', 'now'),
        ('t2', '2024-04-01', '2024-04', -890.00, 'InsurerCo Prem Pay', 'Insurance', 'Expense', 'unknown', 'pipeline', 'now'),
        ('t3', '2026-04-01', '2026-04', -900.00, 'InsurerCo Prem Pay', 'Insurance', 'Expense', 'unknown', 'pipeline', 'now'),
        ('t4', '2025-01-01', '2025-01', -14.99, 'Netflix', 'Entertainment', 'Expense', 'unknown', 'pipeline', 'now'),
        ('t5', '2025-02-01', '2025-02', -14.99, 'Netflix', 'Entertainment', 'Expense', 'unknown', 'pipeline', 'now'),
        ('t6', '2025-03-01', '2025-03', -14.99, 'Netflix', 'Entertainment', 'Expense', 'unknown', 'pipeline', 'now')
        """
    )
    conn.commit()


class GatherCadencePatternTests(unittest.TestCase):
    def test_yearly_pattern_detected(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        pattern = gather_merchant_cadence_pattern(conn, "InsurerCo Prem Pay")
        self.assertEqual(pattern["pattern"], "yearly")
        self.assertEqual(pattern["confidence"], "high")
        self.assertGreaterEqual(len(pattern["high_months"]), 2)


class EditCadenceSuggestQueueTests(unittest.TestCase):
    def test_count_merchants_needing_cadence(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        self.assertEqual(count_merchants_needing_cadence(conn), 2)

    def test_count_respects_category_filter(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        from webapp.services.edit_cadence_suggest import CadenceSuggestFilters

        self.assertEqual(
            count_merchants_needing_cadence(
                conn, filters=CadenceSuggestFilters(category="Insurance")
            ),
            1,
        )
        self.assertEqual(
            count_merchants_needing_cadence(
                conn, filters=CadenceSuggestFilters(category="Entertainment")
            ),
            1,
        )
        self.assertEqual(
            count_merchants_needing_cadence(
                conn, filters=CadenceSuggestFilters(classification="Business")
            ),
            0,
        )

    def test_skips_merchant_with_existing_rule(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        upsert_cadence_rule(
            conn,
            merchant_key="InsurerCo Prem Pay",
            cadence_kind="lump",
            period_count=12,
            period_unit="months",
            include_in_run_rate=False,
            notes="Annual",
            source="user",
        )
        conn.commit()
        merchants = list_merchants_needing_cadence(conn, limit=10)
        keys = {m["merchant_key"] for m in merchants}
        self.assertNotIn("InsurerCo Prem Pay", keys)
        self.assertIn("Netflix", keys)

    def test_list_merchants_respects_limit_and_offset(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        all_m = list_merchants_needing_cadence(conn, limit=10)
        self.assertEqual(len(all_m), 2)
        page = list_merchants_needing_cadence(conn, limit=1, offset=1)
        self.assertEqual(len(page), 1)
        self.assertNotEqual(page[0]["merchant_key"], all_m[0]["merchant_key"])

    def test_list_merchants_includes_ai_category(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        merchants = list_merchants_needing_cadence(conn, limit=10)
        by_key = {m["merchant_key"]: m for m in merchants}
        self.assertEqual(by_key["Netflix"]["ai_category"], "Entertainment")
        self.assertEqual(by_key["InsurerCo Prem Pay"]["ai_category"], "Insurance")

    def test_variable_skip_rule_removes_from_needing_cadence(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        upsert_cadence_rule(
            conn,
            merchant_key="Netflix",
            cadence_kind="unknown",
            include_in_run_rate=True,
            notes="Variable spending — no fixed cadence",
            source="user",
        )
        conn.commit()
        keys = {m["merchant_key"] for m in list_merchants_needing_cadence(conn, limit=10)}
        self.assertNotIn("Netflix", keys)
        self.assertIn("InsurerCo Prem Pay", keys)
    def test_heuristic_yearly_without_llm(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        with patch("webapp.services.edit_cadence_suggest.propose_cadence") as mock_llm:
            proposal = suggest_cadence_for_merchant(conn, "InsurerCo Prem Pay")
            mock_llm.assert_not_called()
        self.assertEqual(proposal["cadence_kind"], CADENCE_KIND_LUMP)
        self.assertEqual(proposal["period_count"], 12)
        self.assertEqual(proposal["source"], "heuristic")
        self.assertTrue(proposal["recommend_save_rule"])

    def test_heuristic_monthly_for_subscription(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        with patch("webapp.services.edit_cadence_suggest.propose_cadence") as mock_llm:
            proposal = suggest_cadence_for_merchant(conn, "Netflix")
            mock_llm.assert_not_called()
        self.assertEqual(proposal["cadence_kind"], CADENCE_KIND_RECURRING)
        self.assertEqual(proposal["source"], "heuristic")


class EditCadenceSuggestBulkTests(unittest.TestCase):
    def test_bulk_respects_limit(self):
        conn = _conn()
        _seed_annual_insurance(conn)
        with patch("webapp.services.edit_cadence_suggest.propose_cadence") as mock_llm:
            result = suggest_cadence_bulk(conn, limit=10)
            mock_llm.assert_not_called()
        self.assertEqual(result["limit"], 10)
        self.assertEqual(result["suggestion_count"], 2)
        self.assertGreaterEqual(result["heuristic_count"], 1)

    def test_bulk_invalid_limit(self):
        conn = _conn()
        with self.assertRaises(ValueError):
            suggest_cadence_bulk(conn, limit=7)


if __name__ == "__main__":
    unittest.main()
