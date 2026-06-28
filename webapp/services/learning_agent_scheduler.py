"""In-app scheduler for the Learning Agent (single-user local app)."""

from __future__ import annotations

import threading
import time
from typing import Callable

from webapp.config import DB_PATH
from webapp.services.learning_agent import (
    LEARNING_AGENT_ENABLED,
    LEARNING_AGENT_INTERVAL_HOURS,
    run_learning_agent,
)


class LearningAgentScheduler:
    def __init__(
        self,
        *,
        db_path=None,
        run_fn: Callable[[], dict] | None = None,
    ) -> None:
        self._db_path = db_path or DB_PATH
        self._run_fn = run_fn or (lambda: run_learning_agent(self._db_path))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not LEARNING_AGENT_ENABLED:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="learning-agent")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        interval_sec = LEARNING_AGENT_INTERVAL_HOURS * 3600
        while not self._stop.is_set():
            try:
                self._run_fn()
            except Exception as exc:
                print(f"Learning agent run failed: {exc}", flush=True)
            if self._stop.wait(interval_sec):
                break
