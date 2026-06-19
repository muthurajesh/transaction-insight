import json
import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.label_health import (
    apply_mapping,
    build_health_report,
    detect_alias_candidates,
    export_health_report,
    report_drift,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _seed_merchant(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    category: str,
    sub_category: str,
    tx_category: str | None = None,
    tx_sub_category: str | None = None,
) -> None:
    tx_cat = tx_category if tx_category is not None else category
    tx_sub = tx_sub_category if tx_sub_category is not None else sub_category
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at
        ) VALUES (?, ?, ?, 'Variable', 1.0, 'confirmed', 'test', 0, 'now')
        """,
        (merchant_key, category, sub_category),
    )
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, ai_sub_category, flow_type, label_status, imported_at
        ) VALUES ('tx-' || ?, '2026-01-01', '2026-01', -10.0, ?, ?, ?, 'Expense', 'confirmed', 'now')
        """,
        (merchant_key, merchant_key, tx_cat, tx_sub),
    )
    conn.commit()


class LabelHealthReportTests(unittest.TestCase):
    def test_drift_detected(self):
        conn = _conn()
        _seed_merchant(
            conn,
            merchant_key="Cafe A",
            category="Dining",
            sub_category="Coffee",
            tx_category="Restaurants/Dining",
            tx_sub_category="Coffee",
        )
        drift = report_drift(conn)
        self.assertEqual(drift["summary"]["drift_rows"], 1)

    def test_alias_candidates_group_similar_names(self):
        conn = _conn()
        _seed_merchant(conn, merchant_key="365 VEND LLC", category="Groceries", sub_category="Groceries")
        _seed_merchant(conn, merchant_key="365 Vend LLC", category="Groceries", sub_category="Groceries")
        groups = detect_alias_candidates(conn, min_similarity=0.9)
        self.assertTrue(any(len(g["members"]) >= 2 for g in groups))

    def test_export_writes_files(self):
        conn = _conn()
        _seed_merchant(conn, merchant_key="Shop", category="Groceries", sub_category="Supermarket")
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            paths = export_health_report(conn, Path(tmp))
            self.assertTrue(Path(paths["report"]).is_file())
            self.assertTrue(Path(paths["suggested_mapping"]).is_file())


class ApplyMappingTests(unittest.TestCase):
    def test_category_merge_and_reconcile(self):
        conn = _conn()
        _seed_merchant(
            conn,
            merchant_key="Cafe Downtown",
            category="Restaurants/Dining",
            sub_category="Coffee",
            tx_category="Dining",
            tx_sub_category="Fast food",
        )
        mapping = {
            "category_merges": {"Restaurants/Dining": "Dining"},
            "sub_category_merges": {"Dining": {"Fast food": "Coffee"}},
            "merchant_aliases": {},
            "reconcile": {"enabled": True, "only_confirmed": True, "skip_merchants": []},
        }
        stats = apply_mapping(conn, mapping, dry_run=False)
        self.assertGreater(stats["category_merges"], 0)
        self.assertGreaterEqual(stats["reconcile_transactions"], 0)
        row = conn.execute(
            "SELECT ai_category, ai_sub_category FROM transactions WHERE merchant_key='Cafe Downtown'"
        ).fetchone()
        self.assertEqual(row["ai_category"], "Dining")
        self.assertEqual(row["ai_sub_category"], "Coffee")
        self.assertEqual(report_drift(conn)["summary"]["drift_rows"], 0)

    def test_merchant_alias_renames_transactions(self):
        conn = _conn()
        _seed_merchant(conn, merchant_key="365 VEND LLC Troy", category="Groceries", sub_category="Supermarket")
        _seed_merchant(conn, merchant_key="365 Vend LLC", category="Groceries", sub_category="Groceries")
        mapping = {
            "category_merges": {},
            "sub_category_merges": {},
            "merchant_aliases": {"365 VEND LLC Troy": "365 Vend LLC"},
            "reconcile": {"enabled": False},
        }
        stats = apply_mapping(conn, mapping, dry_run=False)
        self.assertEqual(stats["merchant_alias_transactions"], 1)
        count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant_key='365 Vend LLC'"
        ).fetchone()[0]
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
