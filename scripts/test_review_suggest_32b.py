#!/usr/bin/env python3
"""Smoke-test review suggest with qwen2.5-coder:32b (lookup-first, then LLM)."""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["CHAT_MODEL"] = "qwen2.5-coder:32b"
os.environ.setdefault("LLM_PROVIDER", "ollama")

import webapp.config as cfg

importlib.reload(cfg)

from webapp.config import CHAT_MODEL, DB_PATH, LLM_MODEL
from webapp.db.schema import get_connection
from webapp.services.categorize import list_review_items
from webapp.services.review_suggest import suggest_labels_for_merchant

TEST_MERCHANTS = 3


def main() -> int:
    print(f"Model: {LLM_MODEL} (CHAT_MODEL={CHAT_MODEL})")
    conn = get_connection(DB_PATH)
    try:
        items = list_review_items(conn)
        if not items:
            print("No review queue items — nothing to test.")
            return 1

        batch = items[:TEST_MERCHANTS]
        print(f"Testing {len(batch)} merchant(s) from queue of {len(items)}…\n")

        results = []
        t0 = time.perf_counter()
        for i, item in enumerate(batch, start=1):
            mk = item["merchant_key"]
            tx_id = item.get("transaction_id")
            print(f"[{i}/{len(batch)}] {mk} …", flush=True)
            start = time.perf_counter()
            try:
                sug = suggest_labels_for_merchant(
                    conn,
                    mk,
                    transaction_id=tx_id,
                )
                elapsed = time.perf_counter() - start
                labels = sug.get("labels") or {}
                row = {
                    "merchant_key": mk,
                    "source": sug.get("source"),
                    "confidence": sug.get("confidence"),
                    "rationale": sug.get("rationale"),
                    "labels": labels,
                    "seconds": round(elapsed, 1),
                }
                results.append(row)
                print(
                    f"    {elapsed:.1f}s · {row['source']} · {row['confidence']} · "
                    f"{labels.get('ai_category')} / {labels.get('ai_sub_category')}"
                )
                print(f"    → {row['rationale']}")
            except Exception as exc:
                elapsed = time.perf_counter() - start
                print(f"    ERROR ({elapsed:.1f}s): {exc}")
                results.append({"merchant_key": mk, "error": str(exc), "seconds": round(elapsed, 1)})

        total = time.perf_counter() - t0
        lookup = sum(
            1
            for r in results
            if r.get("source")
            in ("MerchantCategories", "BusinessCategoryRules", "CustomRules", "merchant_labels")
        )
        llm = sum(1 for r in results if r.get("source") == "llm")
        print(f"\nDone in {total:.1f}s — {lookup} lookup, {llm} LLM")
        print(json.dumps(results, indent=2))
        return 0 if all("error" not in r for r in results) else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
