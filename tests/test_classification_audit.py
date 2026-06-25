"""Tests for classification audit heuristics and sampling."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.classification_audit import (
    heuristic_finding,
    labels_match,
    list_findings,
    normalize_label,
    open_merchant_payload,
    reconcile_open_findings,
    run_classification_audit,
    scan_heuristic_findings,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _insert_tx(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    ai_category: str,
    ai_sub_category: str = "",
    source_file: str = "test.csv",
    amount: float = -42.99,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, source_file, date, budget_month, amount,
            merchant_key, flow_type, ai_category, ai_sub_category,
            label_status, imported_at
        ) VALUES (?, ?, '2026-01-15', '2026-01', ?, ?, 'Expense', ?, ?, 'pending', 'now')
        """,
        (
            f"tx-{merchant_key}-{ai_category}",
            source_file,
            amount,
            merchant_key,
            ai_category,
            ai_sub_category,
        ),
    )
    conn.commit()


class ClassificationAuditHeuristicTests(unittest.TestCase):
    def test_american_home_shield_food_dining_mismatch(self):
        finding = heuristic_finding(
            merchant_key="American Home Shield",
            category="Food/Dining",
            sub_category="Home Services",
        )
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding["suggested_category"], "Home")
        self.assertFalse(
            labels_match(
                "Food/Dining",
                "Home Services",
                finding["suggested_category"],
                finding["suggested_sub"],
            )
        )

    def test_labels_match_case_insensitive(self):
        self.assertTrue(labels_match("Food/Dining", "Groceries", "food/dining", "groceries"))

    def test_scan_heuristic_finds_mismatch_in_db(self):
        conn = _conn()
        _insert_tx(
            conn,
            merchant_key="American Home Shield",
            ai_category="Food/Dining",
            ai_sub_category="Home Services",
        )
        findings = scan_heuristic_findings(conn, ["American Home Shield"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["source"], "heuristic")
        self.assertEqual(findings[0]["confidence"], 1.0)

    def test_llm_audit_flags_high_confidence_disagreement(self):
        conn = _conn()
        _insert_tx(
            conn,
            merchant_key="American Home Shield",
            ai_category="Food/Dining",
            ai_sub_category="Home Services",
        )
        mock_client = MagicMock()
        with patch(
            "webapp.services.classification_audit._resolve_audit_client",
            return_value=(mock_client, "audit-model", "ollama"),
        ), patch(
            "webapp.services.classification_audit.audit_classify_single",
            return_value={
                "category": "Home",
                "sub_category": "Home warranty",
                "type": "Fixed",
                "budget_tier": "Need",
                "confidence": 0.92,
                "rationale": "Home warranty provider, not food.",
            },
        ), patch(
            "webapp.services.classification_audit.CLASSIFICATION_AUDIT_ENABLED",
            True,
        ), patch(
            "webapp.services.classification_audit.sample_merchants_post_import",
            return_value=["American Home Shield"],
        ):
            result = run_classification_audit(
                conn,
                run_type="post_import",
                source_files=["test.csv"],
                sample_size=1,
            )
        self.assertEqual(result["llm_flagged"], 1)
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM classification_audit_findings WHERE status = 'open'"
        ).fetchone()
        self.assertGreaterEqual(row["c"], 1)

    def test_skip_confirmed_merchant_without_drift(self):
        conn = _conn()
        _insert_tx(
            conn,
            merchant_key="Trader Joe's",
            ai_category="Food/Dining",
            ai_sub_category="Groceries",
        )
        conn.execute(
            """
            INSERT INTO merchant_labels (
                merchant_key, ai_category, ai_sub_category, expense_type,
                confidence, label_status, rationale, sample_count, updated_at
            ) VALUES (?, 'Food/Dining', 'Groceries', 'Variable', 1.0, 'confirmed', '', 1, 'now')
            """,
            ("Trader Joe's",),
        )
        conn.commit()
        mock_client = MagicMock()
        with patch(
            "webapp.services.classification_audit._resolve_audit_client",
            return_value=(mock_client, "audit-model", "ollama"),
        ), patch(
            "webapp.services.classification_audit.audit_classify_single",
        ) as mock_audit, patch(
            "webapp.services.classification_audit.CLASSIFICATION_AUDIT_ENABLED",
            True,
        ), patch(
            "webapp.services.classification_audit.sample_merchants_post_import",
            return_value=["Trader Joe's"],
        ):
            run_classification_audit(
                conn,
                run_type="post_import",
                source_files=["test.csv"],
                sample_size=1,
            )
        mock_audit.assert_not_called()


def _insert_audit_run_and_finding(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    production_category: str,
    production_sub: str,
    suggested_category: str,
    suggested_sub: str,
    transaction_id: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO classification_audit_runs (
            run_type, status, sample_size, model, started_at, finished_at
        ) VALUES ('manual', 'completed', 1, 'test-model', 'now', 'now')
        """
    )
    run_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO classification_audit_findings (
            run_id, merchant_key, transaction_id, source,
            production_category, production_sub,
            suggested_category, suggested_sub,
            confidence, rationale, status, created_at
        ) VALUES (?, ?, ?, 'llm_audit', ?, ?, ?, ?, 0.95, 'test', 'open', 'now')
        """,
        (
            run_id,
            merchant_key,
            transaction_id,
            production_category,
            production_sub,
            suggested_category,
            suggested_sub,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


class ClassificationAuditReconcileTests(unittest.TestCase):
    def test_reconcile_resolves_when_current_matches_suggested(self):
        conn = _conn()
        tx_id = "tx-taco-bell"
        _insert_tx(
            conn,
            merchant_key="Taco Bell",
            ai_category="Food/Dining",
            ai_sub_category="Fast Food",
        )
        conn.execute(
            "UPDATE transactions SET transaction_id = ? WHERE merchant_key = ?",
            (tx_id, "Taco Bell"),
        )
        finding_id = _insert_audit_run_and_finding(
            conn,
            merchant_key="Taco Bell",
            production_category="Food/Dining",
            production_sub="Restaurants",
            suggested_category="Food/Dining",
            suggested_sub="Fast Food",
            transaction_id=tx_id,
        )
        resolved = reconcile_open_findings(conn)
        self.assertEqual(resolved, 1)
        row = conn.execute(
            "SELECT status FROM classification_audit_findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        self.assertEqual(row["status"], "resolved")
        open_rows = conn.execute(
            "SELECT COUNT(*) AS c FROM classification_audit_findings WHERE status = 'open'"
        ).fetchone()
        self.assertEqual(open_rows["c"], 0)

    def test_list_findings_enriches_current_labels(self):
        conn = _conn()
        tx_id = "tx-chipotle-list"
        _insert_tx(
            conn,
            merchant_key="Chipotle",
            ai_category="Food/Dining",
            ai_sub_category="Restaurants",
        )
        conn.execute(
            "UPDATE transactions SET transaction_id = ? WHERE merchant_key = ?",
            (tx_id, "Chipotle"),
        )
        _insert_audit_run_and_finding(
            conn,
            merchant_key="Chipotle",
            production_category="Food/Dining",
            production_sub="Restaurants",
            suggested_category="Food/Dining",
            suggested_sub="Fast Food",
            transaction_id=tx_id,
        )
        findings = list_findings(conn, status="open")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["current_category"], "Food/Dining")
        self.assertEqual(findings[0]["current_sub"], "Restaurants")
        self.assertFalse(findings[0]["already_fixed"])

    def test_open_merchant_payload_targets_edit_search(self):
        conn = _conn()
        tx_id = "tx-open-merchant"
        _insert_tx(
            conn,
            merchant_key="Taco Bell",
            ai_category="Food/Dining",
            ai_sub_category="Restaurants",
        )
        finding_id = _insert_audit_run_and_finding(
            conn,
            merchant_key="Taco Bell",
            production_category="Food/Dining",
            production_sub="Restaurants",
            suggested_category="Food/Dining",
            suggested_sub="Fast Food",
            transaction_id=tx_id,
        )
        payload = open_merchant_payload(conn, finding_id)
        self.assertEqual(payload["target_tab"], "edit")
        self.assertEqual(payload["search_query"], "Taco Bell")
        self.assertEqual(payload["merchant_key"], "Taco Bell")


if __name__ == "__main__":
    unittest.main()
