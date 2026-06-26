import sqlite3
import unittest
from unittest.mock import patch

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.custom_rules import (
    add_custom_rule,
    apply_custom_rule_by_id,
    delete_custom_rule,
    list_custom_rules,
    preview_custom_rule,
    update_custom_rule,
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
    tid: str,
    merchant: str,
    amount: float = -10.0,
    ai_category: str = "Shopping",
) -> None:
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            ai_category, flow_type, expense_type, classification,
            label_status, imported_at
        ) VALUES (?, '2026-01-15', '2026-01', ?, ?, ?, 'Expense', 'Variable', 'Personal', 'confirmed', 'now')
        """,
        (tid, amount, merchant, ai_category),
    )
    conn.commit()


_MOCK_COMPILED = {
    "rule_type": "assign",
    "match": {"generated_description": "Merchant A"},
    "set": {
        "ai_category": "Category X",
        "ai_sub_category": "Sub X",
        "classification": "Business",
        "flow_type": "Expense",
    },
}


class CustomRulesServiceTests(unittest.TestCase):
    def test_list_includes_stable_id(self):
        conn = _conn()
        add_custom_rule(conn, "If Merchant A then Category X")
        rules = list_custom_rules(conn)["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["id"], 1)
        self.assertEqual(rules[0]["status"], "Pending")

    def test_update_preserves_id(self):
        conn = _conn()
        add_custom_rule(conn, "Original rule text")
        update_custom_rule(conn, 1, rule_text="Updated rule text")
        rules = list_custom_rules(conn)["rules"]
        self.assertEqual(rules[0]["id"], 1)
        self.assertEqual(rules[0]["rule"], "Updated rule text")
        self.assertEqual(rules[0]["status"], "Pending")

    def test_delete_rule(self):
        conn = _conn()
        add_custom_rule(conn, "Rule to delete")
        delete_custom_rule(conn, 1)
        self.assertEqual(list_custom_rules(conn)["rules"], [])

    @patch("webapp.services.custom_rules._compile_rule_ephemeral")
    def test_preview_returns_proposed_labels(self, mock_compile):
        mock_compile.return_value = (_MOCK_COMPILED, "")
        conn = _conn()
        _insert_tx(conn, tid="t1", merchant="Merchant A")
        _insert_tx(conn, tid="t2", merchant="Other")

        result = preview_custom_rule(conn, rule_text="mock rule")
        self.assertIsNone(result["compile_error"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["transactions"]), 1)
        tx = result["transactions"][0]
        self.assertEqual(tx["transaction_id"], "t1")
        self.assertEqual(tx["ai_category"], "Shopping")
        self.assertEqual(tx["proposed"]["ai_category"], "Category X")
        self.assertEqual(tx["proposed"]["classification"], "Business")

    @patch("webapp.services.custom_rules._compile_rule_ephemeral")
    def test_apply_confirms_rows_when_labels_already_match(self, mock_compile):
        mock_compile.return_value = (_MOCK_COMPILED, "")
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, date, budget_month, amount, merchant_key,
                ai_category, ai_sub_category, flow_type, expense_type, classification,
                label_status, imported_at
            ) VALUES (
                't1', '2026-01-15', '2026-01', -10.0, 'Merchant A',
                'Category X', 'Sub X', 'Expense', 'Variable', 'Business',
                'needs_review', 'now'
            )
            """
        )
        conn.commit()
        add_custom_rule(conn, "If Merchant A then Category X")

        result = apply_custom_rule_by_id(conn, 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_updated"], 1)
        row = conn.execute(
            "SELECT label_status FROM transactions WHERE transaction_id = 't1'"
        ).fetchone()
        self.assertEqual(row["label_status"], "confirmed")

    @patch("webapp.services.custom_rules._compile_rule_ephemeral")
    def test_apply_one_updates_matching_rows(self, mock_compile):
        mock_compile.return_value = (_MOCK_COMPILED, "")
        conn = _conn()
        _insert_tx(conn, tid="t1", merchant="Merchant A")
        _insert_tx(conn, tid="t2", merchant="Other")
        add_custom_rule(conn, "If Merchant A then Category X")

        result = apply_custom_rule_by_id(conn, 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_updated"], 1)
        row = conn.execute(
            "SELECT ai_category, classification FROM transactions WHERE transaction_id = 't1'"
        ).fetchone()
        self.assertEqual(row["ai_category"], "Category X")
        self.assertEqual(row["classification"], "Business")


if __name__ == "__main__":
    unittest.main()
