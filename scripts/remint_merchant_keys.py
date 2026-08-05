#!/usr/bin/env python3
"""S7 — Remint historical merchant_key values via scrub_bank_text.

Default is **dry-run** (plan only). Pass ``--apply`` to write (backs up DB first).

Usage (from repo root)::

    .venv/bin/python scripts/remint_merchant_keys.py
    .venv/bin/python scripts/remint_merchant_keys.py --scope bank_noise --csv
    .venv/bin/python scripts/remint_merchant_keys.py --apply

Scope:
  bank_noise  — keys matching looks_like_bank_noise / ACH / mask / star / checkcard
  all         — any key where scrub_bank_text(key) differs from key
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from webapp.processing.parse import looks_like_bank_noise, scrub_bank_text  # noqa: E402

_STAR_PREFIX = re.compile(r"^(?:SQ\s*)?\*", re.I)
_MASK = re.compile(r"(?:XX\d{3,}|x{4,})", re.I)
_CHECKCARD = re.compile(r"\bCHECKCARD\b", re.I)

_STATUS_RANK = {
    "confirmed": 3,
    "needs_review": 2,
    "pending": 1,
}


@dataclass(frozen=True)
class RemintMapping:
    old_key: str
    new_key: str
    tx_count: int
    flags: tuple[str, ...]
    old_label_status: str
    new_key_exists: bool
    new_label_status: str
    collision: bool


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
    upper = mk.upper()
    if "DES:" in upper:
        flags.append("des")
    if "INDN:" in upper:
        flags.append("indn")
    if "CO ID:" in upper:
        flags.append("co_id")
    if _MASK.search(mk):
        flags.append("mask")
    if _STAR_PREFIX.search(mk.strip()):
        flags.append("star_prefix")
    if _CHECKCARD.search(mk):
        flags.append("checkcard")
    return flags


def _in_scope(mk: str, flags: list[str], scope: str) -> bool:
    scrubbed = scrub_bank_text(mk)
    if not scrubbed or scrubbed == mk:
        return False
    if scope == "all":
        return True
    # bank_noise (default): structural noise only — not long-clean names
    return bool(
        flags
        and any(
            f in flags
            for f in ("bank_noise", "des", "indn", "co_id", "mask", "star_prefix", "checkcard")
        )
    )


def _status_rank(status: str) -> int:
    return _STATUS_RANK.get(str(status or "").strip().lower(), 0)


def build_plan(conn: sqlite3.Connection, *, scope: str) -> list[RemintMapping]:
    conn.row_factory = sqlite3.Row
    existing_keys = {
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT merchant_key FROM transactions WHERE COALESCE(merchant_key,'') != ''"
        )
    }
    labels = {
        str(r["merchant_key"]): str(r["label_status"] or "")
        for r in conn.execute("SELECT merchant_key, label_status FROM merchant_labels")
    }
    rows = conn.execute(
        """
        SELECT merchant_key, COUNT(*) AS tx_count
        FROM transactions
        WHERE COALESCE(merchant_key, '') != ''
        GROUP BY merchant_key
        """
    ).fetchall()

    plan: list[RemintMapping] = []
    for r in rows:
        old = str(r["merchant_key"] or "")
        flags = _flags_for_key(old)
        if not _in_scope(old, flags, scope):
            continue
        new = scrub_bank_text(old)
        if not new or new == old:
            continue
        new_exists = new in existing_keys or new in labels
        plan.append(
            RemintMapping(
                old_key=old,
                new_key=new,
                tx_count=int(r["tx_count"] or 0),
                flags=tuple(flags),
                old_label_status=labels.get(old, ""),
                new_key_exists=new_exists,
                new_label_status=labels.get(new, ""),
                collision=new_exists and new != old,
            )
        )
    plan.sort(key=lambda m: (-m.tx_count, m.old_key))
    return plan


def print_plan_summary(plan: list[RemintMapping]) -> None:
    tx_total = sum(m.tx_count for m in plan)
    collisions = [m for m in plan if m.collision]
    confirmed = [m for m in plan if m.old_label_status == "confirmed"]
    collapse: dict[str, list[str]] = defaultdict(list)
    for m in plan:
        collapse[m.new_key].append(m.old_key)
    multi = {k: v for k, v in collapse.items() if len(v) > 1}

    print("=" * 72)
    print("S7 remint plan")
    print("=" * 72)
    print(f"Mappings:              {len(plan)}")
    print(f"Transaction rows:      {tx_total}")
    print(f"Collisions (new exists): {len(collisions)}")
    print(f"Confirmed labels moved:  {len(confirmed)}")
    print(f"New keys absorbing 2+ old: {len(multi)}")
    print()
    print("Top mappings:")
    for m in plan[:20]:
        coll = " [collision]" if m.collision else ""
        lab = f" label={m.old_label_status}" if m.old_label_status else ""
        print(f"  n={m.tx_count:4d}{lab}{coll}")
        print(f"       {m.old_key!r}")
        print(f"    →  {m.new_key!r}")
    if multi:
        print()
        print("Collapse samples (new ← old count):")
        for new_key, olds in sorted(multi.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:12]:
            print(f"  {len(olds)} → {new_key!r}")


def write_plan_csv(plan: list[RemintMapping], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "old_key",
                "new_key",
                "tx_count",
                "flags",
                "old_label_status",
                "new_label_status",
                "collision",
            ],
        )
        w.writeheader()
        for m in plan:
            w.writerow(
                {
                    "old_key": m.old_key,
                    "new_key": m.new_key,
                    "tx_count": m.tx_count,
                    "flags": "|".join(m.flags),
                    "old_label_status": m.old_label_status,
                    "new_label_status": m.new_label_status,
                    "collision": int(m.collision),
                }
            )


def _remap_label(conn: sqlite3.Connection, old: str, new: str, stats: Counter) -> None:
    alias = conn.execute(
        "SELECT * FROM merchant_labels WHERE merchant_key = ?", (old,)
    ).fetchone()
    if not alias:
        return
    canon = conn.execute(
        "SELECT * FROM merchant_labels WHERE merchant_key = ?", (new,)
    ).fetchone()
    if not canon:
        conn.execute(
            "UPDATE merchant_labels SET merchant_key = ? WHERE merchant_key = ?",
            (new, old),
        )
        stats["labels_renamed"] += 1
        return

    alias_status = str(alias["label_status"] or "")
    canon_status = str(canon["label_status"] or "")
    prefer_alias = _status_rank(alias_status) > _status_rank(canon_status)
    if prefer_alias:
        # PRAGMA table_info: with Row factory, name is index 1 (cid is 0).
        cols = [
            str(d[1])
            for d in conn.execute("PRAGMA table_info(merchant_labels)").fetchall()
            if str(d[1]) != "merchant_key"
        ]
        set_clause = ", ".join(f'"{c}" = ?' for c in cols)
        values = [alias[c] for c in cols] + [new]
        conn.execute(
            f"UPDATE merchant_labels SET {set_clause} WHERE merchant_key = ?",
            values,
        )
        conn.execute("DELETE FROM merchant_labels WHERE merchant_key = ?", (old,))
        stats["labels_preferred_alias"] += 1
    else:
        conn.execute("DELETE FROM merchant_labels WHERE merchant_key = ?", (old,))
        stats["labels_deleted_collision"] += 1


def _remap_cadence(conn: sqlite3.Connection, old: str, new: str, stats: Counter) -> None:
    alias = conn.execute(
        "SELECT rule_id FROM cadence_rules WHERE merchant_key = ?", (old,)
    ).fetchone()
    if not alias:
        return
    canon = conn.execute(
        "SELECT rule_id FROM cadence_rules WHERE merchant_key = ?", (new,)
    ).fetchone()
    if canon:
        conn.execute("DELETE FROM cadence_rules WHERE merchant_key = ?", (old,))
        stats["cadence_deleted_collision"] += 1
    else:
        conn.execute(
            "UPDATE cadence_rules SET merchant_key = ? WHERE merchant_key = ?",
            (new, old),
        )
        stats["cadence_renamed"] += 1


def apply_plan(conn: sqlite3.Connection, plan: list[RemintMapping]) -> Counter:
    conn.row_factory = sqlite3.Row
    stats: Counter = Counter()
    for m in plan:
        old, new = m.old_key, m.new_key
        cur = conn.execute(
            "UPDATE transactions SET merchant_key = ? WHERE merchant_key = ?",
            (new, old),
        )
        stats["tx_rows_updated"] += cur.rowcount
        stats["keys_remapped"] += 1

        _remap_label(conn, old, new, stats)
        _remap_cadence(conn, old, new, stats)

        cur = conn.execute(
            "UPDATE classification_audit_findings SET merchant_key = ? WHERE merchant_key = ?",
            (new, old),
        )
        stats["audit_findings_updated"] += cur.rowcount

        cur = conn.execute(
            "UPDATE ai_insights SET merchant_key = ? WHERE merchant_key = ?",
            (new, old),
        )
        stats["ai_insights_updated"] += cur.rowcount

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S7 remint merchant_key via scrub_bank_text.")
    parser.add_argument("--db", default=None, help="Path to finance.db")
    parser.add_argument(
        "--scope",
        choices=("bank_noise", "all"),
        default="bank_noise",
        help="Which keys to remint (default: bank_noise)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run). Creates a DB backup first.",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write remint plan CSV under data/reports/",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Report/backup directory (default: data/reports)",
    )
    args = parser.parse_args(argv)

    db_path = _resolve_db(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    out_dir = (
        Path(args.out_dir).expanduser().resolve()
        if args.out_dir
        else (_REPO_ROOT / "data" / "reports")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"DB: {db_path}")
    print(f"Scope: {args.scope}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN (no writes)'}")

    # Plan always from the live DB (read).
    conn_ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        plan = build_plan(conn_ro, scope=args.scope)
    finally:
        conn_ro.close()

    if not plan:
        print("Nothing to remint for this scope.")
        return 0

    print_plan_summary(plan)

    plan_csv = out_dir / f"remint_plan_{stamp}.csv"
    if args.csv or not args.apply:
        write_plan_csv(plan, plan_csv)
        print()
        print(f"Plan CSV: {plan_csv}")

    if not args.apply:
        print()
        print("Dry-run only. Re-run with --apply to write (backup will be created).")
        return 0

    backup = out_dir / f"finance_backup_before_remint_{stamp}.db"
    shutil.copy2(db_path, backup)
    print()
    print(f"Backup: {backup}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        stats = apply_plan(conn, plan)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print()
    print("Apply complete:")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    print()
    print("Re-run S8 to verify:")
    print("  .venv/bin/python scripts/report_merchant_key_noise.py --csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
