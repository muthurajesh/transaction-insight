from __future__ import annotations

from pathlib import Path

import pandas as pd

from webapp.excel.formatting import (
    format_baseline_worksheet,
    format_spending_insights_worksheet,
    format_transaction_worksheet,
    open_excel_workbook,
)
from webapp.excel.insights import (
    build_baseline_sheets,
    write_baseline_sheet,
    write_spending_insights_sheet,
)
from webapp.processing.constants import (
    DETAIL_TRANSACTION_SHEETS,
    SUMMARY_INSIGHTS_SHEET,
    TRANSACTION_SHEETS,
)
from webapp.processing.parse import prepare_transaction_export_df, sort_expenses_for_export


def write_excel(
    df: pd.DataFrame,
    output_path: Path,
    *,
    ingest_warning_count: int = 0,
    cadence_review: pd.DataFrame | None = None,
) -> None:
    """Write enriched workbook with Raw, Income/Expenses, and Summary tabs."""

    export_df = prepare_transaction_export_df(df)
    raw_sheet = export_df
    income_sheet = export_df[export_df["Flow Type"] == "Income"]
    expense_sheet = sort_expenses_for_export(
        export_df[export_df["Flow Type"] == "Expense"].copy()
    )
    adjustment_sheet = export_df[export_df["Flow Type"] == "Adjustment"]
    with open_excel_workbook(output_path) as writer:
        raw_sheet.to_excel(writer, sheet_name="Raw Data", index=False)
        income_sheet.to_excel(writer, sheet_name="Income", index=False)
        expense_sheet.to_excel(writer, sheet_name="Expenses", index=False)
        adjustment_sheet.to_excel(writer, sheet_name="Adjustments", index=False)
        insights_layout = write_spending_insights_sheet(
            writer, df, ingest_warning_count=ingest_warning_count
        )
        baseline_layout = write_baseline_sheet(writer, df)
        for sheet_name in TRANSACTION_SHEETS:
            if sheet_name in writer.sheets:
                format_transaction_worksheet(
                    writer.sheets[sheet_name],
                    hide_descriptions=sheet_name in DETAIL_TRANSACTION_SHEETS,
                    short_date_columns=("Date",)
                    if sheet_name in ("Raw Data", "Income")
                    else (),
                    enable_autofilter=sheet_name == "Expenses",
                )
        if SUMMARY_INSIGHTS_SHEET in writer.sheets:
            format_spending_insights_worksheet(
                writer.sheets[SUMMARY_INSIGHTS_SHEET], **insights_layout
            )
        if "Baseline" in writer.sheets:
            format_baseline_worksheet(writer.sheets["Baseline"], **baseline_layout)

    print(f"  Raw Data:      {len(raw_sheet)} rows")
    print(f"  Income:        {len(income_sheet)} rows")
    print(f"  Expenses:      {len(expense_sheet)} rows")
    print(f"  Adjustments:   {len(adjustment_sheet)} rows")
    n_months = insights_layout.get("overview_rows", 0)
    if n_months:
        print(f"  Summary:       {n_months} month(s) with category drill-down links")
    _, baseline_categories, _ = build_baseline_sheets(df)
    if not baseline_categories.empty:
        print(f"  Baseline:      {len(baseline_categories)} categories across budget months")
