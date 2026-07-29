"""Pipeline smoke with mocked LLM (no network)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from webapp.config import PipelineConfig
from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.pipeline.run import run_pipeline
from webapp.processing.parse import load_csv


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    return conn


def test_run_pipeline_smoke_mocked_llm(monkeypatch, tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "Date,Amount,Category,Account Name,Simple Description,Original Description\n"
        "01/05/2026,-62.40,Shopping,Checking,Corner Market,CORNER MARKET #12 POS\n"
        "01/10/2026,3200.00,Paychecks/Salary,Checking,Employer Direct Deposit,ACH CREDIT EMPLOYER\n",
        encoding="utf-8",
    )
    df = load_csv(csv_path)

    def fake_fill(frame, *args, **kwargs):
        out = frame.copy()
        out["Generated Description"] = out["Simple Description"].fillna("").astype(str)
        return out, pd.DataFrame()

    def fake_classify(frame, *args, **kwargs):
        return frame

    monkeypatch.setattr(
        "webapp.pipeline.run.resolve_llm",
        lambda config: (MagicMock(), "test-model", "ollama", False, 10, 10),
    )
    monkeypatch.setattr(
        "webapp.pipeline.run.core.fill_generated_descriptions", fake_fill
    )
    monkeypatch.setattr(
        "webapp.pipeline.run.core.classify_review_rows", fake_classify
    )

    result = run_pipeline(
        df,
        PipelineConfig(
            skip_lookup_update=True,
            skip_cadence_detection=True,
            source_file=csv_path.name,
        ),
        conn=_conn(),
        input_path=csv_path,
    )

    assert len(result.dataframe) == 2
    assert "Transaction ID" in result.dataframe.columns
    assert "Flow Type" in result.dataframe.columns
    assert "Generated Description" in result.dataframe.columns
    flows = set(result.dataframe["Flow Type"].astype(str))
    assert "Expense" in flows
    assert "Income" in flows
