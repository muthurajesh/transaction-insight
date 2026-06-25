from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from webapp.services.label_health import (
    MULTI_CATEGORY_SKIP_DEFAULTS,
    apply_mapping,
    build_health_report,
    detect_alias_candidates,
    report_taxonomy,
    _normalize_merchant_key,
    _similarity,
)

RULE_TYPES = frozenset(
    {"category_merge", "sub_category_merge", "merchant_alias", "label_unify"}
)

MAX_PREVIEW_ROWS_PER_PROPOSAL = 2000

CATEGORY_SIMILARITY_MIN = 0.82
SUB_CATEGORY_SIMILARITY_MIN = 0.85


def _proposal_alphabetical_key(proposal: dict[str, Any]) -> tuple[str, ...]:
    """Sort proposals so related categories/sub-categories appear together."""
    rule_type = str(proposal.get("rule_type") or "")
    type_rank = {
        "category_merge": "0",
        "label_unify": "1",
        "sub_category_merge": "1",
        "merchant_alias": "2",
    }.get(rule_type, "9")

    if rule_type == "category_merge":
        category = str(proposal.get("to_label") or proposal.get("from_label") or "")
    elif rule_type in {"label_unify", "sub_category_merge"}:
        category = str(
            proposal.get("target_category")
            or proposal.get("scope_category")
            or proposal.get("to_label")
            or ""
        )
    elif rule_type == "merchant_alias":
        category = "\uffff"  # merchants last
    else:
        category = ""

    to_label = str(proposal.get("to_label") or "")
    from_label = str(proposal.get("from_label") or "")
    return (
        category.casefold(),
        type_rank,
        to_label.casefold(),
        from_label.casefold(),
    )


def sort_proposals_alphabetically(
    proposals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(proposals, key=_proposal_alphabetical_key)

def _proposal_id(
    rule_type: str,
    from_label: str,
    to_label: str,
    scope_category: str = "",
    *,
    target_category: str = "",
) -> str:
    raw = f"{rule_type}|{scope_category}|{target_category}|{from_label}|{to_label}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cluster_similar_labels(labels: list[str], *, sim_min: float) -> list[list[str]]:
    """Group labels that are case variants or fuzzy duplicates."""
    if len(labels) < 2:
        return []
    parent = {label: label for label in labels}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            na, nb = _normalize_merchant_key(a), _normalize_merchant_key(b)
            if na == nb or _similarity(a, b) >= sim_min:
                union(a, b)

    grouped: dict[str, list[str]] = {}
    for label in labels:
        grouped.setdefault(find(label), []).append(label)

    clusters: list[list[str]] = []
    for members in grouped.values():
        unique = sorted(set(members))
        if len(unique) > 1:
            clusters.append(unique)
    return clusters


def _title_case_score(label: str) -> float:
    words = [w for w in label.split() if w]
    if not words:
        return 0.0
    titled = sum(1 for w in words if w[0].isupper())
    return titled / len(words)


def _load_sub_category_usage(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ai_category, ai_sub_category,
               COUNT(*) AS tx_count,
               ROUND(SUM(CASE WHEN flow_type = 'Expense' AND amount < 0 THEN -amount ELSE 0 END), 2)
                   AS expense_spend
        FROM transactions
        WHERE ai_sub_category IS NOT NULL AND TRIM(ai_sub_category) != ''
        GROUP BY ai_category, ai_sub_category
        """
    ).fetchall()
    usage: dict[str, dict[str, Any]] = {}
    for row in rows:
        sub = str(row["ai_sub_category"] or "").strip()
        cat = str(row["ai_category"] or "").strip()
        if not sub:
            continue
        bucket = usage.setdefault(
            sub,
            {"tx_count": 0, "expense_spend": 0.0, "by_category": {}},
        )
        n = int(row["tx_count"] or 0)
        bucket["tx_count"] += n
        bucket["expense_spend"] += float(row["expense_spend"] or 0)
        if cat:
            bucket["by_category"][cat] = bucket["by_category"].get(cat, 0) + n
    return usage


def _pick_canonical_sub(cluster: list[str], usage: dict[str, dict[str, Any]]) -> str:
    def score(label: str) -> tuple[int, float, int]:
        u = usage.get(label, {})
        return (
            int(u.get("tx_count") or 0),
            _title_case_score(label),
            -len(label),
        )

    return max(cluster, key=score)


def _pick_target_category(cluster: list[str], usage: dict[str, dict[str, Any]]) -> str:
    cat_totals: dict[str, int] = {}
    for sub in cluster:
        for cat, n in usage.get(sub, {}).get("by_category", {}).items():
            cat_totals[cat] = cat_totals.get(cat, 0) + int(n)
    if not cat_totals:
        return ""
    return max(cat_totals, key=lambda c: cat_totals[c])


def _split_proposals_for_apply(
    proposals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (mapping proposals, label_unify proposals)."""
    mapping: list[dict[str, Any]] = []
    unify: list[dict[str, Any]] = []
    for p in proposals:
        if p.get("rule_type") == "label_unify":
            unify.append(p)
            continue
        tc = str(p.get("target_category") or "").strip()
        sc = str(p.get("scope_category") or "").strip()
        if p.get("rule_type") == "sub_category_merge" and tc and tc != sc:
            unify.append(
                {
                    **p,
                    "rule_type": "label_unify",
                    "from_sub_categories": [p.get("from_label")],
                    "source_categories": [sc] if sc else [],
                }
            )
            continue
        mapping.append(p)
    return mapping, unify


def _label_unify_cluster_subs(proposal: dict[str, Any]) -> list[str]:
    to_sub = str(proposal.get("to_label") or "").strip()
    variants = [
        s.strip()
        for s in (proposal.get("from_sub_categories") or [proposal.get("from_label")])
        if s and str(s).strip()
    ]
    cluster = set(variants)
    if to_sub:
        cluster.add(to_sub)
    return sorted(cluster)


def _apply_label_unify_rows(conn: sqlite3.Connection, proposal: dict[str, Any]) -> int:
    to_sub = str(proposal.get("to_label") or "").strip()
    to_cat = str(proposal.get("target_category") or "").strip()
    cluster_subs = _label_unify_cluster_subs(proposal)
    if not cluster_subs or not to_sub:
        return 0

    updated = 0
    for table in ("transactions", "merchant_labels"):
        for from_sub in cluster_subs:
            if to_cat:
                cur = conn.execute(
                    f"""
                    UPDATE {table}
                    SET ai_category = ?, ai_sub_category = ?
                    WHERE COALESCE(ai_sub_category, '') = ?
                      AND (
                          COALESCE(ai_sub_category, '') != ?
                          OR COALESCE(ai_category, '') != ?
                      )
                    """,
                    (to_cat, to_sub, from_sub, to_sub, to_cat),
                )
            elif from_sub != to_sub:
                cur = conn.execute(
                    f"""
                    UPDATE {table}
                    SET ai_sub_category = ?
                    WHERE COALESCE(ai_sub_category, '') = ?
                    """,
                    (to_sub, from_sub),
                )
            else:
                continue
            updated += cur.rowcount
    return updated


def _apply_label_unify(
    conn: sqlite3.Connection,
    proposal: dict[str, Any],
    *,
    dry_run: bool,
) -> int:
    if not dry_run:
        conn.execute("BEGIN")
    try:
        updated = _apply_label_unify_rows(conn, proposal)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return updated


def _apply_all_proposals(
    conn: sqlite3.Connection,
    proposals: list[dict[str, Any]],
    *,
    reconcile: bool,
    dry_run: bool,
) -> dict[str, Any]:
    mapping_props, unify_props = _split_proposals_for_apply(proposals)
    stats = apply_mapping(
        conn, proposals_to_mapping(mapping_props, reconcile=reconcile), dry_run=dry_run
    )
    stats = dict(stats)
    stats["label_unify"] = 0
    if unify_props:
        if not dry_run:
            conn.execute("BEGIN")
        try:
            for proposal in unify_props:
                stats["label_unify"] += _apply_label_unify_rows(conn, proposal)
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    return stats


def _category_stats(conn: sqlite3.Connection, category: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS tx_count,
               ROUND(SUM(CASE WHEN flow_type = 'Expense' AND amount < 0 THEN -amount ELSE 0 END), 2)
                   AS expense_spend
        FROM transactions
        WHERE ai_category = ?
        """,
        (category,),
    ).fetchone()
    return {
        "tx_count": int(row["tx_count"] or 0),
        "expense_spend": float(row["expense_spend"] or 0),
    }


def _sub_category_stats(
    conn: sqlite3.Connection, category: str, sub_category: str
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS tx_count,
               ROUND(SUM(CASE WHEN flow_type = 'Expense' AND amount < 0 THEN -amount ELSE 0 END), 2)
                   AS expense_spend
        FROM transactions
        WHERE ai_category = ? AND COALESCE(ai_sub_category, '') = ?
        """,
        (category, sub_category),
    ).fetchone()
    return {
        "tx_count": int(row["tx_count"] or 0),
        "expense_spend": float(row["expense_spend"] or 0),
    }


def _merchant_stats(conn: sqlite3.Connection, merchant_key: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS tx_count,
               ROUND(SUM(CASE WHEN flow_type = 'Expense' AND amount < 0 THEN -amount ELSE 0 END), 2)
                   AS expense_spend
        FROM transactions
        WHERE merchant_key = ?
        """,
        (merchant_key,),
    ).fetchone()
    return {
        "tx_count": int(row["tx_count"] or 0),
        "expense_spend": float(row["expense_spend"] or 0),
    }


def _pick_canonical(
    a: str, b: str, stats_a: dict[str, Any], stats_b: dict[str, Any]
) -> tuple[str, str, float]:
    """Return (canonical, alias, confidence)."""
    na, nb = _normalize_merchant_key(a), _normalize_merchant_key(b)
    if na == nb and a != b:
        canonical = a if stats_a["tx_count"] >= stats_b["tx_count"] else b
        alias = b if canonical == a else a
        return canonical, alias, 1.0

    if stats_a["tx_count"] > stats_b["tx_count"]:
        canonical, alias = a, b
    elif stats_b["tx_count"] > stats_a["tx_count"]:
        canonical, alias = b, a
    elif stats_a["expense_spend"] >= stats_b["expense_spend"]:
        canonical, alias = a, b
    else:
        canonical, alias = b, a

    sim = _similarity(a, b)
    return canonical, alias, round(min(0.99, 0.7 + 0.3 * sim), 3)


def _proposal(
    *,
    rule_type: str,
    from_label: str,
    to_label: str,
    source: str,
    rationale: str,
    confidence: float,
    affected_transactions: int,
    affected_spend: float,
    scope_category: str | None = None,
    target_category: str | None = None,
    from_sub_categories: list[str] | None = None,
    source_categories: list[str] | None = None,
    sample_merchants: list[str] | None = None,
) -> dict[str, Any]:
    if from_label == to_label and rule_type != "label_unify":
        raise ValueError("from_label and to_label must differ")
    scope = (scope_category or "").strip() or None
    target = (target_category or "").strip() or None
    variants = sorted({s.strip() for s in (from_sub_categories or []) if s and str(s).strip()})
    if rule_type == "label_unify" and not variants:
        variants = [from_label] if from_label else []
    src_cats = sorted({c.strip() for c in (source_categories or []) if c and str(c).strip()})
    pid = _proposal_id(
        rule_type,
        from_label,
        to_label,
        scope or "",
        target_category=target or "",
    )
    if rule_type == "label_unify" and variants:
        pid = hashlib.sha256(
            f"label_unify|{target}|{to_label}|{'|'.join(variants)}".encode()
        ).hexdigest()[:16]
    return {
        "id": pid,
        "rule_type": rule_type,
        "from_label": from_label,
        "to_label": to_label,
        "scope_category": scope,
        "target_category": target,
        "from_sub_categories": variants,
        "source_categories": src_cats,
        "source": source,
        "rationale": rationale,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "affected_transactions": affected_transactions,
        "affected_spend": affected_spend,
        "sample_merchants": (sample_merchants or [])[:5],
        "automation_ready": confidence >= 1.0,
    }


def _sample_merchants_for_category(conn: sqlite3.Connection, category: str, limit: int = 5) -> list[str]:
    rows = conn.execute(
        """
        SELECT merchant_key, COUNT(*) AS n
        FROM transactions
        WHERE ai_category = ?
        GROUP BY merchant_key
        ORDER BY n DESC
        LIMIT ?
        """,
        (category, limit),
    ).fetchall()
    return [str(r["merchant_key"]) for r in rows]


def _sample_merchants_for_sub(
    conn: sqlite3.Connection, category: str, sub_category: str, limit: int = 5
) -> list[str]:
    rows = conn.execute(
        """
        SELECT merchant_key, COUNT(*) AS n
        FROM transactions
        WHERE ai_category = ? AND COALESCE(ai_sub_category, '') = ?
        GROUP BY merchant_key
        ORDER BY n DESC
        LIMIT ?
        """,
        (category, sub_category, limit),
    ).fetchall()
    return [str(r["merchant_key"]) for r in rows]


def build_heuristic_proposals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Fast, deterministic duplicate detection — no LLM."""
    proposals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add(p: dict[str, Any]) -> None:
        if p["id"] in seen_ids:
            return
        seen_ids.add(p["id"])
        proposals.append(p)

    taxonomy = report_taxonomy(conn)
    categories = [c for c in (taxonomy.get("categories") or []) if c and str(c).strip()]

    for cluster in _cluster_similar_labels(categories, sim_min=CATEGORY_SIMILARITY_MIN):
        if len(cluster) < 2:
            continue
        canonical = max(
            cluster,
            key=lambda c: (_category_stats(conn, c)["tx_count"], _title_case_score(c)),
        )
        for alias in cluster:
            if alias == canonical:
                continue
            stats = _category_stats(conn, alias)
            sim = _similarity(alias, canonical)
            na_eq = _normalize_merchant_key(alias) == _normalize_merchant_key(canonical)
            add(
                _proposal(
                    rule_type="category_merge",
                    from_label=alias,
                    to_label=canonical,
                    source="heuristic",
                    rationale=(
                        "Same category after normalizing punctuation and case."
                        if na_eq
                        else f"Clustered with {canonical} ({int(sim * 100)}% match)."
                    ),
                    confidence=1.0 if na_eq else round(min(0.99, 0.75 + 0.25 * sim), 3),
                    affected_transactions=stats["tx_count"],
                    affected_spend=stats["expense_spend"],
                    sample_merchants=_sample_merchants_for_category(conn, alias),
                )
            )

    sub_usage = _load_sub_category_usage(conn)
    all_subs = sorted(sub_usage.keys())
    sub_clusters = _cluster_similar_labels(all_subs, sim_min=SUB_CATEGORY_SIMILARITY_MIN)
    seen_unify: set[str] = set()

    for cluster in sub_clusters:
        canonical_sub = _pick_canonical_sub(cluster, sub_usage)
        target_cat = _pick_target_category(cluster, sub_usage)
        source_cats = sorted(
            {
                cat
                for sub in cluster
                for cat in sub_usage.get(sub, {}).get("by_category", {}).keys()
            }
        )
        variants = [s for s in cluster if s != canonical_sub]
        if not variants:
            continue

        tx_total = sum(int(sub_usage.get(s, {}).get("tx_count") or 0) for s in cluster)
        spend_total = sum(float(sub_usage.get(s, {}).get("expense_spend") or 0) for s in cluster)
        cluster_key = "|".join(sorted(cluster))
        if cluster_key in seen_unify:
            continue
        seen_unify.add(cluster_key)

        sample: list[str] = []
        for sub in cluster:
            for cat in source_cats:
                sample.extend(_sample_merchants_for_sub(conn, cat, sub, limit=2))
                if len(sample) >= 5:
                    break
            if len(sample) >= 5:
                break

        na_variants = {_normalize_merchant_key(s) for s in cluster}
        conf = 1.0 if len(na_variants) == 1 else 0.92

        if len(source_cats) > 1:
            rationale = (
                f"Same sub-category concept across {len(source_cats)} categories "
                f"({', '.join(source_cats[:4])}{'…' if len(source_cats) > 4 else ''}). "
                f"Unify to one spelling under {target_cat}."
            )
        else:
            rationale = (
                f"Spelling variants of the same sub-category — merge all into "
                f"“{canonical_sub}”."
            )

        try:
            add(
                _proposal(
                    rule_type="label_unify",
                    from_label=variants[0],
                    to_label=canonical_sub,
                    target_category=target_cat,
                    from_sub_categories=variants,
                    source_categories=source_cats,
                    source="heuristic",
                    rationale=rationale,
                    confidence=conf,
                    affected_transactions=tx_total,
                    affected_spend=spend_total,
                    sample_merchants=sample[:5],
                )
            )
        except ValueError:
            continue

    for group in detect_alias_candidates(conn):
        canonical = str(group.get("canonical_suggestion") or "")
        if not canonical:
            continue
        for member in group.get("members") or []:
            if member == canonical:
                continue
            stats = _merchant_stats(conn, member)
            sim = _similarity(member, canonical)
            add(
                _proposal(
                    rule_type="merchant_alias",
                    from_label=member,
                    to_label=canonical,
                    source="heuristic",
                    rationale=f"Likely same merchant ({int(sim * 100)}% name match).",
                    confidence=round(min(0.98, 0.75 + 0.25 * sim), 3),
                    affected_transactions=stats["tx_count"],
                    affected_spend=stats["expense_spend"],
                    sample_merchants=[member],
                )
            )

    proposals.sort(
        key=lambda p: (-p["confidence"], -p["affected_transactions"], p["rule_type"])
    )
    return proposals


def analyze_taxonomy(conn: sqlite3.Connection) -> dict[str, Any]:
    report = build_health_report(conn)
    proposals = build_heuristic_proposals(conn)
    taxonomy = report.get("taxonomy") or {}
    return {
        "summary": {
            "category_count": taxonomy.get("category_count", 0),
            "pair_count": taxonomy.get("pair_count", 0),
            "ambiguous_sub_category_count": len(taxonomy.get("ambiguous_sub_categories") or []),
            "alias_group_count": len(report.get("alias_candidates") or []),
            "drift_rows": (report.get("drift") or {}).get("summary", {}).get("drift_rows", 0),
            "heuristic_proposal_count": len(proposals),
        },
        "ambiguous_sub_categories": (taxonomy.get("ambiguous_sub_categories") or [])[:15],
        "proposals": proposals,
    }


def suggest_taxonomy_proposals_with_llm(
    conn: sqlite3.Connection,
    *,
    model: str | None = None,
    include_heuristic: bool = True,
) -> dict[str, Any]:
    """LLM proposes taxonomy rules as reviewable items — never auto-applied."""
    from webapp.services.llm import chat_completion, extract_json

    report = build_health_report(conn)
    taxonomy = report.get("taxonomy") or {}
    heuristic = build_heuristic_proposals(conn) if include_heuristic else []

    payload = {
        "categories": taxonomy.get("categories") or [],
        "top_category_subcategory_pairs": (taxonomy.get("pairs") or [])[:50],
        "ambiguous_sub_categories": (taxonomy.get("ambiguous_sub_categories") or [])[:25],
        "alias_groups": [
            {
                "canonical_suggestion": g.get("canonical_suggestion"),
                "members": g.get("members"),
                "tx_counts": g.get("tx_counts"),
            }
            for g in (report.get("alias_candidates") or [])[:40]
        ],
        "existing_heuristic_ids": [p["id"] for p in heuristic[:30]],
    }

    system = """You are a personal finance taxonomy assistant. Propose label cleanup rules for user review.

Each proposal merges a duplicate or synonym INTO a canonical label. Nothing runs automatically.

Rules:
- category_merge: synonym top-level categories that clearly mean the same thing in this dataset.
- label_unify: ONE proposal per cluster of sub-category spelling variants — pick ONE canonical sub-category string (Title Case preferred). Set target_category to the category with the most transactions when variants span multiple top-level categories.
- sub_category_merge: legacy single-category merge — prefer label_unify for variant clusters instead.
- merchant_alias: same real payee, different spelling — NOT different check numbers or transfer directions.

CRITICAL: Never propose multiple rules that target different spellings for the same concept. Emit one label_unify with from_sub_categories listing all variants and one to_label.

Confidence 0.0-1.0:
- 1.0 only for case/punctuation variants.
- 0.85+ for strong synonyms with same spend pattern.
- Below 0.7: omit — too uncertain.

Respond ONLY with JSON:
{
  "proposals": [
    {
      "rule_type": "category_merge|label_unify|sub_category_merge|merchant_alias",
      "from_label": "...",
      "to_label": "...",
      "target_category": "Category with most tx (required for label_unify)",
      "from_sub_categories": ["variant1", "variant2"],
      "source_categories": ["Category A", "Category B"],
      "scope_category": null or "Category for sub_category_merge only",
      "confidence": 0.92,
      "rationale": "One sentence why this merge is safe."
    }
  ]
}

Prefer fewer high-confidence proposals. One label_unify per variant cluster."""

    user = (
        "Analyze this finance DB taxonomy and return conservative merge proposals.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    raw = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.15,
        model=model,
        caller="taxonomy.suggest",
    )
    parsed = extract_json(raw)
    llm_items = parsed.get("proposals", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(llm_items, list):
        raise ValueError("LLM did not return a proposals array.")

    merged: dict[str, dict[str, Any]] = {p["id"]: p for p in heuristic}
    for item in llm_items:
        if not isinstance(item, dict):
            continue
        rule_type = str(item.get("rule_type") or "").strip()
        if rule_type not in RULE_TYPES:
            continue
        from_label = str(item.get("from_label") or "").strip()
        to_label = str(item.get("to_label") or "").strip()
        if not from_label or not to_label or from_label == to_label:
            continue
        scope = item.get("scope_category")
        scope_str = str(scope).strip() if scope else None

        if rule_type == "category_merge":
            stats = _category_stats(conn, from_label)
            sample = _sample_merchants_for_category(conn, from_label)
        elif rule_type == "label_unify":
            variants = [
                str(v).strip()
                for v in (item.get("from_sub_categories") or [from_label])
                if str(v).strip()
            ]
            stats = {"tx_count": 0, "expense_spend": 0.0}
            for v in variants:
                for cat in item.get("source_categories") or [""]:
                    if cat:
                        s = _sub_category_stats(conn, str(cat), v)
                    else:
                        row = conn.execute(
                            """
                            SELECT COUNT(*) AS tx_count,
                                   ROUND(SUM(CASE WHEN flow_type = 'Expense' AND amount < 0
                                        THEN -amount ELSE 0 END), 2) AS expense_spend
                            FROM transactions WHERE COALESCE(ai_sub_category, '') = ?
                            """,
                            (v,),
                        ).fetchone()
                        s = {
                            "tx_count": int(row["tx_count"] or 0),
                            "expense_spend": float(row["expense_spend"] or 0),
                        }
                    stats["tx_count"] += s["tx_count"]
                    stats["expense_spend"] += s["expense_spend"]
            sample = []
            target_cat = str(item.get("target_category") or "").strip() or None
        elif rule_type == "sub_category_merge":
            if not scope_str:
                continue
            stats = _sub_category_stats(conn, scope_str, from_label)
            sample = _sample_merchants_for_sub(conn, scope_str, from_label)
        else:
            stats = _merchant_stats(conn, from_label)
            sample = [from_label]

        try:
            proposal = _proposal(
                rule_type=rule_type,
                from_label=from_label,
                to_label=to_label,
                scope_category=scope_str,
                target_category=str(item.get("target_category") or "").strip() or None,
                from_sub_categories=[
                    str(v).strip()
                    for v in (item.get("from_sub_categories") or [])
                    if str(v).strip()
                ]
                or None,
                source_categories=[
                    str(c).strip()
                    for c in (item.get("source_categories") or [])
                    if str(c).strip()
                ]
                or None,
                source="llm",
                rationale=str(item.get("rationale") or "Suggested by AI taxonomy review."),
                confidence=float(item.get("confidence") or 0.75),
                affected_transactions=stats["tx_count"],
                affected_spend=stats["expense_spend"],
                sample_merchants=sample if rule_type != "label_unify" else [],
            )
        except ValueError:
            continue
        if proposal["id"] not in merged or merged[proposal["id"]]["source"] == "heuristic":
            merged[proposal["id"]] = proposal

    proposals = sort_proposals_alphabetically(list(merged.values()))
    return {
        "summary": analyze_taxonomy(conn)["summary"],
        "proposals": proposals,
        "llm_proposal_count": sum(1 for p in proposals if p["source"] == "llm"),
    }


def proposals_to_mapping(
    proposals: list[dict[str, Any]],
    *,
    reconcile: bool = False,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "version": 1,
        "category_merges": {},
        "sub_category_merges": {},
        "merchant_aliases": {},
        "reconcile": {
            "enabled": reconcile,
            "only_confirmed": True,
            "skip_merchants": sorted(MULTI_CATEGORY_SKIP_DEFAULTS),
        },
    }
    for p in proposals:
        rule_type = p.get("rule_type")
        from_label = str(p.get("from_label") or "").strip()
        to_label = str(p.get("to_label") or "").strip()
        if not from_label or not to_label or from_label == to_label:
            continue
        if rule_type == "category_merge":
            mapping["category_merges"][from_label] = to_label
        elif rule_type == "sub_category_merge":
            cat = str(p.get("scope_category") or "").strip()
            if not cat:
                continue
            mapping["sub_category_merges"].setdefault(cat, {})[from_label] = to_label
        elif rule_type == "merchant_alias":
            mapping["merchant_aliases"][from_label] = to_label
    return mapping


def _change_label_for_proposal(
    proposal: dict[str, Any],
    *,
    ai_category: str,
    ai_sub_category: str,
    merchant_key: str,
) -> tuple[dict[str, str], dict[str, str], str]:
    """Return before dict, after dict, and a short human summary of the change."""
    before = {
        "ai_category": ai_category or "",
        "ai_sub_category": ai_sub_category or "",
        "merchant_key": merchant_key or "",
    }
    after = dict(before)
    rule_type = proposal.get("rule_type")
    from_label = str(proposal.get("from_label") or "")
    to_label = str(proposal.get("to_label") or "")

    if rule_type == "category_merge":
        after["ai_category"] = to_label
        summary = f"Category: {from_label} → {to_label}"
    elif rule_type == "sub_category_merge":
        after["ai_sub_category"] = to_label
        tc = str(proposal.get("target_category") or "").strip()
        if tc:
            after["ai_category"] = tc
        scope = proposal.get("scope_category") or ""
        summary = f"Sub-category: {from_label} → {to_label}" + (
            f" (under {scope})" if scope else ""
        )
        if tc and tc != scope:
            summary += f"; category → {tc}"
    elif rule_type == "label_unify":
        after["ai_sub_category"] = to_label
        tc = str(proposal.get("target_category") or "").strip()
        if tc:
            after["ai_category"] = tc
        variants = proposal.get("from_sub_categories") or [from_label]
        summary = f"Unify {len(variants)} variant(s) → {to_label}" + (
            f" under {tc}" if tc else ""
        )
    elif rule_type == "merchant_alias":
        after["merchant_key"] = to_label
        summary = f"Merchant: {from_label} → {to_label}"
    else:
        summary = "Unknown rule type"

    return before, after, summary


def _count_matches(conn: sqlite3.Connection, proposal: dict[str, Any]) -> int:
    rule_type = proposal.get("rule_type")
    from_label = str(proposal.get("from_label") or "")
    if rule_type == "category_merge":
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE ai_category = ?",
            (from_label,),
        ).fetchone()
    elif rule_type == "label_unify":
        cluster_subs = _label_unify_cluster_subs(proposal)
        if not cluster_subs:
            return 0
        to_sub = str(proposal.get("to_label") or "").strip()
        to_cat = str(proposal.get("target_category") or "").strip()
        placeholders = ",".join("?" * len(cluster_subs))
        if to_cat:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM transactions
                WHERE COALESCE(ai_sub_category, '') IN ({placeholders})
                  AND (
                      COALESCE(ai_sub_category, '') != ?
                      OR COALESCE(ai_category, '') != ?
                  )
                """,
                (*cluster_subs, to_sub, to_cat),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM transactions
                WHERE COALESCE(ai_sub_category, '') IN ({placeholders})
                  AND COALESCE(ai_sub_category, '') != ?
                """,
                (*cluster_subs, to_sub),
            ).fetchone()
    elif rule_type == "sub_category_merge":
        scope = str(proposal.get("scope_category") or "")
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM transactions
            WHERE ai_category = ? AND COALESCE(ai_sub_category, '') = ?
            """,
            (scope, from_label),
        ).fetchone()
    elif rule_type == "merchant_alias":
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE merchant_key = ?",
            (from_label,),
        ).fetchone()
    else:
        return 0
    return int(row["n"] or 0)


def _fetch_match_rows(
    conn: sqlite3.Connection,
    proposal: dict[str, Any],
    *,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    rule_type = proposal.get("rule_type")
    from_label = str(proposal.get("from_label") or "")
    sql = """
        SELECT transaction_id, date, amount, merchant_key, ai_category, ai_sub_category
        FROM transactions
        WHERE {where}
        ORDER BY date DESC, amount
    """
    if rule_type == "category_merge":
        where = "ai_category = ?"
        params: tuple[Any, ...] = (from_label,)
    elif rule_type == "label_unify":
        cluster_subs = _label_unify_cluster_subs(proposal)
        if not cluster_subs:
            return []
        to_sub = str(proposal.get("to_label") or "").strip()
        to_cat = str(proposal.get("target_category") or "").strip()
        placeholders = ",".join("?" * len(cluster_subs))
        if to_cat:
            where = f"""
                COALESCE(ai_sub_category, '') IN ({placeholders})
                AND (
                    COALESCE(ai_sub_category, '') != ?
                    OR COALESCE(ai_category, '') != ?
                )
            """
            params = (*cluster_subs, to_sub, to_cat)
        else:
            where = f"""
                COALESCE(ai_sub_category, '') IN ({placeholders})
                AND COALESCE(ai_sub_category, '') != ?
            """
            params = (*cluster_subs, to_sub)
    elif rule_type == "sub_category_merge":
        scope = str(proposal.get("scope_category") or "")
        where = "ai_category = ? AND COALESCE(ai_sub_category, '') = ?"
        params = (scope, from_label)
    elif rule_type == "merchant_alias":
        where = "merchant_key = ?"
        params = (from_label,)
    else:
        return []

    query = sql.format(where=where)
    if limit is not None:
        query += " LIMIT ?"
        params = (*params, limit)
    return conn.execute(query, params).fetchall()


def collect_preview_samples(
    conn: sqlite3.Connection,
    proposals: list[dict[str, Any]],
    *,
    limit_per_proposal: int | None = None,
) -> list[dict[str, Any]]:
    """Transactions showing before → after for each selected proposal."""
    groups: list[dict[str, Any]] = []
    for proposal in proposals:
        total = _count_matches(conn, proposal)
        if total <= 0:
            groups.append(
                {
                    "proposal_id": proposal.get("id"),
                    "rule_type": proposal.get("rule_type"),
                    "from_label": proposal.get("from_label"),
                    "to_label": proposal.get("to_label"),
                    "scope_category": proposal.get("scope_category"),
                    "target_category": proposal.get("target_category"),
                    "from_sub_categories": proposal.get("from_sub_categories"),
                    "total_matches": 0,
                    "samples": [],
                    "truncated": False,
                }
            )
            continue

        fetch_limit: int | None
        truncated = False
        if limit_per_proposal is None:
            if total > MAX_PREVIEW_ROWS_PER_PROPOSAL:
                fetch_limit = MAX_PREVIEW_ROWS_PER_PROPOSAL
                truncated = True
            else:
                fetch_limit = None
        else:
            fetch_limit = max(1, int(limit_per_proposal))
            if total > fetch_limit:
                truncated = True

        rows = _fetch_match_rows(conn, proposal, limit=fetch_limit)
        samples: list[dict[str, Any]] = []
        for row in rows:
            before, after, summary = _change_label_for_proposal(
                proposal,
                ai_category=str(row["ai_category"] or ""),
                ai_sub_category=str(row["ai_sub_category"] or ""),
                merchant_key=str(row["merchant_key"] or ""),
            )
            samples.append(
                {
                    "transaction_id": row["transaction_id"],
                    "date": row["date"],
                    "amount": float(row["amount"] or 0),
                    "before": before,
                    "after": after,
                    "change_summary": summary,
                }
            )

        groups.append(
            {
                "proposal_id": proposal.get("id"),
                "rule_type": proposal.get("rule_type"),
                "from_label": proposal.get("from_label"),
                "to_label": proposal.get("to_label"),
                "scope_category": proposal.get("scope_category"),
                "target_category": proposal.get("target_category"),
                "from_sub_categories": proposal.get("from_sub_categories"),
                "total_matches": total,
                "samples": samples,
                "truncated": truncated,
            }
        )
    return groups


def preview_taxonomy_proposals(
    conn: sqlite3.Connection,
    proposals: list[dict[str, Any]],
    *,
    reconcile: bool = False,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    mapping_props, unify_props = _split_proposals_for_apply(proposals)
    stats = _apply_all_proposals(
        conn, proposals, reconcile=reconcile, dry_run=True
    )
    sample_groups = collect_preview_samples(
        conn, proposals, limit_per_proposal=sample_limit
    )
    return {
        "proposal_count": len(proposals),
        "mapping": proposals_to_mapping(mapping_props, reconcile=reconcile),
        "preview": stats,
        "sample_groups": sample_groups,
        "unify_count": len(unify_props),
        "max_preview_rows_per_proposal": MAX_PREVIEW_ROWS_PER_PROPOSAL,
    }


def apply_taxonomy_proposals(
    conn: sqlite3.Connection,
    proposals: list[dict[str, Any]],
    *,
    reconcile: bool = False,
) -> dict[str, Any]:
    if not proposals:
        raise ValueError("No proposals selected.")
    mapping_props, unify_props = _split_proposals_for_apply(proposals)
    stats = _apply_all_proposals(
        conn, proposals, reconcile=reconcile, dry_run=False
    )
    return {
        "proposal_count": len(proposals),
        "applied_proposal_ids": [p.get("id") for p in proposals],
        "mapping": proposals_to_mapping(mapping_props, reconcile=reconcile),
        "stats": stats,
        "unify_count": len(unify_props),
    }
