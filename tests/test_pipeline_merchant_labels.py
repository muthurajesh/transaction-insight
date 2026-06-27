import sqlite3
from datetime import datetime, timezone

import pandas as pd

from webapp.adapters.dataframe_store import save_processed_dataframe
from webapp.adapters.lookup_store import load_lookup_workbook_from_db, save_lookup_workbook_to_db
from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.processing.lookups import apply_merchant_category_lookup
from webapp.llm.classify import classify_review_rows, classify_review_mask


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def _insert_label(conn: sqlite3.Connection, *, rationale: str, category="Entertainment") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at,
            budget_tier, classification, flow_type, notes
        ) VALUES (
            'netflix', ?, 'Streaming', 'Variable',
            1.0, 'confirmed', ?, 3, ?,
            'Discretionary', 'personal', 'Expense', ''
        )
        """,
        (category, rationale, now),
    )
    conn.commit()


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Transaction ID": "tx-1",
                "Transaction Date": pd.Timestamp("2026-06-01"),
                "Budget Month": "2026-06",
                "Amount_Numeric": -15.99,
                "Amount": -15.99,
                "Category": "Shopping",
                "User Description": "",
                "Simple Description": "NETFLIX",
                "Original Description": "NETFLIX.COM",
                "Generated Description": "Netflix",
                "Merchant Key": "netflix",
                "Account Name": "Card",
                "Classification": "",
                "Flow Type": "Expense",
                "AI Category": "Shopping",
                "AI Sub-Category": "",
                "Type": "Variable",
                "Sub-Type": "",
                "Budget Tier": "Review",
                "Include in Spend?": "Y",
            }
        ]
    )


def test_confirmed_user_edited_merchant_label_applies_on_reimport():
    conn = _conn()
    _insert_label(conn, rationale="user edited")

    df = _df()
    lookups = load_lookup_workbook_from_db(conn)
    touched = apply_merchant_category_lookup(df, lookups)

    assert touched == 1
    row = df.iloc[0]
    assert row["AI Category"] == "Entertainment"
    assert row["AI Sub-Category"] == "Streaming"
    assert row["Type"] == "Variable"
    assert row["Budget Tier"] == "Discretionary"


def test_confirmed_user_label_skips_llm_when_fully_classified(monkeypatch):
    conn = _conn()
    _insert_label(conn, rationale="user edited")
    df = _df()
    apply_merchant_category_lookup(df, load_lookup_workbook_from_db(conn))

    assert not classify_review_mask(df).any()

    def fail_classify_batch(*args, **kwargs):
        raise AssertionError("LLM classification should not be called")

    monkeypatch.setattr("webapp.llm.classify.classify_batch", fail_classify_batch)
    result = classify_review_rows(
        df,
        client=object(),
        model="test",
        batch_size=10,
        use_json_mode=False,
        conn=conn,
    )
    assert result.iloc[0]["AI Category"] == "Entertainment"


def test_pipeline_generated_merchant_label_does_not_overwrite_confirmed_user_label():
    conn = _conn()
    _insert_label(conn, rationale="user edited", category="Entertainment")

    pipeline_df = _df()
    pipeline_df["AI Category"] = "Shopping"
    pipeline_df["AI Sub-Category"] = "Online Retail"
    pipeline_df["Budget Tier"] = "Core"

    save_processed_dataframe(conn, pipeline_df, source_file="import.csv")
    save_lookup_workbook_to_db(conn, pipeline_df)

    row = conn.execute(
        "SELECT ai_category, ai_sub_category, label_status, rationale FROM merchant_labels WHERE merchant_key='netflix'"
    ).fetchone()
    assert dict(row) == {
        "ai_category": "Entertainment",
        "ai_sub_category": "Streaming",
        "label_status": "confirmed",
        "rationale": "user edited",
    }
