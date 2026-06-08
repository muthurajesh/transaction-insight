from webapp.services.expense_cadence import (
    CADENCE_KIND_LUMP,
    CADENCE_KIND_ONE_TIME,
    CADENCE_KIND_RECURRING,
    cadence_fields_from_pipeline_row,
    effective_amount,
    normalize_monthly_amount,
    parse_flexible_cadence_text,
)


def test_yearly_lump_normalized():
    amt = normalize_monthly_amount(
        -900.00,
        kind=CADENCE_KIND_LUMP,
        period_count=12,
        period_unit="months",
    )
    assert round(amt, 2) == 75.00


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
    assert effective_amount(-900.00, view="normalized", kind=CADENCE_KIND_LUMP, period_count=12, period_unit="months") == 75.00


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
