"""CLI entry point for scheduled classification audit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(os.getenv("FINANCE_DB_PATH", str(_ROOT / "data" / "finance.db")))

from webapp.services.classification_audit import run_scheduled_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run scheduled classification audit (sampled merchants, local LLM)."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB),
        help="Path to finance.db",
    )
    args = parser.parse_args(argv)
    result = run_scheduled_audit(args.db)
    print(json.dumps(result, indent=2))
    if result.get("skipped"):
        return 0
    return 0 if result.get("llm_errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
