#!/usr/bin/env python3
"""
CLI entry point. Processing logic lives in transaction_insight.core / pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from transaction_insight.core import main

if __name__ == "__main__":
    raise SystemExit(main())
