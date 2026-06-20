import sqlite3
import unittest

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.taxonomy_rules import (
    analyze_taxonomy,
    apply_taxonomy_proposals,
    build_heuristic_proposals,
    preview_taxonomy_proposals,
    proposals_to_mapping,
    sort_proposals_alphabetically,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _seed_tx(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    category: str,
    sub_category: str,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, ai_sub_category, flow_type, label_status, imported_at
        ) VALUES (?, '2026-01-01', '2026-01', -25.0, ?, ?, ?, 'Expense', 'confirmed', 'now')
        """,
        (f"tx-{merchant_key}-{category}-{sub_category}", merchant_key, category, sub_category),
    )
    conn.commit()


class TaxonomyRulesTests(unittest.TestCase):
    def test_heuristic_detects_case_variant_sub_category(self):
        conn = _conn()
        _seed_tx(conn, merchant_key="Chase ATM", category="Banking", sub_category="ATM withdrawal")
        _seed_tx(conn, merchant_key="Chase ATM", category="Banking", sub_category="ATM Withdrawal")
        proposals = build_heuristic_proposals(conn)
        unify = [p for p in proposals if p["rule_type"] == "label_unify"]
        self.assertTrue(
            any(
                "ATM withdrawal" in (p.get("from_sub_categories") or [])
                and p["to_label"] == "ATM Withdrawal"
                for p in unify
            )
        )

    def test_label_unify_cross_category(self):
        conn = _conn()
        _seed_tx(conn, merchant_key="Netflix", category="Entertainment", sub_category="Streaming services")
        _seed_tx(conn, merchant_key="Spotify", category="Subscriptions", sub_category="Streaming Services")
        _seed_tx(conn, merchant_key="Hulu", category="Entertainment", sub_category="Streaming Service")
        proposals = build_heuristic_proposals(conn)
        unify = [p for p in proposals if p["rule_type"] == "label_unify"]
        streaming = [
            p
            for p in unify
            if any("Streaming" in v for v in (p.get("from_sub_categories") or []))
        ]
        self.assertEqual(len(streaming), 1)
        rule = streaming[0]
        apply_taxonomy_proposals(conn, [rule])
        rows = conn.execute(
            "SELECT ai_category, ai_sub_category FROM transactions ORDER BY merchant_key"
        ).fetchall()
        for row in rows:
            self.assertEqual(row["ai_sub_category"], rule["to_label"])
            self.assertEqual(row["ai_category"], rule["target_category"])

    def test_proposals_to_mapping_category(self):
        mapping = proposals_to_mapping(
            [
                {
                    "rule_type": "category_merge",
                    "from_label": "Charitable Giving",
                    "to_label": "Charitable",
                }
            ]
        )
        self.assertEqual(mapping["category_merges"]["Charitable Giving"], "Charitable")

    def test_preview_and_apply_category_merge(self):
        conn = _conn()
        _seed_tx(conn, merchant_key="A", category="Charitable Giving", sub_category="Donation")
        _seed_tx(conn, merchant_key="B", category="Charitable", sub_category="Donation")
        proposals = [
            {
                "id": "x",
                "rule_type": "category_merge",
                "from_label": "Charitable Giving",
                "to_label": "Charitable",
            }
        ]
        preview = preview_taxonomy_proposals(conn, proposals)
        self.assertGreater(preview["preview"]["category_merges"], 0)
        apply_taxonomy_proposals(conn, proposals)
        rows = conn.execute(
            "SELECT DISTINCT ai_category FROM transactions ORDER BY 1"
        ).fetchall()
        cats = [r[0] for r in rows]
        self.assertEqual(cats, ["Charitable"])

    def test_analyze_returns_summary(self):
        conn = _conn()
        _seed_tx(conn, merchant_key="Shop", category="Groceries", sub_category="Supermarket")
        result = analyze_taxonomy(conn)
        self.assertIn("summary", result)
        self.assertIn("proposals", result)
        self.assertGreaterEqual(result["summary"]["category_count"], 1)


    def test_preview_includes_transaction_samples(self):
        conn = _conn()
        _seed_tx(conn, merchant_key="Hulu", category="Entertainment", sub_category="Streaming services")
        _seed_tx(conn, merchant_key="Netflix", category="Entertainment", sub_category="Streaming services")
        proposals = [
            {
                "id": "sub1",
                "rule_type": "sub_category_merge",
                "from_label": "Streaming services",
                "to_label": "Streaming Services",
                "scope_category": "Entertainment",
            }
        ]
        result = preview_taxonomy_proposals(conn, proposals)
        groups = result.get("sample_groups") or []
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total_matches"], 2)
        self.assertEqual(len(groups[0]["samples"]), 2)
        sample = groups[0]["samples"][0]
        self.assertEqual(sample["before"]["ai_sub_category"], "Streaming services")
        self.assertEqual(sample["after"]["ai_sub_category"], "Streaming Services")

    def test_preview_returns_all_matches_by_default(self):
        conn = _conn()
        for i in range(12):
            _seed_tx(
                conn,
                merchant_key=f"M{i}",
                category="Entertainment",
                sub_category="Streaming services",
            )
        proposals = [
            {
                "id": "sub-all",
                "rule_type": "sub_category_merge",
                "from_label": "Streaming services",
                "to_label": "Streaming Services",
                "scope_category": "Entertainment",
            }
        ]
        result = preview_taxonomy_proposals(conn, proposals)
        groups = result.get("sample_groups") or []
        self.assertEqual(len(groups[0]["samples"]), 12)
        self.assertFalse(groups[0]["truncated"])

        result = preview_taxonomy_proposals(conn, proposals, sample_limit=5)
        groups = result.get("sample_groups") or []
        self.assertEqual(len(groups[0]["samples"]), 5)
        self.assertTrue(groups[0]["truncated"])

    def test_sort_proposals_alphabetically_groups_by_category(self):
        proposals = [
            {
                "rule_type": "label_unify",
                "from_label": "Streaming service",
                "to_label": "Streaming Services",
                "target_category": "Subscriptions",
            },
            {
                "rule_type": "category_merge",
                "from_label": "Charitable Giving",
                "to_label": "Charitable",
            },
            {
                "rule_type": "label_unify",
                "from_label": "ATM withdrawal",
                "to_label": "ATM Withdrawal",
                "target_category": "Banking",
            },
            {
                "rule_type": "sub_category_merge",
                "from_label": "Donation",
                "to_label": "Donations",
                "scope_category": "Charitable",
            },
        ]
        sorted_p = sort_proposals_alphabetically(proposals)
        categories = [
            p.get("target_category") or p.get("scope_category") or p.get("to_label")
            for p in sorted_p
        ]
        self.assertEqual(categories, ["Banking", "Charitable", "Charitable", "Subscriptions"])


if __name__ == "__main__":
    unittest.main()
