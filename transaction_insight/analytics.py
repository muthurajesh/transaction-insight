"""Analytics aligned with CLI spend semantics (Include in Spend?, negative amounts)."""

from __future__ import annotations

import sqlite3
from typing import Any


def month_total(
    conn: sqlite3.Connection,
    month: str | None,
    *,
    flow: str = "Expense",
    expense_view: str = "cash",
) -> dict[str, Any]:
    """Total spend or income for one budget month (CLI-aligned expense rules)."""
    if not month:
        overview = available_months(conn)
        full = overview.get("full_months") or []
        if not full:
            raise ValueError("No full months in database — run processing on inbox CSV first.")
        month = full[0]

    if flow == "Expense" and (expense_view or "cash").strip().lower() != "cash":
        from webapp.services.expense_cadence import (
            expense_view_label,
            sum_expenses_for_view,
            validate_expense_view,
        )

        view = validate_expense_view(expense_view)
        total, count = sum_expenses_for_view(conn, view=view, budget_month=month)
        return {
            "month": month,
            "flow_type": flow,
            "expense_view": view,
            "expense_view_label": expense_view_label(view),
            "transaction_count": count,
            "total": total,
        }

    if flow == "Expense":
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt,
                   SUM(-amount) AS total
            FROM transactions
            WHERE budget_month = ?
              AND flow_type = 'Expense'
              AND amount < 0
            """,
            (month,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt,
                   SUM(amount) AS total
            FROM transactions
            WHERE budget_month = ? AND flow_type = ?
            """,
            (month, flow),
        ).fetchone()
    total = float(row["total"] or 0) if row else 0.0
    return {
        "month": month,
        "flow_type": flow,
        "transaction_count": int(row["cnt"] or 0) if row else 0,
        "total": round(total, 2),
    }


def flow_totals_by_month(
    conn: sqlite3.Connection,
    *,
    flow: str = "Income",
    full_months_only: bool = True,
    expense_view: str = "cash",
) -> dict[str, Any]:
    """
    Totals for every budget month. Income uses flow_type=Income and budget_month
    (paycheck spillover is already shifted into budget_month during processing).
    """
    if flow == "Expense" and (expense_view or "cash").strip().lower() != "cash":
        from webapp.services.expense_cadence import (
            expense_view_label,
            flow_totals_expense_by_view,
            validate_expense_view,
        )

        view = validate_expense_view(expense_view)
        result = flow_totals_expense_by_view(
            conn, view=view, full_months_only=full_months_only
        )
        result["expense_view_label"] = expense_view_label(view)
        return result

    if flow == "Expense":
        rows = conn.execute(
            """
            SELECT budget_month,
                   COUNT(*) AS cnt,
                   SUM(-amount) AS total
            FROM transactions
            WHERE flow_type = 'Expense' AND amount < 0
            GROUP BY budget_month
            ORDER BY budget_month ASC
            """
        ).fetchall()
    elif flow == "Income":
        rows = conn.execute(
            """
            SELECT budget_month,
                   COUNT(*) AS cnt,
                   SUM(amount) AS total
            FROM transactions
            WHERE flow_type = 'Income'
            GROUP BY budget_month
            ORDER BY budget_month ASC
            """
        ).fetchall()
    else:
        raise ValueError(f"flow must be Income or Expense, got: {flow}")

    full_set: set[str] | None = None
    if full_months_only:
        full_set = set(available_months(conn).get("full_months") or [])

    months: list[dict[str, Any]] = []
    grand_total = 0.0
    for r in rows:
        month = str(r["budget_month"])
        if full_set is not None and month not in full_set:
            continue
        total = round(float(r["total"] or 0), 2)
        months.append(
            {
                "month": month,
                "transaction_count": int(r["cnt"] or 0),
                "total": total,
            }
        )
        grand_total += total

    return {
        "flow_type": flow,
        "full_months_only": full_months_only,
        "months": months,
        "grand_total": round(grand_total, 2),
    }


def top_categories(
    conn: sqlite3.Connection,
    month: str,
    *,
    limit: int = 10,
    expense_view: str = "cash",
) -> list[dict[str, Any]]:
    if (expense_view or "cash").strip().lower() != "cash":
        from webapp.services.expense_cadence import (
            top_categories_for_view,
            validate_expense_view,
        )

        return top_categories_for_view(
            conn, month, limit=limit, view=validate_expense_view(expense_view)
        )

    rows = conn.execute(
        """
        SELECT ai_category AS category,
               COUNT(*) AS transaction_count,
               SUM(-amount) AS spend
        FROM transactions
        WHERE budget_month = ?
          AND flow_type = 'Expense'
          AND amount < 0
          AND ai_category IS NOT NULL AND ai_category != ''
        GROUP BY ai_category
        ORDER BY spend DESC
        LIMIT ?
        """,
        (month, limit),
    ).fetchall()
    return [
        {
            "category": r["category"],
            "transaction_count": int(r["transaction_count"]),
            "spend": round(float(r["spend"] or 0), 2),
        }
        for r in rows
    ]


def month_vs_avg(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT budget_month, SUM(-amount) AS total
        FROM transactions
        WHERE flow_type = 'Expense' AND amount < 0
        GROUP BY budget_month
        """
    ).fetchall()
    totals = {r["budget_month"]: float(r["total"] or 0) for r in rows}
    if not totals:
        return {"month": month, "total": 0, "avg_monthly": 0, "vs_avg": 0, "vs_avg_pct": 0}
    month_total_val = totals.get(month, 0.0)
    avg = sum(totals.values()) / len(totals)
    vs = month_total_val - avg
    pct = (vs / avg * 100) if avg else 0.0
    return {
        "month": month,
        "total": round(month_total_val, 2),
        "avg_monthly": round(avg, 2),
        "vs_avg": round(vs, 2),
        "vs_avg_pct": round(pct, 1),
        "months_in_baseline": len(totals),
    }


def list_outliers(
    conn: sqlite3.Connection, month: str, *, threshold_pct: float = 50.0
) -> list[dict[str, Any]]:
    hist = conn.execute(
        """
        SELECT ai_category, budget_month, SUM(-amount) AS spend
        FROM transactions
        WHERE flow_type = 'Expense' AND amount < 0 AND ai_category IS NOT NULL
        GROUP BY ai_category, budget_month
        """
    ).fetchall()

    by_cat: dict[str, list[float]] = {}
    month_spend: dict[str, float] = {}
    for r in hist:
        cat = r["ai_category"]
        spend = float(r["spend"] or 0)
        by_cat.setdefault(cat, []).append(spend)
        if r["budget_month"] == month:
            month_spend[cat] = spend

    outliers = []
    for cat, spend in month_spend.items():
        history = by_cat.get(cat, [])
        if len(history) < 2:
            continue
        avg = sum(history) / len(history)
        if avg <= 0:
            continue
        pct = (spend - avg) / avg * 100
        if abs(pct) >= threshold_pct:
            outliers.append(
                {
                    "category": cat,
                    "month_spend": round(spend, 2),
                    "avg_monthly_spend": round(avg, 2),
                    "vs_avg_pct": round(pct, 1),
                }
            )
    outliers.sort(key=lambda x: abs(x["vs_avg_pct"]), reverse=True)
    return outliers


def available_months(
    conn: sqlite3.Connection,
    *,
    full_month_min_transactions: int = 20,
) -> dict[str, Any]:
    """
    Months in the DB with counts. A *full month* is a budget month with at least
    full_month_min_transactions rows (excludes payroll spillover-only months).
    """
    rows = conn.execute(
        """
        SELECT budget_month,
               COUNT(*) AS transaction_count,
               COUNT(DISTINCT source_file) AS source_files,
               SUM(CASE WHEN ai_category IS NOT NULL AND TRIM(ai_category) != '' THEN 1 ELSE 0 END)
                   AS categorized_count
        FROM transactions
        GROUP BY budget_month
        ORDER BY budget_month DESC
        """
    ).fetchall()
    months: list[dict[str, Any]] = []
    full_months: list[str] = []
    for r in rows:
        month = str(r["budget_month"])
        count = int(r["transaction_count"] or 0)
        categorized = int(r["categorized_count"] or 0)
        is_full = count >= full_month_min_transactions
        entry: dict[str, Any] = {
            "month": month,
            "transaction_count": count,
            "categorized_count": categorized,
            "source_files": int(r["source_files"] or 0),
            "full_month": is_full,
        }
        if not is_full and count > 0:
            entry["note"] = (
                "Partial month only (e.g. payroll spillover); not a full export."
            )
        months.append(entry)
        if is_full:
            full_months.append(month)
    return {
        "months": months,
        "full_months": full_months,
        "all_months": [m["month"] for m in months],
    }


def list_transactions(
    conn: sqlite3.Connection,
    month: str,
    *,
    category: str | None = None,
    flow: str = "Expense",
    limit: int = 100,
) -> dict[str, Any]:
    """
    List individual transactions for a budget month, optionally filtered by ai_category.
    Expense rows are outflows only (amount < 0), matching other spend tools.
    """
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    clauses = ["budget_month = ?"]
    params: list[Any] = [month]

    if flow == "Expense":
        clauses.append("flow_type = 'Expense'")
        clauses.append("amount < 0")
    elif flow in ("Income", "Transfer", "Adjustment"):
        clauses.append("flow_type = ?")
        params.append(flow)
    else:
        raise ValueError(f"flow must be Income, Expense, Transfer, or Adjustment, got: {flow}")

    if category:
        clauses.append("LOWER(ai_category) = LOWER(?)")
        params.append(category.strip())

    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT date,
               amount,
               merchant_key,
               ai_category,
               ai_sub_category,
               expense_type,
               classification,
               flow_type,
               original_description
        FROM transactions
        WHERE {where}
        ORDER BY date DESC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    transactions: list[dict[str, Any]] = []
    total = 0.0
    for r in rows:
        amount = float(r["amount"] or 0)
        if flow == "Expense":
            total += -amount
        else:
            total += amount
        transactions.append(
            {
                "date": str(r["date"] or ""),
                "amount": round(amount, 2),
                "merchant_key": str(r["merchant_key"] or ""),
                "ai_category": str(r["ai_category"] or ""),
                "ai_sub_category": str(r["ai_sub_category"] or ""),
                "expense_type": str(r["expense_type"] or ""),
                "classification": str(r["classification"] or ""),
                "flow_type": str(r["flow_type"] or flow),
                "description": str(r["original_description"] or "").strip(),
            }
        )

    return {
        "month": month,
        "category": category,
        "flow_type": flow,
        "transaction_count": len(transactions),
        "total": round(total, 2),
        "transactions": transactions,
        "truncated": len(transactions) >= limit,
    }
