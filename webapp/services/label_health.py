from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# Conservative defaults — extend via mapping JSON before apply.
SUGGESTED_CATEGORY_MERGES: dict[str, str] = {
    "Restaurants/Dining": "Dining",
    "Food & Dining": "Dining",
    "Restaurants": "Dining",
    "Healthcare/Medical": "Healthcare",
    "Healthcare": "Healthcare",
}

MULTI_CATEGORY_SKIP_DEFAULTS = frozenset(
    {
        "Check Payment",
        "Amazon Marketplace",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_merchant_key(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> float:
    na, nb = _normalize_merchant_key(a), _normalize_merchant_key(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def load_mapping(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Mapping file must be a JSON object.")
    return data


def report_drift(conn: sqlite3.Connection) -> dict[str, Any]:
    summary = conn.execute(
        """
        SELECT
            COUNT(*) AS drift_rows,
            COUNT(DISTINCT t.merchant_key) AS drift_merchants,
            ROUND(SUM(CASE WHEN t.flow_type = 'Expense' AND t.amount < 0 THEN -t.amount ELSE 0 END), 2)
                AS drift_expense_spend
        FROM transactions t
        JOIN merchant_labels ml ON ml.merchant_key = t.merchant_key
        WHERE COALESCE(t.ai_category, '') != COALESCE(ml.ai_category, '')
           OR COALESCE(t.ai_sub_category, '') != COALESCE(ml.ai_sub_category, '')
        """
    ).fetchone()

    total_expense = conn.execute(
        """
        SELECT ROUND(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 2)
        FROM transactions WHERE flow_type = 'Expense'
        """
    ).fetchone()[0] or 0.0

    rows = conn.execute(
        """
        SELECT
            t.merchant_key,
            ml.ai_category AS label_category,
            ml.ai_sub_category AS label_sub_category,
            ml.label_status,
            t.ai_category AS tx_category,
            t.ai_sub_category AS tx_sub_category,
            COUNT(*) AS tx_count,
            ROUND(SUM(CASE WHEN t.flow_type = 'Expense' AND t.amount < 0 THEN -t.amount ELSE 0 END), 2)
                AS expense_spend
        FROM transactions t
        JOIN merchant_labels ml ON ml.merchant_key = t.merchant_key
        WHERE COALESCE(t.ai_category, '') != COALESCE(ml.ai_category, '')
           OR COALESCE(t.ai_sub_category, '') != COALESCE(ml.ai_sub_category, '')
        GROUP BY 1, 2, 3, 4, 5, 6
        ORDER BY expense_spend DESC, tx_count DESC
        """
    ).fetchall()

    return {
        "summary": {
            "drift_rows": int(summary["drift_rows"] or 0),
            "drift_merchants": int(summary["drift_merchants"] or 0),
            "drift_expense_spend": float(summary["drift_expense_spend"] or 0),
            "total_expense_spend": float(total_expense),
            "drift_expense_pct": round(
                100.0 * float(summary["drift_expense_spend"] or 0) / total_expense, 2
            )
            if total_expense
            else 0.0,
        },
        "rows": [_row_dict(r) for r in rows],
    }


def report_taxonomy(conn: sqlite3.Connection) -> dict[str, Any]:
    categories = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT ai_category FROM (
                SELECT ai_category FROM transactions
                UNION SELECT ai_category FROM merchant_labels
            ) WHERE ai_category IS NOT NULL AND TRIM(ai_category) != ''
            ORDER BY 1
            """
        ).fetchall()
    ]

    pairs = conn.execute(
        """
        SELECT ai_category, ai_sub_category, COUNT(*) AS tx_count,
               ROUND(SUM(CASE WHEN flow_type = 'Expense' AND amount < 0 THEN -amount ELSE 0 END), 2)
                   AS expense_spend
        FROM transactions
        WHERE ai_category IS NOT NULL AND TRIM(ai_category) != ''
        GROUP BY 1, 2
        ORDER BY expense_spend DESC, tx_count DESC
        """
    ).fetchall()

    pair_rows = [_row_dict(r) for r in pairs]

    # Sub-categories reused under multiple categories (likely taxonomy noise).
    sub_multi: dict[str, list[str]] = defaultdict(list)
    for row in pair_rows:
        sub = str(row.get("ai_sub_category") or "").strip()
        cat = str(row.get("ai_category") or "").strip()
        if sub and cat and cat not in sub_multi[sub]:
            sub_multi[sub].append(cat)
    ambiguous_subs = [
        {"sub_category": sub, "categories": cats}
        for sub, cats in sorted(sub_multi.items())
        if len(cats) > 1
    ]

    return {
        "category_count": len(categories),
        "categories": categories,
        "pair_count": len(pair_rows),
        "pairs": pair_rows,
        "ambiguous_sub_categories": ambiguous_subs,
        "suggested_category_merges": dict(SUGGESTED_CATEGORY_MERGES),
    }


def report_merchant_inconsistency(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT merchant_key,
               COUNT(DISTINCT ai_category || '|' || COALESCE(ai_sub_category, '')) AS combo_count,
               COUNT(*) AS tx_count
        FROM transactions
        GROUP BY merchant_key
        HAVING combo_count > 1
        ORDER BY tx_count DESC
        """
    ).fetchall()
    return {
        "multi_combo_merchant_count": len(rows),
        "merchants": [_row_dict(r) for r in rows],
    }


def detect_alias_candidates(
    conn: sqlite3.Connection,
    *,
    min_similarity: float = 0.88,
) -> list[dict[str, Any]]:
    merchants = [
        r[0]
        for r in conn.execute(
            """
            SELECT merchant_key FROM merchant_labels
            UNION
            SELECT DISTINCT merchant_key FROM transactions
            ORDER BY 1
            """
        ).fetchall()
    ]

    buckets: dict[str, list[str]] = defaultdict(list)
    for mk in merchants:
        norm = _normalize_merchant_key(mk)
        token = norm.split()[0] if norm else norm
        buckets[token].append(mk)

    groups: list[list[str]] = []
    used: set[str] = set()

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        bucket_sorted = sorted(bucket, key=len)
        for i, a in enumerate(bucket_sorted):
            if a in used:
                continue
            group = [a]
            for b in bucket_sorted[i + 1 :]:
                if b in used:
                    continue
                if _similarity(a, b) >= min_similarity:
                    group.append(b)
            if len(group) > 1:
                for m in group:
                    used.add(m)
                groups.append(group)

    results: list[dict[str, Any]] = []
    for group in groups:
        tx_counts = {}
        spend = {}
        for mk in group:
            row = conn.execute(
                """
                SELECT COUNT(*) n,
                       ROUND(SUM(CASE WHEN flow_type='Expense' AND amount<0 THEN -amount ELSE 0 END), 2) s
                FROM transactions WHERE merchant_key = ?
                """,
                (mk,),
            ).fetchone()
            tx_counts[mk] = int(row["n"] or 0)
            spend[mk] = float(row["s"] or 0)

        canonical = max(group, key=lambda m: (tx_counts.get(m, 0), spend.get(m, 0), -len(m)))
        labels = {
            mk: _row_dict(
                conn.execute(
                    "SELECT ai_category, ai_sub_category, label_status FROM merchant_labels WHERE merchant_key=?",
                    (mk,),
                ).fetchone()
            )
            for mk in group
        }
        results.append(
            {
                "canonical_suggestion": canonical,
                "members": group,
                "tx_counts": tx_counts,
                "expense_spend": spend,
                "merchant_labels": labels,
                "aliases": {mk: canonical for mk in group if mk != canonical},
            }
        )
    return results


def build_health_report(conn: sqlite3.Connection) -> dict[str, Any]:
    drift = report_drift(conn)
    taxonomy = report_taxonomy(conn)
    multi = report_merchant_inconsistency(conn)
    aliases = detect_alias_candidates(conn)

    suggested_mapping: dict[str, Any] = {
        "version": 1,
        "category_merges": dict(SUGGESTED_CATEGORY_MERGES),
        "sub_category_merges": {},
        "merchant_aliases": {},
        "reconcile": {
            "enabled": True,
            "only_confirmed": True,
            "skip_merchants": sorted(MULTI_CATEGORY_SKIP_DEFAULTS),
        },
    }
    for group in aliases:
        suggested_mapping["merchant_aliases"].update(group["aliases"])

    return {
        "generated_at": _utc_now(),
        "drift": drift,
        "taxonomy": taxonomy,
        "multi_combo_merchants": multi,
        "alias_candidates": aliases,
        "suggested_mapping": suggested_mapping,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def export_health_report(conn: sqlite3.Connection, output_dir: Path) -> dict[str, str]:
    report = build_health_report(conn)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    paths["report"] = str(report_path)

    drift_rows = report["drift"]["rows"]
    drift_csv = output_dir / "drift.csv"
    _write_csv(
        drift_csv,
        drift_rows,
        [
            "merchant_key",
            "label_category",
            "label_sub_category",
            "label_status",
            "tx_category",
            "tx_sub_category",
            "tx_count",
            "expense_spend",
        ],
    )
    paths["drift"] = str(drift_csv)

    pairs_csv = output_dir / "taxonomy_pairs.csv"
    _write_csv(
        pairs_csv,
        report["taxonomy"]["pairs"],
        ["ai_category", "ai_sub_category", "tx_count", "expense_spend"],
    )
    paths["taxonomy_pairs"] = str(pairs_csv)

    alias_rows = []
    for group in report["alias_candidates"]:
        for mk in group["members"]:
            alias_rows.append(
                {
                    "merchant_key": mk,
                    "canonical_suggestion": group["canonical_suggestion"],
                    "tx_count": group["tx_counts"].get(mk, 0),
                    "expense_spend": group["expense_spend"].get(mk, 0),
                    "ai_category": (group["merchant_labels"].get(mk) or {}).get("ai_category", ""),
                    "ai_sub_category": (group["merchant_labels"].get(mk) or {}).get(
                        "ai_sub_category", ""
                    ),
                }
            )
    alias_csv = output_dir / "alias_candidates.csv"
    _write_csv(
        alias_csv,
        alias_rows,
        [
            "merchant_key",
            "canonical_suggestion",
            "tx_count",
            "expense_spend",
            "ai_category",
            "ai_sub_category",
        ],
    )
    paths["alias_candidates"] = str(alias_csv)

    mapping_path = output_dir / "suggested_mapping.json"
    mapping_path.write_text(
        json.dumps(report["suggested_mapping"], indent=2),
        encoding="utf-8",
    )
    paths["suggested_mapping"] = str(mapping_path)

    return paths


def _resolve_category(category: str, merges: dict[str, str]) -> str:
    text = (category or "").strip()
    while text in merges:
        text = merges[text]
    return text


def preview_mapping(conn: sqlite3.Connection, mapping: dict[str, Any]) -> dict[str, Any]:
    return apply_mapping(conn, mapping, dry_run=True)


def apply_mapping(
    conn: sqlite3.Connection,
    mapping: dict[str, Any],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    category_merges: dict[str, str] = dict(mapping.get("category_merges") or {})
    sub_merges: dict[str, dict[str, str]] = dict(mapping.get("sub_category_merges") or {})
    merchant_aliases: dict[str, str] = dict(mapping.get("merchant_aliases") or {})
    reconcile_cfg = mapping.get("reconcile") or {}
    reconcile_enabled = bool(reconcile_cfg.get("enabled", False))
    only_confirmed = bool(reconcile_cfg.get("only_confirmed", True))
    skip_merchants = set(reconcile_cfg.get("skip_merchants") or MULTI_CATEGORY_SKIP_DEFAULTS)

    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "category_merges": 0,
        "sub_category_merges": 0,
        "merchant_alias_transactions": 0,
        "merchant_labels_deleted": 0,
        "merchant_labels_renamed": 0,
        "cadence_rules_updated": 0,
        "reconcile_transactions": 0,
        "warnings": [],
    }

    # Validate alias chains point to existing canonical keys.
    for alias, canonical in list(merchant_aliases.items()):
        if alias == canonical:
            merchant_aliases.pop(alias, None)
            continue
        while canonical in merchant_aliases and merchant_aliases[canonical] != canonical:
            canonical = merchant_aliases[canonical]
        merchant_aliases[alias] = canonical

    if not dry_run:
        conn.execute("BEGIN")

    try:
        for old_cat, new_cat in category_merges.items():
            old_cat = old_cat.strip()
            new_cat = new_cat.strip()
            if not old_cat or not new_cat or old_cat == new_cat:
                continue
            for table in ("transactions", "merchant_labels"):
                cur = conn.execute(
                    f"UPDATE {table} SET ai_category = ? WHERE ai_category = ?",
                    (new_cat, old_cat),
                )
                stats["category_merges"] += cur.rowcount

        for cat, merges in sub_merges.items():
            cat = cat.strip()
            for old_sub, new_sub in (merges or {}).items():
                old_sub = (old_sub or "").strip()
                new_sub = (new_sub or "").strip()
                if not old_sub or not new_sub or old_sub == new_sub:
                    continue
                for table in ("transactions", "merchant_labels"):
                    cur = conn.execute(
                        f"""
                        UPDATE {table}
                        SET ai_sub_category = ?
                        WHERE ai_category = ? AND COALESCE(ai_sub_category, '') = ?
                        """,
                        (new_sub, cat, old_sub),
                    )
                    stats["sub_category_merges"] += cur.rowcount

        for alias, canonical in merchant_aliases.items():
            if alias == canonical:
                continue
            tx_cur = conn.execute(
                "UPDATE transactions SET merchant_key = ? WHERE merchant_key = ?",
                (canonical, alias),
            )
            stats["merchant_alias_transactions"] += tx_cur.rowcount

            cad_row = conn.execute(
                "SELECT rule_id FROM cadence_rules WHERE merchant_key = ?", (alias,)
            ).fetchone()
            canon_cad = conn.execute(
                "SELECT rule_id FROM cadence_rules WHERE merchant_key = ?", (canonical,)
            ).fetchone()
            if cad_row and canon_cad:
                if not dry_run:
                    conn.execute("DELETE FROM cadence_rules WHERE merchant_key = ?", (alias,))
                stats["warnings"].append(
                    f"Dropped cadence rule for alias merchant {alias!r} — canonical {canonical!r} already has one."
                )
            elif cad_row:
                cad_cur = conn.execute(
                    "UPDATE cadence_rules SET merchant_key = ? WHERE merchant_key = ?",
                    (canonical, alias),
                )
                stats["cadence_rules_updated"] += cad_cur.rowcount

            alias_label = conn.execute(
                "SELECT merchant_key FROM merchant_labels WHERE merchant_key = ?",
                (alias,),
            ).fetchone()
            canonical_label = conn.execute(
                "SELECT merchant_key FROM merchant_labels WHERE merchant_key = ?",
                (canonical,),
            ).fetchone()

            if alias_label and canonical_label:
                alias_row = _row_dict(
                    conn.execute(
                        "SELECT ai_category, ai_sub_category FROM merchant_labels WHERE merchant_key=?",
                        (alias,),
                    ).fetchone()
                )
                canon_row = _row_dict(
                    conn.execute(
                        "SELECT ai_category, ai_sub_category FROM merchant_labels WHERE merchant_key=?",
                        (canonical,),
                    ).fetchone()
                )
                if alias_row and canon_row and (
                    alias_row.get("ai_category") != canon_row.get("ai_category")
                    or alias_row.get("ai_sub_category") != canon_row.get("ai_sub_category")
                ):
                    stats["warnings"].append(
                        f"Deleted merchant_labels for alias {alias!r} — label differed from canonical {canonical!r}."
                    )
                if not dry_run:
                    conn.execute("DELETE FROM merchant_labels WHERE merchant_key = ?", (alias,))
                stats["merchant_labels_deleted"] += 1
            elif alias_label and not canonical_label:
                if not dry_run:
                    conn.execute(
                        "UPDATE merchant_labels SET merchant_key = ? WHERE merchant_key = ?",
                        (canonical, alias),
                    )
                stats["merchant_labels_renamed"] += 1

        if reconcile_enabled:
            skip_list = sorted(skip_merchants)
            where_parts = [
                """
                EXISTS (
                    SELECT 1 FROM merchant_labels ml
                    WHERE ml.merchant_key = transactions.merchant_key
                )
                """,
                """
                (
                    COALESCE(transactions.ai_category, '') != COALESCE(
                        (SELECT ai_category FROM merchant_labels ml WHERE ml.merchant_key = transactions.merchant_key),
                        ''
                    )
                    OR COALESCE(transactions.ai_sub_category, '') != COALESCE(
                        (SELECT ai_sub_category FROM merchant_labels ml WHERE ml.merchant_key = transactions.merchant_key),
                        ''
                    )
                )
                """,
            ]
            params: list[Any] = []
            if only_confirmed:
                where_parts.append(
                    """
                    (SELECT label_status FROM merchant_labels ml WHERE ml.merchant_key = transactions.merchant_key)
                        = 'confirmed'
                    """
                )
            if skip_list:
                where_parts.append(
                    f"transactions.merchant_key NOT IN ({','.join('?' * len(skip_list))})"
                )
                params.extend(skip_list)

            where_sql = " AND ".join(f"({p.strip()})" for p in where_parts)
            sql = f"""
                UPDATE transactions
                SET
                    ai_category = (
                        SELECT ai_category FROM merchant_labels ml
                        WHERE ml.merchant_key = transactions.merchant_key
                    ),
                    ai_sub_category = (
                        SELECT ai_sub_category FROM merchant_labels ml
                        WHERE ml.merchant_key = transactions.merchant_key
                    ),
                    expense_type = (
                        SELECT expense_type FROM merchant_labels ml
                        WHERE ml.merchant_key = transactions.merchant_key
                    ),
                    label_status = (
                        SELECT label_status FROM merchant_labels ml
                        WHERE ml.merchant_key = transactions.merchant_key
                    ),
                    confidence = (
                        SELECT confidence FROM merchant_labels ml
                        WHERE ml.merchant_key = transactions.merchant_key
                    ),
                    rationale = COALESCE(
                        transactions.rationale,
                        (
                            SELECT rationale FROM merchant_labels ml
                            WHERE ml.merchant_key = transactions.merchant_key
                        )
                    )
                WHERE {where_sql}
            """
            cur = conn.execute(sql, params)
            stats["reconcile_transactions"] = cur.rowcount

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    return stats


def _compact_alias_groups(groups: list[dict[str, Any]], *, limit: int = 50) -> list[dict[str, Any]]:
    ranked = sorted(
        groups,
        key=lambda g: sum((g.get("tx_counts") or {}).values()),
        reverse=True,
    )
    compact = []
    for g in ranked[:limit]:
        compact.append(
            {
                "canonical_suggestion": g.get("canonical_suggestion"),
                "members": g.get("members"),
                "tx_counts": g.get("tx_counts"),
                "merchant_labels": g.get("merchant_labels"),
            }
        )
    return compact


def suggest_mapping_with_llm(
    conn: sqlite3.Connection,
    *,
    model: str | None = None,
    alias_group_limit: int = 50,
) -> dict[str, Any]:
    """
    Use the configured chat LLM to propose a label mapping JSON.
    Always review output and run apply_label_mapping.py --dry-run before --apply.
    """
    from webapp.services.llm import chat_completion, extract_json

    report = build_health_report(conn)
    taxonomy = report["taxonomy"]

    # High-spend category pairs for merge context.
    top_pairs = (taxonomy.get("pairs") or [])[:40]
    ambiguous = (taxonomy.get("ambiguous_sub_categories") or [])[:25]

    payload = {
        "drift_summary": report["drift"]["summary"],
        "categories": taxonomy.get("categories") or [],
        "top_category_subcategory_pairs": top_pairs,
        "ambiguous_sub_categories": ambiguous,
        "alias_groups": _compact_alias_groups(
            report.get("alias_candidates") or [], limit=alias_group_limit
        ),
        "multi_combo_merchants": (report.get("multi_combo_merchants") or {}).get("merchants", [])[
            :20
        ],
    }

    system = """You are a personal finance data taxonomist. Produce a JSON label mapping for SQLite cleanup.

Rules — merchant_aliases:
- Merge ONLY the same real-world merchant with typos, truncation, or location suffix (e.g. "7 Leaves Irvine" -> "7 Leaves Cafe").
- Do NOT merge different check numbers ("Check 00000000211" vs "Check 00000000214").
- Do NOT merge bank transfers =In vs =Out, To vs From, different account numbers (CHK 8138 vs 0334), or different confirmation IDs.
- Do NOT merge different CAPITAL ONE / payment processor IDs unless clearly the same payee with OCR truncation.
- Do NOT merge ATM withdrawals at different branch locations unless user would treat them as one merchant.
- Pick canonical = member with highest tx_count; prefer cleaner full name over truncated.

Rules — category_merges:
- Merge synonyms only: Restaurants/Dining, Food & Dining -> Dining; Healthcare/Medical -> Healthcare.
- Do NOT merge unrelated categories.

Rules — sub_category_merges:
- Scoped by category key. Merge spelling variants only (Grocery shopping -> Supermarket under Groceries).
- Do NOT map Fast food -> Coffee globally.
- Do NOT move sub-categories across categories.

Always include reconcile block:
{"enabled": true, "only_confirmed": true, "skip_merchants": ["Check Payment", "Amazon Marketplace"]}

Respond with ONLY valid JSON matching this schema:
{
  "version": 1,
  "category_merges": {"Old": "New"},
  "sub_category_merges": {"Category": {"OldSub": "NewSub"}},
  "merchant_aliases": {"alias_key": "canonical_key"},
  "reconcile": {"enabled": true, "only_confirmed": true, "skip_merchants": []}
}"""

    user = (
        "Using the data below, produce a conservative mapping JSON. "
        "Prefer fewer, high-confidence merges over aggressive deduplication.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    raw = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.1,
        model=model,
    )
    mapping = extract_json(raw)
    if not isinstance(mapping, dict):
        raise ValueError("LLM did not return a JSON object.")
    mapping.setdefault("version", 1)
    mapping.setdefault("category_merges", {})
    mapping.setdefault("sub_category_merges", {})
    mapping.setdefault("merchant_aliases", {})
    mapping.setdefault(
        "reconcile",
        {
            "enabled": True,
            "only_confirmed": True,
            "skip_merchants": sorted(MULTI_CATEGORY_SKIP_DEFAULTS),
        },
    )
    return mapping
