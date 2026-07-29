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
