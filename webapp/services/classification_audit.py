"""Classification quality audit — heuristics + sampled LLM re-check with a stronger model."""

from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from openai import OpenAI

from webapp.db.schema import get_connection, init_db

RunType = Literal["post_import", "scheduled", "manual"]

_ROOT = Path(__file__).resolve().parents[2]


def _env_bool(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on")


def _db_path() -> Path:
    return Path(os.getenv("FINANCE_DB_PATH", str(_ROOT / "data" / "finance.db")))


CLASSIFICATION_AUDIT_ENABLED = _env_bool("CLASSIFICATION_AUDIT_ENABLED", default=True)
CLASSIFICATION_AUDIT_POST_IMPORT_MERCHANTS = int(
    os.getenv("CLASSIFICATION_AUDIT_POST_IMPORT_MERCHANTS", "8")
)
CLASSIFICATION_AUDIT_SCHEDULED_MERCHANTS = int(
    os.getenv("CLASSIFICATION_AUDIT_SCHEDULED_MERCHANTS", "30")
)
CLASSIFICATION_AUDIT_MIN_CONFIDENCE = float(
    os.getenv("CLASSIFICATION_AUDIT_MIN_CONFIDENCE", "0.85")
)


def vocabulary_hint_enabled() -> bool:
    from webapp.services.classification_vocabulary import vocabulary_hint_enabled as _enabled

    return _enabled()

_FOOD_CATEGORY_HINTS = ("food", "dining", "restaurant", "grocery", "groceries")
_NON_FOOD_SUB_HINTS = (
    "home",
    "insurance",
    "tax",
    "warranty",
    "utility",
    "utilities",
    "electric",
    "repair",
    "maintenance",
    "mortgage",
    "rent",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_label(value: str | None) -> str:
    return (value or "").strip().casefold()


def labels_match(
    prod_cat: str,
    prod_sub: str,
    sugg_cat: str,
    sugg_sub: str,
) -> bool:
    return normalize_label(prod_cat) == normalize_label(sugg_cat) and normalize_label(
        prod_sub
    ) == normalize_label(sugg_sub)


def heuristic_finding(
    *,
    merchant_key: str,
    category: str,
    sub_category: str,
) -> dict[str, Any] | None:
    """Return a suggested fix when production labels are obviously inconsistent."""
    cat_n = normalize_label(category)
    sub_n = normalize_label(sub_category)
    mk_n = normalize_label(merchant_key)

    if sub_n and sub_n == mk_n:
        return {
            "suggested_category": category or "Review",
            "suggested_sub": "",
            "rationale": "Sub-category duplicates merchant name; needs a semantic spend type.",
        }

    if cat_n and sub_n:
        cat_is_food = any(h in cat_n for h in _FOOD_CATEGORY_HINTS)
        sub_non_food = any(h in sub_n for h in _NON_FOOD_SUB_HINTS)
        if cat_is_food and sub_non_food:
            suggested_cat = "Home"
            if "insurance" in sub_n or "warranty" in sub_n:
                suggested_cat = "Insurance"
            elif "tax" in sub_n:
                suggested_cat = "Taxes"
            elif "utility" in sub_n or "electric" in sub_n:
                suggested_cat = "Utilities"
            return {
                "suggested_category": suggested_cat,
                "suggested_sub": sub_category.strip() or "Home services",
                "rationale": (
                    f"Category {category!r} conflicts with sub-category {sub_category!r} "
                    "(non-food spend type under a food/dining category)."
                ),
            }

    return None


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _merchant_confirmed_labels(conn: sqlite3.Connection, merchant_key: str) -> dict[str, str] | None:
    row = conn.execute(
        """
        SELECT ai_category, ai_sub_category, label_status
        FROM merchant_labels
        WHERE merchant_key = ?
        """,
        (merchant_key,),
    ).fetchone()
    if row is None or str(row["label_status"] or "").strip().lower() != "confirmed":
        return None
    return {
        "ai_category": str(row["ai_category"] or "").strip(),
        "ai_sub_category": str(row["ai_sub_category"] or "").strip(),
    }


def _merchant_tx_labels_consistent(conn: sqlite3.Connection, merchant_key: str) -> bool:
    rows = conn.execute(
        """
        SELECT DISTINCT COALESCE(ai_category, '') AS c, COALESCE(ai_sub_category, '') AS s
        FROM transactions
        WHERE merchant_key = ?
          AND flow_type = 'Expense'
          AND amount < 0
        """,
        (merchant_key,),
    ).fetchall()
    return len(rows) <= 1


def _should_skip_llm_audit(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    production_category: str,
    production_sub: str,
) -> bool:
    confirmed = _merchant_confirmed_labels(conn, merchant_key)
    if not confirmed:
        return False
    if not _merchant_tx_labels_consistent(conn, merchant_key):
        return False
    if labels_match(
        production_category,
        production_sub,
        confirmed["ai_category"],
        confirmed["ai_sub_category"],
    ):
        return True
    return False


def _representative_transaction(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    source_file: str | None = None,
) -> sqlite3.Row | None:
    if source_file:
        row = conn.execute(
            """
            SELECT *
            FROM transactions
            WHERE merchant_key = ?
              AND source_file = ?
              AND flow_type = 'Expense'
              AND amount < 0
            ORDER BY ABS(amount) DESC, date DESC
            LIMIT 1
            """,
            (merchant_key, source_file),
        ).fetchone()
        if row is not None:
            return row
    return conn.execute(
        """
        SELECT *
        FROM transactions
        WHERE merchant_key = ?
          AND flow_type = 'Expense'
          AND amount < 0
        ORDER BY ABS(amount) DESC, date DESC
        LIMIT 1
        """,
        (merchant_key,),
    ).fetchone()


def _tx_to_series(row: sqlite3.Row) -> pd.Series:
    return pd.Series(
        {
            "Transaction Date": row["date"],
            "Amount_Numeric": row["amount"],
            "Amount": str(row["amount"]),
            "Category": row["source_category"] or "",
            "Generated Description": row["merchant_key"] or "",
            "Account Name": row["account_name"] or "",
        }
    )


def _build_classification_payload(row: pd.Series, index: int) -> dict[str, Any]:
    amount = row.get("Amount_Numeric", row.get("amount", 0))
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        amount_value = 0.0
    return {
        "index": index,
        "date": str(row.get("Transaction Date", "") or row.get("date", "") or ""),
        "amount": amount_value,
        "original_category": str(
            row.get("Category", "") or row.get("source_category", "") or ""
        ),
        "generated_description": str(
            row.get("Generated Description", "") or row.get("merchant_key", "") or ""
        ),
        "account": str(row.get("Account Name", "") or row.get("account_name", "") or ""),
    }


def _extract_json_payload(text: str) -> dict[str, Any] | list[Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def sample_merchants_post_import(
    conn: sqlite3.Connection,
    source_files: list[str],
    *,
    limit: int,
) -> list[str]:
    if limit <= 0 or not source_files:
        return []
    placeholders = ",".join("?" * len(source_files))
    rows = conn.execute(
        f"""
        SELECT DISTINCT merchant_key
        FROM transactions
        WHERE source_file IN ({placeholders})
          AND flow_type = 'Expense'
          AND amount < 0
          AND COALESCE(merchant_key, '') != ''
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (*source_files, limit * 3),
    ).fetchall()
    picked: list[str] = []
    for row in rows:
        mk = str(row["merchant_key"]).strip()
        if not mk or mk in picked:
            continue
        rep = _representative_transaction(conn, mk, source_file=source_files[0])
        if rep is None:
            continue
        prod_cat = str(rep["ai_category"] or "").strip()
        prod_sub = str(rep["ai_sub_category"] or "").strip()
        if _should_skip_llm_audit(conn, mk, production_category=prod_cat, production_sub=prod_sub):
            if heuristic_finding(merchant_key=mk, category=prod_cat, sub_category=prod_sub) is None:
                continue
        picked.append(mk)
        if len(picked) >= limit:
            break
    return picked


def sample_merchants_scheduled(
    conn: sqlite3.Connection,
    *,
    limit: int,
) -> list[str]:
    if limit <= 0:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_audited = {
        str(r["merchant_key"])
        for r in conn.execute(
            """
            SELECT DISTINCT f.merchant_key
            FROM classification_audit_findings f
            JOIN classification_audit_runs r ON r.id = f.run_id
            WHERE f.created_at >= ?
            """,
            (cutoff,),
        ).fetchall()
    }

    spend_rows = conn.execute(
        """
        SELECT merchant_key,
               SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END) AS spend
        FROM transactions
        WHERE flow_type = 'Expense'
          AND amount < 0
          AND COALESCE(merchant_key, '') != ''
        GROUP BY merchant_key
        ORDER BY spend DESC
        """,
    ).fetchall()
    if not spend_rows:
        return []

    by_spend = [str(r["merchant_key"]) for r in spend_rows]
    top_n = max(1, limit // 2)
    candidates: list[str] = []
    for mk in by_spend:
        if mk in recent_audited:
            continue
        candidates.append(mk)
        if len(candidates) >= top_n:
            break

    pool = [mk for mk in by_spend if mk not in candidates and mk not in recent_audited]
    random.shuffle(pool)
    for mk in pool:
        candidates.append(mk)
        if len(candidates) >= limit:
            break

    if len(candidates) < limit:
        for mk in by_spend:
            if mk not in candidates:
                candidates.append(mk)
            if len(candidates) >= limit:
                break
    return candidates[:limit]


def scan_heuristic_findings(
    conn: sqlite3.Connection,
    merchant_keys: list[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    inconsistent = conn.execute(
        """
        SELECT merchant_key, COUNT(DISTINCT COALESCE(ai_category, '')) AS cat_n
        FROM transactions
        WHERE flow_type = 'Expense' AND amount < 0
        GROUP BY merchant_key
        HAVING cat_n > 1
        """
    ).fetchall()
    inconsistent_set = {str(r["merchant_key"]) for r in inconsistent}

    keys = set(merchant_keys) | inconsistent_set
    for mk in keys:
        rep = _representative_transaction(conn, mk)
        if rep is None:
            continue
        prod_cat = str(rep["ai_category"] or "").strip()
        prod_sub = str(rep["ai_sub_category"] or "").strip()
        hint = heuristic_finding(merchant_key=mk, category=prod_cat, sub_category=prod_sub)
        if hint and not labels_match(
            prod_cat, prod_sub, hint["suggested_category"], hint["suggested_sub"]
        ):
            findings.append(
                {
                    "merchant_key": mk,
                    "transaction_id": rep["transaction_id"],
                    "source": "heuristic",
                    "production_category": prod_cat,
                    "production_sub": prod_sub,
                    "suggested_category": hint["suggested_category"],
                    "suggested_sub": hint["suggested_sub"],
                    "confidence": 1.0,
                    "rationale": hint["rationale"],
                }
            )
        elif mk in inconsistent_set:
            findings.append(
                {
                    "merchant_key": mk,
                    "transaction_id": rep["transaction_id"],
                    "source": "heuristic",
                    "production_category": prod_cat,
                    "production_sub": prod_sub,
                    "suggested_category": "",
                    "suggested_sub": "",
                    "confidence": 1.0,
                    "rationale": "Same merchant has multiple distinct AI categories in the database.",
                }
            )
    return findings


def audit_classify_single(
    client: OpenAI,
    payload: dict[str, Any],
    model: str,
    *,
    use_json_mode: bool,
    vocabulary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from webapp.services.classification_vocabulary import format_vocabulary_prompt_block
    from webapp.llm.client import PIPELINE_LLM_TEMPERATURE, json_for_prompt
    from webapp.llm.prompts import CLASSIFICATION_AUDIT_PROMPT

    vocab_block = format_vocabulary_prompt_block(vocabulary)
    user_content = json_for_prompt([payload])
    user_message = (
        "Audit this transaction classification. Respond with a single JSON object "
        '{"results": [ ... ]} where each item has index, category, sub_category, '
        "type, budget_tier, confidence, rationale. No markdown.\n\n"
    )
    if vocab_block:
        user_message += f"{vocab_block}\n\n"
    user_message += f"Transaction:\n{user_content}"
    messages = [
        {"role": "system", "content": CLASSIFICATION_AUDIT_PROMPT},
        {"role": "user", "content": user_message},
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": PIPELINE_LLM_TEMPERATURE,
        "messages": messages,
    }
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    from webapp.llm.request_log import logged_chat_completions_create

    response = logged_chat_completions_create(
        client, caller="classification_audit.llm", **kwargs
    )
    raw = response.choices[0].message.content or "{}"
    parsed = _extract_json_payload(raw)
    results = parsed.get("results", parsed if isinstance(parsed, list) else [])
    if not isinstance(results, list) or not results:
        raise ValueError(f"Unexpected audit response: {raw[:500]}")
    item = results[0]
    sub = str(item.get("sub_category", "") or "").strip()
    mk = str(payload.get("generated_description", "") or "").strip()
    if sub.lower() == mk.lower():
        sub = ""
    return {
        "category": str(item.get("category", "") or "").strip(),
        "sub_category": sub,
        "type": str(item.get("type", "Variable") or "Variable").strip(),
        "budget_tier": str(item.get("budget_tier", "Review") or "Review").strip(),
        "confidence": float(item.get("confidence", 0) or 0),
        "rationale": str(item.get("rationale", "") or "").strip(),
    }


def _use_json_mode(provider: str) -> bool:
    return provider in ("openai", "lmstudio", "ollama")


def _resolve_audit_client() -> tuple[OpenAI, str, str]:
    from webapp.llm.client import create_client, resolve_provider_config

    audit_model_env = os.getenv("CLASSIFICATION_AUDIT_MODEL", "").strip()
    if audit_model_env:
        provider, base_url, model = resolve_provider_config(
            "auto",
            base_url_arg=None,
            model_arg=audit_model_env,
            role="classification_audit",
        )
    else:
        provider, base_url, model = resolve_provider_config(
            "auto",
            base_url_arg=None,
            model_arg=None,
            role="pipeline",
        )
    client = create_client(provider, base_url=base_url)
    return client, model, provider


def _start_run(
    conn: sqlite3.Connection,
    *,
    run_type: RunType,
    sample_size: int,
    model: str,
) -> tuple[int, int]:
    cur = conn.execute(
        """
        INSERT INTO classification_audit_runs (
            run_type, status, sample_size, model, started_at
        ) VALUES (?, 'running', ?, ?, ?)
        """,
        (run_type, sample_size, model, _utc_now()),
    )
    run_id = int(cur.lastrowid)
    agent_cur = conn.execute(
        """
        INSERT INTO agent_runs (run_type, status, detail, started_at)
        VALUES ('classification_audit', 'running', ?, ?)
        """,
        (
            json.dumps({"audit_run_id": run_id, "audit_run_type": run_type}),
            _utc_now(),
        ),
    )
    conn.commit()
    return run_id, int(agent_cur.lastrowid)


def _finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    agent_run_id: int,
    *,
    status: str,
    summary: dict[str, Any],
) -> None:
    finished = _utc_now()
    conn.execute(
        """
        UPDATE classification_audit_runs
        SET status = ?, finished_at = ?, summary_json = ?
        WHERE id = ?
        """,
        (status, finished, json.dumps(summary), run_id),
    )
    conn.execute(
        """
        UPDATE agent_runs
        SET status = ?, finished_at = ?, detail = ?
        WHERE id = ?
        """,
        (status, finished, json.dumps(summary), agent_run_id),
    )
    conn.commit()


def _finding_exists_open(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    production_category: str,
    production_sub: str,
    suggested_category: str,
    suggested_sub: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM classification_audit_findings
        WHERE status = 'open'
          AND merchant_key = ?
          AND COALESCE(production_category, '') = ?
          AND COALESCE(production_sub, '') = ?
          AND COALESCE(suggested_category, '') = ?
          AND COALESCE(suggested_sub, '') = ?
        LIMIT 1
        """,
        (
            merchant_key,
            production_category,
            production_sub,
            suggested_category,
            suggested_sub,
        ),
    ).fetchone()
    return row is not None


def _insert_finding(conn: sqlite3.Connection, run_id: int, finding: dict[str, Any]) -> bool:
    if _finding_exists_open(
        conn,
        merchant_key=finding["merchant_key"],
        production_category=finding.get("production_category", ""),
        production_sub=finding.get("production_sub", ""),
        suggested_category=finding.get("suggested_category", ""),
        suggested_sub=finding.get("suggested_sub", ""),
    ):
        return False
    conn.execute(
        """
        INSERT INTO classification_audit_findings (
            run_id, merchant_key, transaction_id, source,
            production_category, production_sub,
            suggested_category, suggested_sub,
            confidence, rationale, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            run_id,
            finding["merchant_key"],
            finding.get("transaction_id"),
            finding["source"],
            finding.get("production_category", ""),
            finding.get("production_sub", ""),
            finding.get("suggested_category", ""),
            finding.get("suggested_sub", ""),
            float(finding.get("confidence", 0) or 0),
            finding.get("rationale", ""),
            _utc_now(),
        ),
    )
    return True


def run_classification_audit(
    conn: sqlite3.Connection,
    *,
    run_type: RunType,
    source_files: list[str] | None = None,
    sample_size: int | None = None,
) -> dict[str, Any]:
    if not CLASSIFICATION_AUDIT_ENABLED:
        return {"enabled": False, "skipped": True}

    if run_type == "post_import":
        limit = sample_size or CLASSIFICATION_AUDIT_POST_IMPORT_MERCHANTS
        merchant_keys = sample_merchants_post_import(
            conn, source_files or [], limit=limit
        )
    elif run_type == "scheduled":
        limit = sample_size or CLASSIFICATION_AUDIT_SCHEDULED_MERCHANTS
        merchant_keys = sample_merchants_scheduled(conn, limit=limit)
    else:
        limit = sample_size or CLASSIFICATION_AUDIT_SCHEDULED_MERCHANTS
        merchant_keys = sample_merchants_scheduled(conn, limit=limit)

    client, model, provider = _resolve_audit_client()
    run_id, agent_run_id = _start_run(
        conn, run_type=run_type, sample_size=len(merchant_keys), model=model
    )

    vocabulary: dict[str, Any] | None = None
    if vocabulary_hint_enabled():
        from webapp.services.classification_vocabulary import load_classification_vocabulary

        vocabulary = load_classification_vocabulary(conn)

    heuristic_findings = scan_heuristic_findings(conn, merchant_keys)
    inserted = 0
    for finding in heuristic_findings:
        if _insert_finding(conn, run_id, finding):
            inserted += 1
    conn.commit()

    llm_audited = 0
    llm_flagged = 0
    llm_errors = 0
    use_json = _use_json_mode(provider)

    for mk in merchant_keys:
        rep = _representative_transaction(
            conn,
            mk,
            source_file=(source_files[0] if source_files else None),
        )
        if rep is None:
            continue
        prod_cat = str(rep["ai_category"] or "").strip()
        prod_sub = str(rep["ai_sub_category"] or "").strip()
        if _should_skip_llm_audit(
            conn, mk, production_category=prod_cat, production_sub=prod_sub
        ):
            continue
        payload = _build_classification_payload(_tx_to_series(rep), 0)
        try:
            audit = audit_classify_single(
                client,
                payload,
                model,
                use_json_mode=use_json,
                vocabulary=vocabulary,
            )
            llm_audited += 1
        except Exception:
            llm_errors += 1
            continue

        from webapp.services.classification_vocabulary import (
            labels_equivalent,
            normalize_classify_labels,
        )

        sugg_cat, sugg_sub = normalize_classify_labels(
            audit["category"], audit["sub_category"], vocabulary
        )
        if labels_equivalent(prod_cat, prod_sub, sugg_cat, sugg_sub, vocabulary):
            continue
        if audit["confidence"] < CLASSIFICATION_AUDIT_MIN_CONFIDENCE:
            continue

        finding = {
            "merchant_key": mk,
            "transaction_id": rep["transaction_id"],
            "source": "llm_audit",
            "production_category": prod_cat,
            "production_sub": prod_sub,
            "suggested_category": sugg_cat,
            "suggested_sub": sugg_sub,
            "confidence": audit["confidence"],
            "rationale": audit["rationale"]
            or "Audit model disagrees with production labels.",
        }
        if _insert_finding(conn, run_id, finding):
            inserted += 1
            llm_flagged += 1
    conn.commit()

    summary = {
        "run_type": run_type,
        "merchants_sampled": len(merchant_keys),
        "heuristic_findings": len(heuristic_findings),
        "llm_audited": llm_audited,
        "llm_flagged": llm_flagged,
        "llm_errors": llm_errors,
        "findings_inserted": inserted,
        "model": model,
    }
    status = "completed" if llm_errors == 0 else "completed_with_errors"
    _finish_run(conn, run_id, agent_run_id, status=status, summary=summary)
    return {"run_id": run_id, "enabled": True, **summary}


def schedule_post_import_audit(source_files: list[str]) -> None:
    """Fire-and-forget post-import audit in a background thread."""
    import threading

    if not CLASSIFICATION_AUDIT_ENABLED or not source_files:
        return

    files = [f for f in source_files if f]

    def _worker() -> None:
        try:
            run_post_import_audit_for_files(_db_path(), files)
        except Exception as exc:
            print(f"Classification audit failed: {exc}", flush=True)

    threading.Thread(target=_worker, daemon=True).start()


def run_post_import_audit_for_files(
    db_path: Path,
    source_files: list[str],
) -> dict[str, Any]:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        return run_classification_audit(
            conn, run_type="post_import", source_files=source_files
        )
    finally:
        conn.close()


def run_scheduled_audit(db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or _db_path()
    init_db(path)
    conn = get_connection(path)
    try:
        return run_classification_audit(conn, run_type="scheduled")
    finally:
        conn.close()


def _current_labels_for_finding(
    conn: sqlite3.Connection,
    finding: dict[str, Any],
) -> tuple[str, str]:
    """Live category/sub on the sampled transaction, or the merchant's representative row."""
    tid = finding.get("transaction_id")
    if tid:
        row = conn.execute(
            """
            SELECT ai_category, ai_sub_category
            FROM transactions
            WHERE transaction_id = ?
            """,
            (tid,),
        ).fetchone()
        if row is not None:
            return (
                str(row["ai_category"] or "").strip(),
                str(row["ai_sub_category"] or "").strip(),
            )
    rep = _representative_transaction(conn, str(finding.get("merchant_key") or ""))
    if rep is not None:
        return (
            str(rep["ai_category"] or "").strip(),
            str(rep["ai_sub_category"] or "").strip(),
        )
    return "", ""


def _labels_equivalent_with_vocab(
    conn: sqlite3.Connection,
    cat_a: str,
    sub_a: str,
    cat_b: str,
    sub_b: str,
) -> bool:
    from webapp.services.classification_vocabulary import labels_equivalent

    vocabulary: dict[str, Any] | None = None
    if vocabulary_hint_enabled():
        from webapp.services.classification_vocabulary import load_classification_vocabulary

        vocabulary = load_classification_vocabulary(conn)
    return labels_equivalent(cat_a, sub_a, cat_b, sub_b, vocabulary)


def _finding_should_auto_resolve(
    conn: sqlite3.Connection,
    finding: dict[str, Any],
) -> bool:
    cur_cat, cur_sub = _current_labels_for_finding(conn, finding)
    sugg_cat = str(finding.get("suggested_category") or "").strip()
    sugg_sub = str(finding.get("suggested_sub") or "").strip()
    if sugg_cat or sugg_sub:
        return _labels_equivalent_with_vocab(conn, cur_cat, cur_sub, sugg_cat, sugg_sub)
    if finding.get("source") == "heuristic" and not sugg_cat and not sugg_sub:
        return _merchant_tx_labels_consistent(conn, str(finding.get("merchant_key") or ""))
    return False


def reconcile_open_findings(conn: sqlite3.Connection) -> int:
    """Mark open findings resolved when live DB labels already match the suggestion."""
    rows = conn.execute(
        "SELECT * FROM classification_audit_findings WHERE status = 'open'"
    ).fetchall()
    resolved = 0
    for row in rows:
        finding = _row_dict(row)
        if finding is None or not _finding_should_auto_resolve(conn, finding):
            continue
        conn.execute(
            """
            UPDATE classification_audit_findings
            SET status = 'resolved'
            WHERE id = ?
            """,
            (finding["id"],),
        )
        resolved += 1
    if resolved:
        conn.commit()
    return resolved


def _enrich_finding(conn: sqlite3.Connection, finding: dict[str, Any]) -> dict[str, Any]:
    cur_cat, cur_sub = _current_labels_for_finding(conn, finding)
    finding["current_category"] = cur_cat
    finding["current_sub"] = cur_sub
    sugg_cat = str(finding.get("suggested_category") or "").strip()
    sugg_sub = str(finding.get("suggested_sub") or "").strip()
    finding["already_fixed"] = bool(
        (sugg_cat or sugg_sub)
        and _labels_equivalent_with_vocab(conn, cur_cat, cur_sub, sugg_cat, sugg_sub)
    )
    return finding


def audit_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    reconcile_open_findings(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS open_count
        FROM classification_audit_findings
        WHERE status = 'open'
        """
    ).fetchone()
    last_run = conn.execute(
        """
        SELECT id, run_type, status, started_at, finished_at, summary_json
        FROM classification_audit_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "open_count": int(row["open_count"] if row else 0),
        "enabled": CLASSIFICATION_AUDIT_ENABLED,
        "last_run": _row_dict(last_run),
    }


def list_findings(
    conn: sqlite3.Connection,
    *,
    status: str = "open",
    limit: int = 100,
) -> list[dict[str, Any]]:
    if status == "open":
        reconcile_open_findings(conn)
    rows = conn.execute(
        """
        SELECT f.*, r.run_type, r.model AS audit_model
        FROM classification_audit_findings f
        JOIN classification_audit_runs r ON r.id = f.run_id
        WHERE f.status = ?
        ORDER BY f.confidence DESC, f.created_at DESC
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()
    return [_enrich_finding(conn, _row_dict(r)) for r in rows if _row_dict(r)]  # type: ignore[misc]


def dismiss_finding(conn: sqlite3.Connection, finding_id: int) -> bool:
    cur = conn.execute(
        """
        UPDATE classification_audit_findings
        SET status = 'dismissed'
        WHERE id = ? AND status = 'open'
        """,
        (finding_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def open_merchant_payload(conn: sqlite3.Connection, finding_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT merchant_key, transaction_id FROM classification_audit_findings WHERE id = ?",
        (finding_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Finding not found")
    return {
        "merchant_key": row["merchant_key"],
        "transaction_id": row["transaction_id"],
        "target_tab": "edit",
        "search_query": row["merchant_key"],
    }
