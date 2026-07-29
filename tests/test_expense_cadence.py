import sqlite3

from webapp.db.schema import SCHEMA_SQL, _migrate_schema
from webapp.services.expense_cadence import (
    CADENCE_KIND_LUMP,
    CADENCE_KIND_ONE_TIME,
    CADENCE_KIND_RECURRING,
    cadence_fields_from_pipeline_row,
    effective_amount,
    normalize_monthly_amount,
    parse_flexible_cadence_text,
    sum_expenses_for_view,
    top_categories_for_view,
    upsert_cadence_rule,
)


def test_yearly_lump_normalized():
    amt = normalize_monthly_amount(
        -900.00,
        kind=CADENCE_KIND_LUMP,
        period_count=12,
        period_unit="months",
    )
    assert round(amt, 2) == 75.0


def test_semi_annual_lump():
    amt = normalize_monthly_amount(
        -600.0,
        kind=CADENCE_KIND_LUMP,
        period_count=6,
        period_unit="months",
    )
    assert round(amt, 2) == 100.0


def test_biweekly_recurring():
    amt = normalize_monthly_amount(
        -200.0,
        kind=CADENCE_KIND_RECURRING,
        period_count=2,
        period_unit="weeks",
    )
    assert round(amt, 2) == 433.33


def test_one_time_normalized_zero():
    assert (
        normalize_monthly_amount(-500, kind=CADENCE_KIND_ONE_TIME) == 0.0
    )


def test_effective_amount_views():
    assert effective_amount(-900.00, view="cash", kind=CADENCE_KIND_LUMP, period_count=12, period_unit="months") == 900.00
    assert effective_amount(-900.00, view="core", kind=CADENCE_KIND_LUMP, period_count=12, period_unit="months", include_in_run_rate=False) == 0.0
    assert effective_amount(-900.00, view="normalized", kind=CADENCE_KIND_LUMP, period_count=12, period_unit="months") == 75.0


def test_parse_semi_annual_text():
    kind, count, unit = parse_flexible_cadence_text("semi-annual auto insurance")
    assert kind == CADENCE_KIND_LUMP
    assert count == 6
    assert unit == "months"


def test_pipeline_yearly_row():
    fields = cadence_fields_from_pipeline_row(
        {"Expense Cadence": "Yearly", "In Monthly Run-Rate?": "N"}
    )
    assert fields["cadence_kind"] == CADENCE_KIND_LUMP
    assert fields["period_count"] == 12
    assert fields["include_in_run_rate"] is False


def _test_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    conn.commit()
    return conn


def test_sum_expenses_for_view_normalized():
    conn = _test_conn()
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            flow_type, ai_category, imported_at,
            cadence_kind, period_count, period_unit, include_in_run_rate
        ) VALUES
        ('t1', '2026-04-01', '2026-04', -900.00, 'InsurerCo', 'Expense', 'Insurance', 'now',
         'lump', 12, 'months', 0),
        ('t2', '2026-04-05', '2026-04', -100.0, 'Groceries', 'Expense', 'Groceries', 'now',
         'recurring', 1, 'months', 1)
        """
    )
    conn.commit()
    cash, count = sum_expenses_for_view(conn, view="cash", budget_month="2026-04")
    norm, _ = sum_expenses_for_view(conn, view="normalized", budget_month="2026-04")
    core, _ = sum_expenses_for_view(conn, view="core", budget_month="2026-04")
    assert count == 2
    assert cash == 1000.0
    assert norm == round(75.0 + 100.0, 2)
    assert core == 100.0


def test_top_categories_for_view():
    conn = _test_conn()
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            flow_type, ai_category, imported_at,
            cadence_kind, period_count, period_unit, include_in_run_rate
        ) VALUES
        ('t1', '2026-04-01', '2026-04', -600.0, 'Auto Ins', 'Expense', 'Insurance', 'now',
         'lump', 6, 'months', 0),
        ('t2', '2026-04-05', '2026-04', -50.0, 'Groceries', 'Expense', 'Groceries', 'now',
         'recurring', 1, 'months', 1)
        """
    )
    conn.commit()
    ranked = top_categories_for_view(conn, "2026-04", limit=5, view="normalized")
    by_cat = {r["category"]: r["spend"] for r in ranked}
    assert by_cat["Insurance"] == 100.0
    assert by_cat["Groceries"] == 50.0


def test_merchant_cadence_rule_in_aggregation():
    conn = _test_conn()
    upsert_cadence_rule(
        conn,
        merchant_key="InsurerCo",
        cadence_kind=CADENCE_KIND_LUMP,
        period_count=12,
        period_unit="months",
        include_in_run_rate=False,
    )
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, date, budget_month, amount, merchant_key,
            flow_type, ai_category, imported_at
        ) VALUES ('t1', '2026-04-01', '2026-04', -900.00, 'InsurerCo', 'Expense', 'Insurance', 'now')
        """
    )
    conn.commit()
    norm, _ = sum_expenses_for_view(conn, view="normalized", budget_month="2026-04")
    assert norm == 75.0
