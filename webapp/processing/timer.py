from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


def format_duration(seconds: float) -> str:
    """Human-readable duration for console output."""
    if seconds < 0.001:
        return "<1 ms"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {secs:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {secs:.0f}s"

class PhaseTimer:
    """Collect per-phase wall times and print a summary at the end of a run."""

    def __init__(self) -> None:
        self._run_start = time.perf_counter()
        self.phases: list[tuple[str, float]] = []

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        yield
        self.phases.append((name, time.perf_counter() - t0))

    @staticmethod
    @contextmanager
    def track(timer: PhaseTimer | None, name: str) -> Iterator[None]:
        """Record a named phase when *timer* is set; no-op otherwise."""
        if timer is None:
            yield
        else:
            with timer.phase(name):
                yield

    def total_seconds(self) -> float:
        return time.perf_counter() - self._run_start

    def print_summary(self) -> None:
        total = self.total_seconds()
        phase_sum = sum(d for _, d in self.phases)
        llm_phase_names = {"Descriptions (LLM)", "AI classification (LLM)"}
        print("\nTiming summary")
        print("─" * 52)
        name_width = max((len(n) for n, _ in self.phases), default=20)
        for name, elapsed in self.phases:
            if elapsed < 0.05 and name not in llm_phase_names:
                continue
            pct = (elapsed / total * 100) if total > 0 else 0
            print(
                f"  {name:<{name_width}}  {format_duration(elapsed):>10}  ({pct:4.1f}%)"
            )
        llm_total = sum(d for n, d in self.phases if n in llm_phase_names)
        if llm_total > 0 and len(llm_phase_names.intersection(n for n, _ in self.phases)) > 1:
            pct = (llm_total / total * 100) if total > 0 else 0
            print("─" * 52)
            print(
                f"  {'LLM total':<{name_width}}  {format_duration(llm_total):>10}  ({pct:4.1f}%)"
            )
        print("─" * 52)
        print(f"  {'Total':<{name_width}}  {format_duration(total):>10}  (100.0%)")
        other = total - phase_sum
        if other > 0.05:
            print(
                f"  (Phases account for {format_duration(phase_sum)}; "
                f"setup/print overhead {format_duration(other)})"
            )
