from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from webapp.config import AUTO_CONFIDENCE, CATEGORIZE_BATCH_SIZE, REVIEW_CONFIDENCE
from webapp.services.llm import chat_completion, extract_json

# Merchants reviewed one transaction at a time (amounts/categories differ per check).
SPLIT_REVIEW_MERCHANT_KEYS: frozenset[str] = frozenset({"Check Payment"})

MERCHANT_PROMPT = """You are a personal finance analyst. Classify each merchant below based on sample transactions.

For each merchant, return:
- section: "Income" or "Expense"
- category: top-level category (Housing, Utilities, Groceries, Dining, Transportation, Healthcare, Insurance, Entertainment, Income, Transfers, Savings, Subscriptions, Pets, Shopping, Personal Care, Education, Charitable, Fees, Other)
- sub_category: specific label
- type: "Fixed" or "Variable"
- confidence: 0.0 to 1.0 (how sure you are)
- rationale: one short sentence

Credit card payments are Expense (transfers), not Income.

Return ONLY valid JSON: {"results": [{"merchant_key": "<string>", "section": "...", "category": "...", "sub_category": "...", "type": "...", "confidence": 0.9, "rationale": "..."}]}
"""


def _label_status(confidence: float) -> str:
    if confidence >= AUTO_CONFIDENCE:
        return "auto"
    if confidence >= REVIEW_CONFIDENCE:
        return "needs_review"
    return "needs_review"


def _merchant_samples(conn: sqlite3.Connection, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT merchant_key,
               COUNT(*) AS cnt,
               AVG(amount) AS avg_amount,
               MIN(source_category) AS sample_category,
               MIN(simple_description) AS simple_desc,
               MIN(original_description) AS orig_desc
        FROM transactions
        WHERE merchant_key IS NOT NULL AND merchant_key != ''
        GROUP BY merchant_key
        ORDER BY cnt DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        cached = conn.execute(
            "SELECT label_status FROM merchant_labels WHERE merchant_key = ?",
            (r["merchant_key"],),
        ).fetchone()
        if cached and cached["label_status"] in ("auto", "confirmed"):
            continue
        out.append(dict(r))
    return out


def _merchants_to_categorize(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    max_merchants: int = 200,
) -> list[dict[str, Any]]:
    if force:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT merchant_key, COUNT(*) AS cnt,
                       AVG(amount) AS avg_amount,
                       MIN(source_category) AS sample_category,
                       MIN(simple_description) AS simple_desc,
                       MIN(original_description) AS orig_desc
                FROM transactions
                GROUP BY merchant_key
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (max_merchants,),
            ).fetchall()
        ]
    return _merchant_samples(conn, limit=max_merchants)


def iter_categorize_merchants(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    max_merchants: int = 200,
):
    """Yield progress events (dicts with 'type' field) then commit at the end."""
    merchants = _merchants_to_categorize(conn, force=force, max_merchants=max_merchants)
    total = len(merchants)
    total_batches = max(1, (total + CATEGORIZE_BATCH_SIZE - 1) // CATEGORIZE_BATCH_SIZE) if total else 0

    yield {
        "type": "start",
        "total_merchants": total,
        "total_batches": total_batches,
        "batch_size": CATEGORIZE_BATCH_SIZE,
        "message": (
            f"Labeling {total} merchant(s) in {total_batches} batch(es)…"
            if total
            else "No merchants need categorization (all auto/confirmed or empty DB)."
        ),
    }

    if not total:
        yield {"type": "done", "labeled": 0, "rows_updated": 0, "errors": 0}
        return

    labeled = applied = errors = 0
    now = datetime.now(timezone.utc).isoformat()

    for batch_num, i in enumerate(range(0, len(merchants), CATEGORIZE_BATCH_SIZE), start=1):
        batch = merchants[i : i + CATEGORIZE_BATCH_SIZE]
        preview = [str(m["merchant_key"])[:60] for m in batch[:4]]
        yield {
            "type": "batch_start",
            "batch": batch_num,
            "total_batches": total_batches,
            "merchants_in_batch": len(batch),
            "merchants_done": i,
            "total_merchants": total,
            "preview": preview,
            "message": f"Batch {batch_num}/{total_batches}: calling LLM for {len(batch)} merchant(s)…",
        }

        payload = [
            {
                "merchant_key": m["merchant_key"],
                "transaction_count": int(m["cnt"]),
                "avg_amount": round(float(m["avg_amount"] or 0), 2),
                "bank_category": m.get("sample_category") or "",
                "sample_description": (m.get("simple_desc") or m.get("orig_desc") or "")[:200],
            }
            for m in batch
        ]

        user_msg = json.dumps({"merchants": payload}, separators=(",", ":"))
        batch_labeled = 0
        batch_errors = 0
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": MERCHANT_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            parsed = extract_json(raw)
            results = parsed.get("results", parsed if isinstance(parsed, list) else [])
        except Exception as exc:
            batch_errors = len(batch)
            errors += batch_errors
            yield {
                "type": "batch_error",
                "batch": batch_num,
                "total_batches": total_batches,
                "error": str(exc)[:300],
                "message": f"Batch {batch_num} failed: {exc}",
            }
            continue

        by_key = {r.get("merchant_key"): r for r in results if r.get("merchant_key")}

        for m in batch:
            mk = m["merchant_key"]
            item = by_key.get(mk)
            if not item:
                batch_errors += 1
                errors += 1
                continue

            conf = float(item.get("confidence", 0.5))
            status = _label_status(conf)
            section = str(item.get("section", "Expense"))
            category = str(item.get("category", "Other"))
            sub = str(item.get("sub_category", ""))
            exp_type = str(item.get("type", "Variable"))
            rationale = str(item.get("rationale", ""))

            conn.execute(
                """
                INSERT INTO merchant_labels (
                    merchant_key, ai_category, ai_sub_category, expense_type,
                    confidence, label_status, rationale, sample_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(merchant_key) DO UPDATE SET
                    ai_category=excluded.ai_category,
                    ai_sub_category=excluded.ai_sub_category,
                    expense_type=excluded.expense_type,
                    confidence=excluded.confidence,
                    label_status=excluded.label_status,
                    rationale=excluded.rationale,
                    sample_count=excluded.sample_count,
                    updated_at=excluded.updated_at
                """,
                (mk, category, sub, exp_type, conf, status, rationale, int(m["cnt"]), now),
            )
            labeled += 1
            batch_labeled += 1
            applied += _apply_merchant_label(
                conn, mk, section, category, sub, exp_type, conf, status, rationale
            )

        merchants_done = min(i + len(batch), total)
        pct = round(merchants_done / total * 100, 1) if total else 100
        yield {
            "type": "batch_done",
            "batch": batch_num,
            "total_batches": total_batches,
            "batch_labeled": batch_labeled,
            "batch_errors": batch_errors,
            "labeled": labeled,
            "errors": errors,
            "rows_updated": applied,
            "merchants_done": merchants_done,
            "total_merchants": total,
            "percent": pct,
            "message": (
                f"Batch {batch_num}/{total_batches} done — "
                f"{merchants_done}/{total} merchants ({pct}%)"
            ),
        }

    conn.commit()
    yield {
        "type": "done",
        "labeled": labeled,
        "rows_updated": applied,
        "errors": errors,
        "total_merchants": total,
        "message": f"Finished: {labeled} merchant(s) labeled, {errors} error(s).",
    }


def categorize_merchants(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    max_merchants: int = 200,
) -> dict[str, int]:
    result = {"labeled": 0, "rows_updated": 0, "errors": 0}
    for event in iter_categorize_merchants(conn, force=force, max_merchants=max_merchants):
        if event.get("type") == "done":
            result = {
                "labeled": event["labeled"],
                "rows_updated": event["rows_updated"],
                "errors": event["errors"],
            }
    return result


def _apply_merchant_label(
    conn: sqlite3.Connection,
    merchant_key: str,
    section: str,
    category: str,
    sub: str,
    exp_type: str,
    conf: float,
    status: str,
    rationale: str,
) -> int:
    flow = section if section in ("Income", "Expense") else "Expense"
    cur = conn.execute(
        """
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            confidence = ?,
            label_status = ?,
            rationale = ?
        WHERE merchant_key = ?
        """,
        (flow, category, sub, exp_type, conf, status, rationale, merchant_key),
    )
    return cur.rowcount


def confirm_merchant(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at
        ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'user confirmed', 0, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            ai_category=excluded.ai_category,
            ai_sub_category=excluded.ai_sub_category,
            expense_type=excluded.expense_type,
            confidence=1.0,
            label_status='confirmed',
            updated_at=excluded.updated_at
        """,
        (merchant_key, ai_category, ai_sub_category, expense_type, now),
    )
    cur = conn.execute(
        """
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            confidence = 1.0,
            label_status = 'confirmed',
            rationale = 'user confirmed'
        WHERE merchant_key = ?
          AND label_status IN ('needs_review', 'pending')
        """,
        (flow_type, ai_category, ai_sub_category, expense_type, merchant_key),
    )
    conn.commit()
    return cur.rowcount


def confirm_transaction(
    conn: sqlite3.Connection,
    transaction_id: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
) -> int:
    cur = conn.execute(
        """
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            confidence = 1.0,
            label_status = 'confirmed',
            rationale = 'user confirmed'
        WHERE transaction_id = ?
          AND label_status IN ('needs_review', 'pending')
        """,
        (flow_type, ai_category, ai_sub_category, expense_type, transaction_id),
    )
    conn.commit()
    return cur.rowcount


def list_review_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    split_keys = tuple(SPLIT_REVIEW_MERCHANT_KEYS)
    items: list[dict[str, Any]] = []

    if split_keys:
        placeholders = ",".join("?" * len(split_keys))
        grouped = conn.execute(
            f"""
            SELECT merchant_key,
                   COUNT(*) AS transaction_count,
                   SUM(CASE WHEN amount < 0 THEN -amount ELSE amount END) AS total_spend,
                   MAX(ai_category) AS ai_category,
                   MAX(ai_sub_category) AS ai_sub_category,
                   MAX(flow_type) AS flow_type,
                   MAX(expense_type) AS expense_type,
                   MAX(confidence) AS confidence,
                   MAX(label_status) AS label_status,
                   MIN(simple_description) AS sample_description
            FROM transactions
            WHERE label_status IN ('needs_review', 'pending')
              AND merchant_key NOT IN ({placeholders})
            GROUP BY merchant_key
            ORDER BY transaction_count DESC
            """,
            split_keys,
        ).fetchall()
    else:
        grouped = conn.execute(
            """
            SELECT merchant_key,
                   COUNT(*) AS transaction_count,
                   SUM(CASE WHEN amount < 0 THEN -amount ELSE amount END) AS total_spend,
                   MAX(ai_category) AS ai_category,
                   MAX(ai_sub_category) AS ai_sub_category,
                   MAX(flow_type) AS flow_type,
                   MAX(expense_type) AS expense_type,
                   MAX(confidence) AS confidence,
                   MAX(label_status) AS label_status,
                   MIN(simple_description) AS sample_description
            FROM transactions
            WHERE label_status IN ('needs_review', 'pending')
            GROUP BY merchant_key
            ORDER BY transaction_count DESC
            """
        ).fetchall()

    for row in grouped:
        item = dict(row)
        item["review_mode"] = "merchant"
        items.append(item)

    if split_keys:
        placeholders = ",".join("?" * len(split_keys))
        split_rows = conn.execute(
            f"""
            SELECT transaction_id,
                   merchant_key,
                   date,
                   amount,
                   ai_category,
                   ai_sub_category,
                   flow_type,
                   expense_type,
                   confidence,
                   label_status,
                   COALESCE(NULLIF(simple_description, ''), NULLIF(user_description, ''),
                            NULLIF(original_description, '')) AS sample_description
            FROM transactions
            WHERE label_status IN ('needs_review', 'pending')
              AND merchant_key IN ({placeholders})
            ORDER BY date DESC, id DESC
            """,
            split_keys,
        ).fetchall()
        for row in split_rows:
            item = dict(row)
            item["review_mode"] = "transaction"
            item["transaction_count"] = 1
            items.append(item)

    return items


def list_merchant_transactions(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    review_only: bool = False,
) -> list[dict[str, Any]]:
    status_filter = (
        " AND label_status IN ('needs_review', 'pending')" if review_only else ""
    )
    rows = conn.execute(
        f"""
        SELECT date,
               budget_month,
               amount,
               source_category,
               account_name,
               user_description,
               simple_description,
               original_description,
               classification,
               flow_type,
               ai_category,
               ai_sub_category,
               expense_type,
               label_status,
               source_file
        FROM transactions
        WHERE merchant_key = ?{status_filter}
        ORDER BY date DESC, id DESC
        """,
        (merchant_key,),
    ).fetchall()
    return [dict(r) for r in rows]
