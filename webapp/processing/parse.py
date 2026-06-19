from __future__ import annotations

from pathlib import Path

import pandas as pd

from webapp.processing.constants import DROP_OUTPUT_COLUMNS


def parse_amount(value: str | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "").replace("$", "")
    return float(cleaned) if cleaned else 0.0

def parse_transaction_dates(date_series: pd.Series) -> pd.Series:
    """
    Parse bank export dates into datetime64.

    Supports common US export formats (M/D/Y and M/D/YY), ISO dates, and values
    already stored as datetimes (e.g. from Excel history sheets).
    """
    if date_series.empty:
        return pd.Series(dtype="datetime64[ns]")

    if pd.api.types.is_datetime64_any_dtype(date_series):
        return pd.to_datetime(date_series, errors="coerce")

    raw = date_series.fillna("").astype(str).str.strip()
    empty_mask = raw.isin(("", "nan", "NaT", "None", "nat"))
    result = pd.Series(pd.NaT, index=date_series.index, dtype="datetime64[ns]")

    if not empty_mask.all():
        values = raw[~empty_mask]
        # pandas 2+: mixed M/D/Y, M/D/YY, ISO, etc. in one column
        try:
            mixed = pd.to_datetime(values, format="mixed", errors="coerce")
            result.loc[mixed.index] = mixed
        except (ValueError, TypeError):
            pass

        still_missing = result.isna() & ~empty_mask
        explicit_formats = (
            "%m/%d/%y",
            "%m/%d/%Y",
            "%m-%d-%y",
            "%m-%d-%Y",
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%y %H:%M:%S",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%b %d, %Y",
            "%b %d, %y",
        )
        for fmt in explicit_formats:
            if not still_missing.any():
                break
            attempt = pd.to_datetime(
                raw.loc[still_missing], format=fmt, errors="coerce"
            )
            result.loc[still_missing] = attempt
            still_missing = result.isna() & ~empty_mask

        if still_missing.any():
            inferred = pd.to_datetime(
                raw.loc[still_missing], errors="coerce", dayfirst=False
            )
            result.loc[still_missing] = inferred

    return result

def drop_rows_with_invalid_dates(
    df: pd.DataFrame,
    *,
    date_column: str = "Date",
    out_column: str = "Transaction Date",
) -> tuple[pd.DataFrame, int]:
    """Parse transaction dates; drop rows that cannot be parsed. Returns (df, dropped_count)."""
    if date_column not in df.columns:
        raise ValueError(f"Missing column: {date_column}")

    n_before = len(df)
    out = df.copy()
    out[out_column] = parse_transaction_dates(out[date_column])
    bad_mask = out[out_column].isna()
    dropped = int(bad_mask.sum())
    if dropped:
        examples = out.loc[bad_mask, date_column].astype(str).head(5).tolist()
        print(
            f"  Warning: dropped {dropped} of {n_before} row(s) with unparseable "
            f"'{date_column}'",
            flush=True,
        )
        if examples:
            print(f"  Unparseable examples: {examples}", flush=True)
    out = out.loc[~bad_mask].copy()
    return out, dropped

def merchant_key(row: pd.Series) -> str:
    """Best-effort merchant identifier for lookup matching."""
    explicit = str(row.get("Merchant Key", "") or "").strip()
    if explicit:
        return explicit[:120]
    gd = str(row.get("Generated Description", "") or "").strip()
    if gd:
        return " ".join(gd.split())[:120]
    sd = str(row.get("Simple Description", "") or "").strip()
    od = str(row.get("Original Description", "") or "").strip()
    key = sd if sd else od
    return " ".join(key.split())[:120]

def ensure_merchant_key_column(df: pd.DataFrame) -> pd.DataFrame:
    """Populate Merchant Key from generated description when missing."""
    if "Merchant Key" not in df.columns:
        df = df.copy()
        df["Merchant Key"] = df.apply(merchant_key, axis=1)
        return df
    missing = df["Merchant Key"].fillna("").astype(str).str.strip() == ""
    if missing.any():
        df = df.copy()
        df.loc[missing, "Merchant Key"] = df.loc[missing].apply(merchant_key, axis=1)
    return df

def _row_merchant_key(row: pd.Series) -> str:
    return str(row.get("Merchant Key", "") or merchant_key(row)).strip()

def _is_semantic_sub_category(row: pd.Series) -> bool:
    sub = str(row.get("AI Sub-Category", "") or "").strip()
    if not sub:
        return False
    mk = _row_merchant_key(row).lower()
    return sub.lower() != mk

def _merchant_category_is_semantic(row: pd.Series) -> bool:
    mk = str(row.get("Merchant Key", "") or "").strip().lower()
    sub = str(row.get("AI Sub-Category", "") or "").strip().lower()
    return bool(sub) and sub != mk

def is_business_row(row: pd.Series) -> bool:
    return str(row.get("Classification", "") or "").strip().lower() == "business"

def _clean_original_for_display(text: str) -> str:
    """Strip common bank noise from Original Description for heuristic labels."""
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    t = re.sub(r"\bx{6,}\d+\b", "", t, flags=re.I)
    t = re.sub(r"\bDES:.*", "", t, flags=re.I)
    t = re.sub(r"\bID:.*", "", t, flags=re.I)
    t = re.sub(r"\bINDN:.*", "", t, flags=re.I)
    t = re.sub(r"\bCO ID:.*", "", t, flags=re.I)
    t = re.sub(r"\bMOBILE PURCHASE\s+\d{4}\s*", "", t, flags=re.I)
    return " ".join(t.split()).strip()[:120]

def heuristic_generated_description(row: pd.Series) -> str:
    user = str(row.get("User Description", "") or "").strip()
    simple = str(row.get("Simple Description", "") or "").strip()
    original = _clean_original_for_display(str(row.get("Original Description", "") or ""))
    if user:
        return user[:120]
    if simple:
        return simple[:120]
    return original[:120] if original else "Unknown"

def _norm_description_part(text: str) -> str:
    return " ".join(str(text or "").split()).strip().lower()

def transaction_fingerprint(row: pd.Series) -> str:
    parts = [
        str(row.get("Date", "") or "").strip(),
        str(row.get("Amount", "") or "").strip(),
        str(row.get("Account Name", "") or "").strip(),
        str(row.get("Original Description", "") or "").strip()[:200],
    ]
    return "|".join(parts)

def assign_transaction_ids(df: pd.DataFrame) -> pd.DataFrame:
    from webapp.parsing import transaction_id_from_row

    df = df.copy()
    parsed_dates = (
        df["Transaction Date"]
        if "Transaction Date" in df.columns
        else parse_transaction_dates(df["Date"])
    )

    def _tid(row: pd.Series) -> str:
        idx = row.name
        parsed = parsed_dates.loc[idx] if idx in parsed_dates.index else None
        return transaction_id_from_row(row, parsed_date=parsed)

    df["Transaction ID"] = df.apply(_tid, axis=1)
    return df

def prepare_transaction_export_df(df: pd.DataFrame) -> pd.DataFrame:
    """Columns for transaction tabs: drop clutter, order Generated Description prominently."""
    out = df.drop(columns=["Amount_Numeric"], errors="ignore")
    out = out.drop(columns=[c for c in DROP_OUTPUT_COLUMNS if c in out.columns], errors="ignore")

    if "Generated Description" not in out.columns:
        out["Generated Description"] = out.apply(heuristic_generated_description, axis=1)

    # Place Generated Description after Date when possible
    preferred_front = [
        "Transaction ID",
        "Date",
        "Generated Description",
        "Merchant Key",
        "Amount",
        "Category",
        "Account Name",
        "Classification",
        "Expense Cadence",
        "In Monthly Run-Rate?",
        "Cadence Source",
    ]
    front = [c for c in preferred_front if c in out.columns]
    rest = [c for c in out.columns if c not in front]
    out = out[front + rest]
    if "Amount" in out.columns:
        if "Amount_Numeric" in df.columns:
            out["Amount"] = pd.to_numeric(df["Amount_Numeric"], errors="coerce")
        else:
            out["Amount"] = out["Amount"].apply(parse_amount)
    if "Date" in out.columns:
        if "Transaction Date" in df.columns:
            out["Date"] = pd.to_datetime(df["Transaction Date"], errors="coerce")
        else:
            out["Date"] = parse_transaction_dates(out["Date"])
    return out

def sort_expenses_for_export(expense_df: pd.DataFrame) -> pd.DataFrame:
    """Sort expense rows for the Expenses tab: AI Category, then Generated Description."""
    if expense_df.empty:
        return expense_df
    sort_cols = [c for c in ("AI Category", "Generated Description") if c in expense_df.columns]
    if not sort_cols:
        return expense_df
    return expense_df.sort_values(sort_cols, na_position="last", kind="stable").reset_index(
        drop=True
    )

def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype=str,
        encoding="utf-8-sig",
        engine="python",
        on_bad_lines="skip",
    )
    df.columns = [c.strip() for c in df.columns]
    if "Amount" in df.columns:
        df["Amount_Numeric"] = df["Amount"].apply(parse_amount)
    return df
