"""FastAPI smoke tests (no LLM / no real bank files)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    inbox = tmp_path / "input"
    processed = tmp_path / "processed"
    inbox.mkdir()
    processed.mkdir()
    monkeypatch.setenv("FINANCE_DB_PATH", str(db_path))
    monkeypatch.setenv("FINANCE_INBOX_DIR", str(inbox))
    monkeypatch.setenv("FINANCE_PROCESSED_DIR", str(processed))
    monkeypatch.setenv("LEARNING_AGENT_ENABLED", "0")
    monkeypatch.setenv("CLASSIFICATION_AUDIT_ENABLED", "0")

    # Import after env is set so config/DB paths pick up test dirs.
    import importlib

    import webapp.config as config
    import webapp.main as main

    importlib.reload(config)
    monkeypatch.setattr(main, "DB_PATH", Path(os.environ["FINANCE_DB_PATH"]))
    monkeypatch.setattr(main, "INBOX_DIR", Path(os.environ["FINANCE_INBOX_DIR"]))
    monkeypatch.setattr(main, "PROCESSED_DIR", Path(os.environ["FINANCE_PROCESSED_DIR"]))

    with TestClient(main.app) as test_client:
        yield test_client


def test_api_status(client: TestClient):
    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert "db_path" in body
    assert "inbox_dir" in body
    assert isinstance(body.get("inbox_csv_files"), list)
    assert "review_merchant_count" in body
    assert "review_transaction_count" in body
    assert body["review_merchant_count"] == 0
    assert body["review_transaction_count"] == 0
    assert body.get("ui_mode") in ("simple", "expert")
    assert "edit_insight_enabled" in body


def test_edit_insight_disabled_returns_403(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import webapp.main as main

    monkeypatch.setattr(main, "EDIT_INSIGHT_ENABLED", False)
    res = client.post(
        "/api/transactions/edit-insight",
        json={
            "merchant_key": "Test Merchant",
            "scope": "single",
            "rows_updated": 1,
            "before": {"ai_category": "A", "ai_sub_category": "", "expense_type": "Variable", "classification": "Personal"},
            "after": {"ai_category": "B", "ai_sub_category": "", "expense_type": "Variable", "classification": "Personal"},
            "amount": -10.0,
            "update_merchant_label": False,
        },
    )
    assert res.status_code == 403
    assert "turned off" in res.json()["detail"].lower()


def test_api_review_merchant_aliases(client: TestClient):
    res = client.get("/api/review/merchant-aliases")
    assert res.status_code == 200
    body = res.json()
    assert "groups" in body
    assert isinstance(body["groups"], list)
    assert body.get("total", 0) == len(body["groups"])


def test_api_transactions_search_group_by_merchant(client: TestClient):
    res = client.get("/api/transactions/search", params={"group_by": "merchant", "limit": 5})
    assert res.status_code == 200
    body = res.json()
    assert body.get("group_by") == "merchant"
    assert "merchants" in body
    assert body.get("transactions") == []


def test_api_ingest_upload(client: TestClient, tmp_path: Path):
    sample = (
        "Date,Amount,Category,Account Name,Simple Description,Original Description\n"
        "01/05/2026,-10.00,Shopping,Checking,Corner Market,CORNER MARKET\n"
    ).encode("utf-8")
    res = client.post(
        "/api/ingest/upload",
        files=[("files", ("sample.csv", sample, "text/csv"))],
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("saved_count", 0) >= 1
    assert any(u.get("ok") for u in body.get("uploads", []))
