"""Shared transaction processing core (CLI and web app)."""

from transaction_insight.pipeline import PipelineConfig, PipelineResult, run_pipeline

__all__ = ["PipelineConfig", "PipelineResult", "run_pipeline"]
