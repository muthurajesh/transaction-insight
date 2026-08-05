#!/usr/bin/env python3
"""S8 — Read-only merchant_key noise mining report.

Measures structural bank noise in existing payee keys and previews what
``scrub_bank_text`` would produce. Does **not** write to the database.

Usage (from repo root)::

    .venv/bin/python scripts/report_merchant_key_noise.py
    .venv/bin/python scripts/report_merchant_key_noise.py --top 40 --csv

Output: console summary + optional CSV under ``data/reports/`` (gitignored).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Allow `python scripts/...` from repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from webapp.processing.parse import looks_like_bank_noise, scrub_bank_text  # noqa: E402

_STAR_PREFIX = re.compile(r"^(?:SQ\s*)?\*", re.I)
_MASK = re.compile(r"(?:XX\d{3,}|x{4,})", re.I)
_CHECKCARD = re.compile(r"\bCHECKCARD\b", re.I)


@dataclass(frozen=True)
class KeyStats:
    merchant_key: str
    tx_count: int
    months: str
    has_label: bool
    label_status: str
    has_cadence: bool
    scrubbed: str
    would_change: bool
    flags: tuple[str, ...]


def _resolve_db(path: str | None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env = os.getenv("FINANCE_DB_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute():
            p = _REPO_ROOT / p
        return p.resolve()
    return (_REPO_ROOT / "data" / "finance.db").resolve()


def _flags_for_key(mk: str) -> list[str]:
    flags: list[str] = []
    if looks_like_bank_noise(mk):
        flags.append("bank_noise")
    if "DES:" in mk.upper():
        flags.append("des")
    if "INDN:" in mk.upper():
        flags.append("indn")
    if "CO ID:" in mk.upper() or "CO ID:" in mk:
        flags.append("co_id")
    if _MASK.search(mk):
        flags.append("mask")
    if _STAR_PREFIX.search(mk.strip()):
        flags.append("star_prefix")
    if _CHECKCARD.search(mk):
        flags.append("checkcard")
    if len(mk) > 40:
        flags.append("long_gt_40")
    if len(mk) > 60:
        flags.append("long_gt_60")
    return flags


def _candidate_key(mk: str, simple: str, original: str) -> str:
    """Preview remint candidate: scrub the key first, then Simple, then Original."""
    scrubbed_mk = scrub_bank_text(mk)
    if scrubbed_mk:
        return scrubbed_mk
    sd = scrub_bank_text(simple)
    if sd:
        return sd
    od = scrub_bank_text(original)
    if od:
        return od
    return mk


def load_key_rows(conn: sqlite3.Connection) -> list[KeyStats]:
    conn.row_factory = sqlite3.Row
    label_keys = {
        str(r["merchant_key"]): str(r["label_status"] or "")
        for r in conn.execute(
            "SELECT merchant_key, label_status FROM merchant_labels"
        )
    }
    cadence_keys = {
        str(r[0])
        for r in conn.execute(
            "SELECT merchant_key FROM cadence_rules WHERE enabled = 1"
        )
    }

    rows = conn.execute(
        """
        SELECT
          merchant_key,
          COUNT(*) AS tx_count,
          GROUP_CONCAT(DISTINCT budget_month) AS months,
          MAX(COALESCE(simple_description, '')) AS sample_simple,
          MAX(COALESCE(original_description, '')) AS sample_original
        FROM transactions
        WHERE COALESCE(merchant_key, '') != ''
        GROUP BY merchant_key
        ORDER BY tx_count DESC, merchant_key ASC
        """
    ).fetchall()

    out: list[KeyStats] = []
    for r in rows:
        mk = str(r["merchant_key"] or "")
        simple = str(r["sample_simple"] or "")
        original = str(r["sample_original"] or "")
        scrubbed = _candidate_key(mk, simple, original)
        flags = tuple(_flags_for_key(mk))
        out.append(
            KeyStats(
                merchant_key=mk,
                tx_count=int(r["tx_count"] or 0),
                months=str(r["months"] or ""),
                has_label=mk in label_keys,
                label_status=label_keys.get(mk, ""),
                has_cadence=mk in cadence_keys,
                scrubbed=scrubbed,
                would_change=bool(scrubbed) and scrubbed != mk,
                flags=flags,
            )
        )
    return out


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * n / total, 2)


def print_summary(keys: list[KeyStats], *, top: int) -> None:
    total_keys = len(keys)
    total_txs = sum(k.tx_count for k in keys)
    noisy = [k for k in keys if k.flags]
    would_change = [k for k in keys if k.would_change]
    bank_noise = [k for k in keys if "bank_noise" in k.flags]

    flag_counter: Counter[str] = Counter()
    for k in keys:
        for f in k.flags:
            flag_counter[f] += 1

    labeled_noisy = [k for k in bank_noise if k.has_label]
    cadence_noisy = [k for k in bank_noise if k.has_cadence]
    collision_targets: Counter[str] = Counter()
    for k in would_change:
        collision_targets[k.scrubbed] += 1
    multi_collapse = {t: n for t, n in collision_targets.items() if n > 1}

    print("=" * 72)
    print("Merchant key noise report (read-only)")
    print("=" * 72)
    print(f"Distinct merchant_key:     {total_keys}")
    print(f"Transaction rows covered:  {total_txs}")
    print(
        f"Keys with any noise flag:  {len(noisy)} "
        f"({_pct(len(noisy), total_keys)}% keys) / "
        f"{sum(k.tx_count for k in noisy)} txs "
        f"({_pct(sum(k.tx_count for k in noisy), total_txs)}% txs)"
    )
    print(
        f"looks_like_bank_noise:     {len(bank_noise)} keys / "
        f"{sum(k.tx_count for k in bank_noise)} txs"
    )
    print(
        f"Would change if reminted:  {len(would_change)} keys / "
        f"{sum(k.tx_count for k in would_change)} txs"
    )
    print(
        f"Noisy keys w/ merchant_labels: {len(labeled_noisy)} "
        f"(statuses: {Counter(k.label_status for k in labeled_noisy)})"
    )
    print(f"Noisy keys w/ cadence_rules:   {len(cadence_noisy)}")
    print(f"Scrub targets that collapse 2+ old keys: {len(multi_collapse)}")
    print()
    print("Flag counts (distinct keys):")
    for flag, n in flag_counter.most_common():
        print(f"  {flag:16s} {n:5d}  ({_pct(n, total_keys)}%)")

    print()
    print(f"Top {top} noisest keys (by tx_count, flagged only):")
    ranked = sorted(
        noisy,
        key=lambda k: (-k.tx_count, -len(k.merchant_key), k.merchant_key),
    )[:top]
    for k in ranked:
        arrow = f"  →  {k.scrubbed!r}" if k.would_change else "  (unchanged)"
        print(
            f"  n={k.tx_count:4d}  flags={','.join(k.flags) or '-'}  "
            f"label={k.label_status or '-'}  cadence={'Y' if k.has_cadence else 'N'}"
        )
        print(f"       {k.merchant_key!r}{arrow}")

    if multi_collapse:
        print()
        print("Sample collapse collisions (new_key ← count of old keys):")
        for new_key, n in sorted(
            multi_collapse.items(), key=lambda kv: (-kv[1], kv[0])
        )[:15]:
            print(f"  {n} → {new_key!r}")


def write_csv(keys: list[KeyStats], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "merchant_key",
        "tx_count",
        "months",
        "has_label",
        "label_status",
        "has_cadence",
        "flags",
        "scrubbed_candidate",
        "would_change",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for k in sorted(keys, key=lambda x: (-x.tx_count, x.merchant_key)):
            w.writerow(
                {
                    "merchant_key": k.merchant_key,
                    "tx_count": k.tx_count,
                    "months": k.months,
                    "has_label": int(k.has_label),
                    "label_status": k.label_status,
                    "has_cadence": int(k.has_cadence),
                    "flags": "|".join(k.flags),
                    "scrubbed_candidate": k.scrubbed,
                    "would_change": int(k.would_change),
                }
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only merchant_key noise mining report (S8)."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to finance.db (default: FINANCE_DB_PATH or data/finance.db)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="How many noisy keys to print (default 30)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write full key report CSV under data/reports/",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for CSV (default: data/reports)",
    )
    args = parser.parse_args(argv)

    db_path = _resolve_db(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    print(f"DB: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        keys = load_key_rows(conn)
    finally:
        conn.close()

    print_summary(keys, top=max(1, args.top))

    if args.csv:
        out_dir = (
            Path(args.out_dir).expanduser().resolve()
            if args.out_dir
            else (_REPO_ROOT / "data" / "reports")
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        csv_path = out_dir / f"merchant_key_noise_{stamp}.csv"
        write_csv(keys, csv_path)
        print()
        print(f"CSV written: {csv_path}")

    print()
    print("No database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
