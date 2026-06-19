from __future__ import annotations

from typing import Any

import pandas as pd

from webapp.processing.constants import (
    BASELINE_FIXED_BUFFER_PCT,
    BASELINE_RECOMMENDED_MONTHS,
    BASELINE_VARIABLE_BUFFER_PCT,
    CADENCE_ONETIME,
    CADENCE_UNPLANNED,
    CADENCE_UNKNOWN,
    CADENCE_YEARLY,
    SUMMARY_INSIGHTS_SHEET,
)
from webapp.processing.flow import _spend_rows


def _category_avg_monthly_spend(spend: pd.DataFrame) -> pd.Series:
    """
    Average monthly spend per AI Category (mean of each month's category total).

    Not per transaction — e.g. Restaurants/Dining is ~$2k/mo, not ~$39/check.
    """
    if spend.empty or "Budget Month" not in spend.columns:
        return pd.Series(dtype=float)
    monthly_by_cat = (
        spend.groupby(["AI Category", "Budget Month"], dropna=False)["Spend Amount"]
        .sum()
    )
    return monthly_by_cat.groupby("AI Category", dropna=False).mean()

def build_monthly_expense_overview(
    df: pd.DataFrame,
    spend: pd.DataFrame,
) -> pd.DataFrame:
    """
    Month totals vs average monthly total expenses.
    Averages use all budget months present in the dataset (this export or history file).
    """
    empty = pd.DataFrame(
        columns=[
            "Budget Month",
            "Gross Income",
            "Total Expenses",
            "Avg Monthly Expenses",
            "vs Avg",
            "vs Avg %",
            "View Categories",
        ]
    )
    if spend.empty or "Budget Month" not in spend.columns:
        return empty

    monthly_totals = spend.groupby("Budget Month", dropna=False)["Spend Amount"].sum()
    overall_avg = float(monthly_totals.mean()) if len(monthly_totals) else 0.0
    budget_months = sorted(monthly_totals.index.tolist())

    rows: list[dict[str, Any]] = []
    for m in budget_months:
        total = float(monthly_totals.get(m, 0.0))
        vs_avg = total - overall_avg
        vs_pct = (vs_avg / overall_avg * 100) if overall_avg else 0.0

        income_budget = 0.0
        if "Budget Month" in df.columns:
            sub = df[df["Budget Month"] == m]
            income_budget = float(
                sub[
                    (sub["Flow Type"] == "Income") & (sub["Amount_Numeric"] > 0)
                ]["Amount_Numeric"].sum()
            )

        rows.append(
            {
                "Budget Month": m,
                "Gross Income": round(income_budget, 2),
                "Total Expenses": round(total, 2),
                "Avg Monthly Expenses": round(overall_avg, 2),
                "vs Avg": round(vs_avg, 2),
                "vs Avg %": round(vs_pct, 1),
                "View Categories": "",
            }
        )

    return pd.DataFrame(rows)

def build_month_category_breakdown(
    spend: pd.DataFrame,
    budget_month: str,
    *,
    cat_avg: pd.Series,
    month_total: float,
) -> pd.DataFrame:
    """
    Categories for one month: sorted by deviation from category average (largest first),
    then by month spend descending.
    """
    cols = [
        "AI Category",
        "Month Spend",
        "Avg Monthly Spend",
        "vs Avg",
        "vs Avg %",
        "% of Month",
    ]
    month_spend = spend[spend["Budget Month"] == budget_month]
    if month_spend.empty:
        return pd.DataFrame(columns=cols)

    by_cat = month_spend.groupby("AI Category", dropna=False)["Spend Amount"].sum()
    rows: list[dict[str, Any]] = []
    for cat, amt in by_cat.items():
        label = "" if pd.isna(cat) else str(cat)
        avg = float(cat_avg.get(cat, 0.0))
        amount = float(amt)
        vs_avg = amount - avg
        if avg:
            vs_pct = vs_avg / avg * 100
        else:
            vs_pct = 100.0 if amount else 0.0
        pct_month = (amount / month_total * 100) if month_total else 0.0
        rows.append(
            {
                "AI Category": label,
                "Month Spend": round(amount, 2),
                "Avg Monthly Spend": round(avg, 2),
                "vs Avg": round(vs_avg, 2),
                "vs Avg %": round(vs_pct, 1),
                "% of Month": round(pct_month, 1),
            }
        )

    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail
    detail["_dev_sort"] = detail["vs Avg %"].abs()
    detail = detail.sort_values(
        ["_dev_sort", "Month Spend"], ascending=[False, False], kind="stable"
    ).drop(columns=["_dev_sort"])
    return detail.reset_index(drop=True)

def write_spending_insights_sheet(
    writer: pd.ExcelWriter,
    df: pd.DataFrame,
    *,
    ingest_warning_count: int = 0,
) -> dict[str, Any]:
    """
    Summary sheet: monthly expense overview with hyperlinks to per-month category
    breakdown (deviation-first, then spend descending).
    """
    _ = ingest_warning_count  # reserved for future ingest notes on Summary
    spend = _spend_rows(df)
    overview = build_monthly_expense_overview(df, spend)
    sheet = SUMMARY_INSIGHTS_SHEET

    if overview.empty:
        pd.DataFrame(
            [["No included expense rows to build spending insights."]],
            columns=["Note"],
        ).to_excel(writer, sheet_name=sheet, index=False)
        return {"overview_rows": 0, "overview_header_row": 1, "detail_header_rows": [], "month_anchors": {}}

    overview_header_row = 1
    overview.to_excel(writer, sheet_name=sheet, index=False, startrow=0)

    cat_avg = _category_avg_monthly_spend(spend)
    monthly_totals = spend.groupby("Budget Month", dropna=False)["Spend Amount"].sum()
    budget_months = sorted(monthly_totals.index.tolist())

    start_detail = len(overview) + 3
    pd.DataFrame(
        [
            [
                "Category breakdown by month — click View Categories above, or scroll. "
                "Sorted: largest deviation from category average, then spend."
            ]
        ]
    ).to_excel(writer, sheet_name=sheet, index=False, header=False, startrow=start_detail - 1)

    current_row = start_detail + 1
    month_anchors: dict[str, int] = {}
    detail_header_rows: list[int] = []

    for m in budget_months:
        month_key = str(m)
        month_anchors[month_key] = current_row
        pd.DataFrame([[f"── {month_key} ──"]]).to_excel(
            writer,
            sheet_name=sheet,
            index=False,
            header=False,
            startrow=current_row - 1,
        )
        current_row += 1

        detail = build_month_category_breakdown(
            spend,
            month_key,
            cat_avg=cat_avg,
            month_total=float(monthly_totals.get(m, 0.0)),
        )
        detail_header_rows.append(current_row)
        detail.to_excel(writer, sheet_name=sheet, index=False, startrow=current_row - 1)
        current_row += len(detail) + 2

    return {
        "overview_rows": len(overview),
        "overview_header_row": overview_header_row,
        "detail_header_rows": detail_header_rows,
        "month_anchors": month_anchors,
    }

def build_baseline_sheets(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Historical spending baseline from included expense rows (by Budget Month).

    Returns overview metrics, per-category baseline, and monthly total spend.
    """
    spend = _spend_rows(df)
    empty_overview = pd.DataFrame(
        columns=["Metric", "Value"],
        data=[["Note", "No included expense rows to build a baseline."]],
    )
    empty_categories = pd.DataFrame(
        columns=[
            "AI Category",
            "Budget Tier",
            "Type",
            "Months With Spend",
            "Irregular Months",
            "Total Spend",
            "Typical Monthly Spend (Core)",
            "Avg Monthly Spend",
            "Min Month",
            "Min Month Spend",
            "Max Month",
            "Max Month Spend",
            "Buffer %",
            "Suggested Monthly Budget",
        ]
    )
    empty_monthly = pd.DataFrame(
        columns=["Budget Month", "Total Spend", "Core Monthly Spend", "vs Monthly Avg", "vs Monthly Avg %"]
    )

    if spend.empty:
        return empty_overview, empty_categories, empty_monthly

    if "In Monthly Run-Rate?" not in spend.columns:
        spend["In Monthly Run-Rate?"] = "Y"
    if "Expense Cadence" not in spend.columns:
        spend["Expense Cadence"] = CADENCE_UNKNOWN

    spend_core = spend[
        spend["In Monthly Run-Rate?"].astype(str).str.strip().str.upper() == "Y"
    ]

    budget_months = sorted(spend["Budget Month"].dropna().unique().tolist())
    n_months = len(budget_months)

    monthly_by_category = (
        spend.groupby(["Budget Month", "AI Category"], dropna=False)["Spend Amount"]
        .sum()
        .reset_index()
    )
    monthly_totals = (
        spend.groupby("Budget Month", dropna=False)["Spend Amount"]
        .sum()
        .sort_index()
    )
    monthly_totals_core = (
        spend_core.groupby("Budget Month", dropna=False)["Spend Amount"]
        .sum()
        .sort_index()
    )
    overall_avg = float(monthly_totals.mean()) if n_months else 0.0
    overall_core_avg = (
        float(monthly_totals_core.mean()) if not monthly_totals_core.empty else 0.0
    )
    overall_min = float(monthly_totals.min()) if n_months else 0.0
    overall_max = float(monthly_totals.max()) if n_months else 0.0
    min_total_month = monthly_totals.idxmin() if n_months else ""
    max_total_month = monthly_totals.idxmax() if n_months else ""

    stability_note = (
        f"Based on {n_months} month(s) in this file."
        if n_months >= BASELINE_RECOMMENDED_MONTHS
        else (
            f"Based on {n_months} month(s); {BASELINE_RECOMMENDED_MONTHS}+ months "
            "recommended for a stable baseline."
        )
    )

    overview = pd.DataFrame(
        [
            {"Metric": "Budget months analyzed", "Value": n_months},
            {"Metric": "Month range", "Value": f"{budget_months[0]} → {budget_months[-1]}"},
            {"Metric": "Overall avg monthly spend", "Value": round(overall_avg, 2)},
            {
                "Metric": "Overall typical core monthly spend",
                "Value": round(overall_core_avg, 2),
            },
            {"Metric": "Lowest month (total spend)", "Value": f"{min_total_month} ({round(overall_min, 2)})"},
            {"Metric": "Highest month (total spend)", "Value": f"{max_total_month} ({round(overall_max, 2)})"},
            {
                "Metric": "Suggested variable buffer",
                "Value": f"{BASELINE_VARIABLE_BUFFER_PCT:g}% on avg (Variable categories)",
            },
            {
                "Metric": "Suggested fixed buffer",
                "Value": f"{BASELINE_FIXED_BUFFER_PCT:g}% on avg (Fixed categories)",
            },
            {"Metric": "Note", "Value": stability_note},
        ]
    )

    category_rows: list[dict[str, Any]] = []
    for category, grp in monthly_by_category.groupby("AI Category", dropna=False):
        by_month = grp.set_index("Budget Month")["Spend Amount"]
        cat_label = "" if pd.isna(category) else str(category)
        cat_spend = spend[spend["AI Category"] == category]
        type_mode = (
            cat_spend["Type"].mode().iat[0]
            if not cat_spend.empty and not cat_spend["Type"].mode().empty
            else "Variable"
        )
        tier_mode = (
            cat_spend["Budget Tier"].mode().iat[0]
            if not cat_spend.empty and not cat_spend["Budget Tier"].mode().empty
            else ""
        )
        buffer_pct = (
            BASELINE_FIXED_BUFFER_PCT
            if str(type_mode).strip().lower() == "fixed"
            else BASELINE_VARIABLE_BUFFER_PCT
        )
        avg_spend = float(by_month.mean())
        cat_core = cat_spend[
            cat_spend["In Monthly Run-Rate?"].astype(str).str.strip().str.upper() == "Y"
        ]
        if not cat_core.empty:
            core_by_month = cat_core.groupby("Budget Month", dropna=False)["Spend Amount"].sum()
            typical_core = float(core_by_month.median())
        else:
            typical_core = 0.0
        irregular_months = 0
        irr = cat_spend[
            cat_spend["Expense Cadence"].isin(
                [CADENCE_YEARLY, CADENCE_ONETIME, CADENCE_UNPLANNED]
            )
        ]
        if not irr.empty and "Budget Month" in irr.columns:
            irregular_months = int(irr["Budget Month"].nunique())
        budget_base = typical_core if typical_core > 0 else avg_spend
        category_rows.append(
            {
                "AI Category": cat_label,
                "Budget Tier": tier_mode,
                "Type": type_mode,
                "Months With Spend": int(by_month.count()),
                "Irregular Months": irregular_months,
                "Total Spend": round(float(by_month.sum()), 2),
                "Typical Monthly Spend (Core)": round(typical_core, 2),
                "Avg Monthly Spend": round(avg_spend, 2),
                "Min Month": by_month.idxmin(),
                "Min Month Spend": round(float(by_month.min()), 2),
                "Max Month": by_month.idxmax(),
                "Max Month Spend": round(float(by_month.max()), 2),
                "Buffer %": buffer_pct,
                "Suggested Monthly Budget": round(budget_base * (1 + buffer_pct / 100), 2),
            }
        )

    categories = pd.DataFrame(category_rows).sort_values(
        "Avg Monthly Spend", ascending=False, na_position="last"
    ).reset_index(drop=True)
    suggested_total = round(float(categories["Suggested Monthly Budget"].sum()), 2)

    monthly_df = monthly_totals.reset_index()
    monthly_df.columns = ["Budget Month", "Total Spend"]
    monthly_df["Total Spend"] = monthly_df["Total Spend"].round(2)
    monthly_df["Core Monthly Spend"] = (
        monthly_df["Budget Month"]
        .map(monthly_totals_core.to_dict())
        .fillna(0)
        .round(2)
    )
    monthly_df["vs Monthly Avg"] = (monthly_df["Total Spend"] - overall_avg).round(2)
    if overall_avg:
        monthly_df["vs Monthly Avg %"] = (
            (monthly_df["Total Spend"] - overall_avg) / overall_avg * 100
        ).round(1)
    else:
        monthly_df["vs Monthly Avg %"] = 0.0

    overview = pd.concat(
        [
            overview,
            pd.DataFrame(
                [{"Metric": "Sum of suggested category budgets", "Value": suggested_total}]
            ),
        ],
        ignore_index=True,
    )

    return overview, categories, monthly_df

def write_baseline_sheet(writer: pd.ExcelWriter, df: pd.DataFrame) -> dict[str, int]:
    """Write Baseline tab: overview + category budget table (monthly drill-down is on Summary)."""
    overview, categories, _monthly = build_baseline_sheets(df)
    sheet = "Baseline"
    overview.to_excel(writer, sheet_name=sheet, index=False, startrow=0)
    start_categories = len(overview) + 2
    categories_header_row = start_categories + 1
    pd.DataFrame(
        [["Category budgets (historical average + buffer — see Summary for month drill-down)"]]
    ).to_excel(
        writer, sheet_name=sheet, index=False, header=False, startrow=start_categories - 1
    )
    categories.to_excel(writer, sheet_name=sheet, index=False, startrow=start_categories)
    return {
        "overview_rows": len(overview),
        "categories_header_row": categories_header_row,
        "monthly_header_row": None,
    }
