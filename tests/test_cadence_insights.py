import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.cadence_insights import (
    _fallback_proposal,
    find_merchant_key_from_text,
    propose_cadence,
)
from webapp.services.cadence_rule_similarity import (
    cadence_rules_equivalent,
    suppress_duplicate_cadence_proposal,
)
from webapp.services.expense_cadence import (
    CADENCE_KIND_LUMP,
    upsert_cadence_rule,
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
            ai_category, flow_type, cadence_kind, imported_at
        ) VALUES
        ('t1', '2025-04-01', '2025-04', -928.87, 'Mercury Ins Mcc Ppa', 'Insurance', 'Expense', 'unknown', 'now'),
        ('t2', '2024-04-01', '2024-04', -915.20, 'Mercury Ins Mcc Ppa', 'Insurance', 'Expense', 'unknown', 'now'),
        ('t3', '2026-04-01', '2026-04', -928.87, 'Mercury Ins Mcc Ppa', 'Insurance', 'Expense', 'unknown', 'now')
        """
    )
    conn.commit()
    return conn


class CadenceRuleSimilarityTests(unittest.TestCase):
    def test_equivalent_yearly_lump(self):
        proposed = {
            "cadence_kind": "lump",
            "period_count": 12,
            "period_unit": "months",
            "include_in_run_rate": False,
        }
        existing = {
            "cadence_kind": CADENCE_KIND_LUMP,
            "period_count": 12,
            "period_unit": "months",
            "include_in_run_rate": 0,
        }
        self.assertTrue(cadence_rules_equivalent(proposed, existing))

    def test_suppress_duplicate_proposal(self):
        result = {
            "insight": "Annual insurance",
            "cadence_kind": "lump",
            "period_count": 12,
            "period_unit": "months",
            "include_in_run_rate": False,
            "recommend_save_rule": True,
            "data_notes": [],
        }
        existing = {
            "cadence_kind": CADENCE_KIND_LUMP,
            "period_count": 12,
            "period_unit": "months",
            "include_in_run_rate": 0,
            "source": "user",
            "notes": "Annual auto premium",
        }
        out = suppress_duplicate_cadence_proposal(
            result,
            merchant_key="Mercury Ins Mcc Ppa",
            existing_rule=existing,
        )
        self.assertFalse(out["recommend_save_rule"])
        self.assertIn("existing_similar_rule", out)


class CadenceInsightsTests(unittest.TestCase):
    def test_find_merchant_from_text(self):
        conn = _conn()
        mk = find_merchant_key_from_text(conn, "Mercury Ins is annual insurance")
        self.assertEqual(mk, "Mercury Ins Mcc Ppa")

    def test_fallback_uses_hint(self):
        conn = _conn()
        stats = {
            "merchant_transaction_count": 3,
            "months_active": 3,
            "top_amounts": [{"amount": -928.87, "count": 3}],
            "months": ["2024-04", "2025-04", "2026-04"],
        }
        proposal = _fallback_proposal(
            merchant_key="Mercury Ins Mcc Ppa",
            hint="yearly insurance premium",
            stats=stats,
        )
        self.assertEqual(proposal["cadence_kind"], CADENCE_KIND_LUMP)
        self.assertEqual(proposal["period_count"], 12)
        self.assertTrue(proposal["recommend_save_rule"])

    def test_propose_suppresses_existing_rule(self):
        conn = _conn()
        upsert_cadence_rule(
            conn,
            merchant_key="Mercury Ins Mcc Ppa",
            cadence_kind="lump",
            period_count=12,
            period_unit="months",
            include_in_run_rate=False,
            notes="Annual",
            source="user",
        )
        conn.commit()
        proposal = propose_cadence(
            conn,
            merchant_key="Mercury Ins Mcc Ppa",
            hint="yearly insurance",
        )
        self.assertFalse(proposal["recommend_save_rule"])
        self.assertTrue(proposal.get("duplicate_rule_skipped"))


if __name__ == "__main__":
    unittest.main()
