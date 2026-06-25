from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    source_file TEXT,
    date TEXT NOT NULL,
    budget_month TEXT NOT NULL,
    amount REAL NOT NULL,
    source_category TEXT,
    user_description TEXT,
    simple_description TEXT,
    original_description TEXT,
    merchant_key TEXT NOT NULL,
    account_name TEXT,
    classification TEXT,
    flow_type TEXT,
    ai_category TEXT,
    ai_sub_category TEXT,
    expense_type TEXT,
    confidence REAL,
    label_status TEXT DEFAULT 'pending',
    rationale TEXT,
    imported_at TEXT NOT NULL,
    cadence_kind TEXT,
    period_count INTEGER,
    period_unit TEXT,
    include_in_run_rate INTEGER,
    cadence_source TEXT,
    cadence_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_budget_month ON transactions(budget_month);
CREATE INDEX IF NOT EXISTS idx_tx_merchant ON transactions(merchant_key);
CREATE INDEX IF NOT EXISTS idx_tx_label_status ON transactions(label_status);

CREATE TABLE IF NOT EXISTS merchant_labels (
    merchant_key TEXT PRIMARY KEY,
    ai_category TEXT NOT NULL,
    ai_sub_category TEXT,
    expense_type TEXT,
    confidence REAL,
    label_status TEXT NOT NULL,
    rationale TEXT,
    sample_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cadence_rules (
    rule_id TEXT PRIMARY KEY,
    merchant_key TEXT NOT NULL UNIQUE,
    cadence_kind TEXT NOT NULL,
    period_count INTEGER,
    period_unit TEXT,
    include_in_run_rate INTEGER,
    notes TEXT,
    source TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cadence_rules_merchant ON cadence_rules(merchant_key);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_trace TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS ingested_files (
    filename TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    last_ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lookup_snapshots (
    sheet_name TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_reports (
    report_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    sql_template TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '[]',
    original_question TEXT,
    report_prompt TEXT NOT NULL DEFAULT '',
    report_config_json TEXT NOT NULL DEFAULT '{}',
    parent_report_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_custom_reports_name ON custom_reports(name);

CREATE TABLE IF NOT EXISTS description_lookup (
    source_key TEXT PRIMARY KEY,
    user_description TEXT,
    simple_description TEXT,
    original_description TEXT,
    generated_description TEXT NOT NULL,
    source TEXT,
    model TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_rules (
    source_category TEXT PRIMARY KEY,
    ai_category TEXT NOT NULL,
    budget_tier TEXT,
    type TEXT,
    sub_type TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_custom_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text TEXT NOT NULL,
    status TEXT NOT NULL,
    compiled_rule TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classification_audit_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_classification_audit_runs_started
    ON classification_audit_runs(started_at);

CREATE TABLE IF NOT EXISTS classification_audit_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES classification_audit_runs(id),
    merchant_key TEXT NOT NULL,
    transaction_id TEXT,
    source TEXT NOT NULL,
    production_category TEXT,
    production_sub TEXT,
    suggested_category TEXT,
    suggested_sub TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_classification_audit_findings_status
    ON classification_audit_findings(status);
CREATE INDEX IF NOT EXISTS idx_classification_audit_findings_merchant
    ON classification_audit_findings(merchant_key);
"""

_TRANSACTION_CADENCE_COLUMNS = (
    ("cadence_kind", "TEXT"),
    ("period_count", "INTEGER"),
    ("period_unit", "TEXT"),
    ("include_in_run_rate", "INTEGER"),
    ("cadence_source", "TEXT"),
    ("cadence_note", "TEXT"),
)

_MERCHANT_LABEL_EXTRA_COLUMNS = (
    ("budget_tier", "TEXT"),
    ("classification", "TEXT"),
    ("flow_type", "TEXT"),
    ("notes", "TEXT"),
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    tx_cols = _existing_columns(conn, "transactions")
    for name, col_type in _TRANSACTION_CADENCE_COLUMNS:
        if name not in tx_cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {name} {col_type}")

    ml_cols = _existing_columns(conn, "merchant_labels")
    for name, col_type in _MERCHANT_LABEL_EXTRA_COLUMNS:
        if name not in ml_cols:
            conn.execute(f"ALTER TABLE merchant_labels ADD COLUMN {name} {col_type}")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS description_lookup (
            source_key TEXT PRIMARY KEY,
            user_description TEXT,
            simple_description TEXT,
            original_description TEXT,
            generated_description TEXT NOT NULL,
            source TEXT,
            model TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS category_rules (
            source_category TEXT PRIMARY KEY,
            ai_category TEXT NOT NULL,
            budget_tier TEXT,
            type TEXT,
            sub_type TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pipeline_custom_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_text TEXT NOT NULL,
            status TEXT NOT NULL,
            compiled_rule TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS classification_audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL,
            sample_size INTEGER NOT NULL DEFAULT 0,
            model TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            summary_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_classification_audit_runs_started
            ON classification_audit_runs(started_at);
        CREATE TABLE IF NOT EXISTS classification_audit_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES classification_audit_runs(id),
            merchant_key TEXT NOT NULL,
            transaction_id TEXT,
            source TEXT NOT NULL,
            production_category TEXT,
            production_sub TEXT,
            suggested_category TEXT,
            suggested_sub TEXT,
            confidence REAL NOT NULL DEFAULT 0,
            rationale TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_classification_audit_findings_status
            ON classification_audit_findings(status);
        CREATE INDEX IF NOT EXISTS idx_classification_audit_findings_merchant
            ON classification_audit_findings(merchant_key);
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tx_cadence_kind ON transactions(cadence_kind)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cadence_rules (
            rule_id TEXT PRIMARY KEY,
            merchant_key TEXT NOT NULL UNIQUE,
            cadence_kind TEXT NOT NULL,
            period_count INTEGER,
            period_unit TEXT,
            include_in_run_rate INTEGER,
            notes TEXT,
            source TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cadence_rules_merchant ON cadence_rules(merchant_key)
        """
    )

    cr_cols = _existing_columns(conn, "custom_reports")
    for name, col_type in (
        ("report_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("report_config_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("parent_report_id", "TEXT"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ):
        if name not in cr_cols:
            conn.execute(f"ALTER TABLE custom_reports ADD COLUMN {name} {col_type}")


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        conn.commit()


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
