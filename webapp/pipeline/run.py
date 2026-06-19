from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from openai import OpenAI

import webapp.processing as core
from webapp.config import PipelineConfig

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class PipelineResult:
    dataframe: pd.DataFrame
    new_description_entries: pd.DataFrame = field(default_factory=pd.DataFrame)
    suggested_business: pd.DataFrame = field(default_factory=pd.DataFrame)
    custom_rules_sheet: pd.DataFrame = field(default_factory=pd.DataFrame)
    cadence_review: pd.DataFrame = field(default_factory=pd.DataFrame)
    ingest_warning_count: int = 0
    stats: dict[str, Any] = field(default_factory=dict)


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def _emit_batch_progress(
    cb: ProgressCallback | None,
    *,
    phase_start: int,
    phase_end: int,
    done: int,
    total: int,
    message: str,
) -> None:
    if not cb or total <= 0:
        return
    span = max(1, phase_end - phase_start)
    pct = phase_start + int(span * done / total)
    _emit(
        cb,
        {
            "type": "progress",
            "percent": min(99, pct),
            "message": message,
        },
    )


def _phase_progress(
    cb: ProgressCallback | None,
    *,
    percent: int,
    message: str,
) -> None:
    _emit(cb, {"type": "phase", "percent": percent, "message": message})


def resolve_llm(config: PipelineConfig) -> tuple[OpenAI, str, str, bool, int, int]:
    provider, base_url, model = core.resolve_provider_config(
        config.provider,
        base_url_arg=config.base_url,
        model_arg=config.model,
        role="pipeline",
    )
    client = core.create_client(provider, base_url=base_url)
    use_json_mode = provider == "openai"
    _, description_batch_size, classification_batch_size = core.resolve_batch_sizes(
        provider, batch_size_arg=config.batch_size
    )
    return client, model, provider, use_json_mode, description_batch_size, classification_batch_size


def run_pipeline(
    df: pd.DataFrame,
    config: PipelineConfig,
    *,
    conn: sqlite3.Connection | None = None,
    on_progress: ProgressCallback | None = None,
    input_path: Path | None = None,
    timer: core.PhaseTimer | None = None,
) -> PipelineResult:
    """
    Shared categorization pipeline (descriptions → enrich → lookups → LLM review
    → business → custom rules → cadence). Does not write Excel; callers persist output.
    """
    lookup_path = config.resolved_lookup_path()
    use_db_lookups = (
        conn is not None and config.use_db_lookups() and not config.skip_lookup_update
    )

    client, model, provider, use_json_mode, description_batch_size, classification_batch_size = (
        resolve_llm(config)
    )

    description_lookup_map: dict[str, str] = {}
    new_description_entries = pd.DataFrame(columns=list(core.DESCRIPTION_LOOKUP_COLUMNS))
    lookups_workbook: dict[str, pd.DataFrame] = {}
    stats: dict[str, Any] = {"provider": provider, "model": model}

    with core.PhaseTimer.track(timer, "Load lookups"):
        _phase_progress(on_progress, percent=0, message="Loading lookups")
        if not config.skip_lookup_update:
            if use_db_lookups:
                from webapp.adapters.lookup_store import (
                    ensure_lookups_seeded,
                    load_lookup_workbook_from_db,
                )

                ensure_lookups_seeded(conn, lookup_path)
                lookups_workbook = load_lookup_workbook_from_db(conn)
            else:
                lookups_workbook = core.load_lookup_workbook(lookup_path)
            if not config.rebuild_description_lookup:
                description_lookup_map = core.build_description_lookup_map(lookups_workbook)
                stats["description_lookup_entries"] = len(description_lookup_map)

    _phase_progress(on_progress, percent=2, message="Generating descriptions")

    def _description_batch_progress(done: int, total: int, message: str) -> None:
        _emit_batch_progress(
            on_progress,
            phase_start=2,
            phase_end=32,
            done=done,
            total=total,
            message=message,
        )

    with core.PhaseTimer.track(timer, "Descriptions (LLM)"):
        df, new_description_entries = core.fill_generated_descriptions(
            df,
            client,
            model,
            batch_size=description_batch_size,
            use_json_mode=use_json_mode,
            description_lookup=description_lookup_map if not config.skip_lookup_update else None,
            rebuild_lookup=config.rebuild_description_lookup,
            on_batch_progress=_description_batch_progress,
        )
    stats["new_description_entries"] = len(new_description_entries)

    with core.PhaseTimer.track(timer, "Enrichment & lookup rules"):
        if lookups_workbook:
            n_biz_marked = core.mark_business_from_category_rules(df, lookups_workbook)
            stats["business_marked"] = n_biz_marked

        _phase_progress(on_progress, percent=34, message="Enriching dates and flow types")
        df = core.assign_transaction_ids(df)

        ingest_warning_count = 0
        if input_path is not None:
            try:
                with open(input_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                    total_lines = sum(1 for _ in f)
                ingest_warning_count = max(0, total_lines - 1 - len(df))
            except OSError:
                ingest_warning_count = 0

        df, date_dropped = core.drop_rows_with_invalid_dates(df)
        ingest_warning_count += date_dropped
        df = df.sort_values("Transaction Date").reset_index(drop=True)

        import calendar

        df["Calendar Month"] = df["Transaction Date"].dt.to_period("M").astype(str)
        df["Budget Month"] = df["Calendar Month"]
        df["Income Attribution Month"] = df["Calendar Month"]
        df["Payroll Spillover"] = "N"

        df = core.ensure_merchant_key_column(df)
        df["AI Category"] = df["Category"].fillna("")
        df["AI Sub-Category"] = ""
        df["Type"] = df["Category"].apply(core.cost_type_from_category)
        df["Sub-Type"] = ""

        pay_mask = df.apply(core.is_paycheck_row, axis=1)
        for i in df[pay_mask].index:
            d = df.at[i, "Transaction Date"]
            if pd.isna(d):
                continue
            last_day = calendar.monthrange(d.year, d.month)[1]
            tail_start = last_day - core.PAYROLL_SPILLOVER_DAYS + 1
            if d.day >= tail_start:
                next_month = str(
                    (d.replace(day=1) + pd.offsets.MonthBegin(1)).to_period("M")
                )
                df.at[i, "Budget Month"] = next_month
                df.at[i, "Income Attribution Month"] = next_month
                df.at[i, "Payroll Spillover"] = "Y"

        df["Flow Type"] = df.apply(core.flow_type_from_row, axis=1)
        df["Include in Spend?"] = "N"
        spend_mask = (df["Flow Type"] == "Expense") & (df["Amount_Numeric"] < 0)
        df.loc[spend_mask, "Include in Spend?"] = "Y"
        df["Budget Tier"] = "N/A"

        _phase_progress(on_progress, percent=38, message="Applying lookup rules")
        if not config.skip_lookup_update:
            if not lookups_workbook:
                if use_db_lookups and conn is not None:
                    from webapp.adapters.lookup_store import load_lookup_workbook_from_db

                    lookups_workbook = load_lookup_workbook_from_db(conn)
                else:
                    lookups_workbook = core.load_lookup_workbook(lookup_path)
            if lookups_workbook:
                stats["lookup_rows_touched"] = core.apply_lookup_rules(
                    df, lookups_workbook, spend_mask=spend_mask
                )
                stats["merchant_lookup_rows"] = core.apply_merchant_category_lookup(
                    df, lookups_workbook, spend_mask=spend_mask
                )

            needs_tier = spend_mask & (
                df["Budget Tier"].isin(["N/A", ""]) | df["Budget Tier"].isna()
            )
            df.loc[needs_tier, "Budget Tier"] = df.loc[needs_tier, "AI Category"].apply(
                core.budget_tier_from_category
            )

    _phase_progress(on_progress, percent=40, message="AI classification (review rows)")

    def _classification_batch_progress(done: int, total: int, message: str) -> None:
        _emit_batch_progress(
            on_progress,
            phase_start=40,
            phase_end=78,
            done=done,
            total=total,
            message=message,
        )

    with core.PhaseTimer.track(timer, "AI classification (LLM)"):
        df = core.classify_review_rows(
            df,
            client,
            model,
            batch_size=classification_batch_size,
            use_json_mode=use_json_mode,
            on_batch_progress=_classification_batch_progress,
        )
        needs_tier_post = spend_mask & (
            df["Budget Tier"].isin(["N/A", "", "Review"]) | df["Budget Tier"].isna()
        )
        df.loc[needs_tier_post, "Budget Tier"] = df.loc[needs_tier_post, "AI Category"].apply(
            core.budget_tier_from_category
        )

    suggested_business = pd.DataFrame()
    with core.PhaseTimer.track(timer, "Business rules"):
        _phase_progress(on_progress, percent=80, message="Business rules")
        if not config.skip_lookup_update:
            if not lookups_workbook:
                if use_db_lookups and conn is not None:
                    from webapp.adapters.lookup_store import load_lookup_workbook_from_db

                    lookups_workbook = load_lookup_workbook_from_db(conn)
                else:
                    lookups_workbook = core.load_lookup_workbook(lookup_path)
            existing_business = lookups_workbook.get("BusinessCategoryRules")
            core.apply_business_rules_to_df(
                df,
                existing_business if existing_business is not None else pd.DataFrame(),
                spend_mask=spend_mask,
            )
            suggested_business = core.enrich_business_lookup_rules(
                df,
                client,
                model,
                lookups_workbook,
                batch_size=classification_batch_size,
                use_json_mode=use_json_mode,
            )
            stats["business_rules_applied"] = core.apply_business_rules_to_df(
                df, suggested_business, spend_mask=spend_mask
            )

    custom_rules_sheet = core.empty_custom_rules_sheet()
    active_custom_rules: list[dict[str, Any]] = []
    with core.PhaseTimer.track(timer, "Custom rules"):
        _phase_progress(on_progress, percent=84, message="Custom rules")
        if not lookups_workbook and lookup_path.exists() and not use_db_lookups:
            lookups_workbook = core.load_lookup_workbook(lookup_path)
        elif not lookups_workbook and use_db_lookups and conn is not None:
            from webapp.adapters.lookup_store import load_lookup_workbook_from_db

            lookups_workbook = load_lookup_workbook_from_db(conn)
        if lookups_workbook:
            custom_rules_sheet = core.normalize_custom_rules_sheet(
                lookups_workbook.get(core.CUSTOM_RULES_SHEET)
            )
        if not custom_rules_sheet.empty and not config.skip_lookup_update:
            custom_rules_sheet = core.compile_custom_rules_sheet(
                custom_rules_sheet,
                client,
                model,
                use_json_mode=use_json_mode,
            )
            active_custom_rules = core.load_active_custom_rules(custom_rules_sheet)
            stats["active_custom_rules"] = len(active_custom_rules)

    with core.PhaseTimer.track(timer, "Expense cadence"):
        _phase_progress(on_progress, percent=88, message="Expense cadence")
        core.init_expense_cadence_columns(df)
        cadence_rules_sheet = core.normalize_expense_cadence_rules_sheet(
            lookups_workbook.get(core.EXPENSE_CADENCE_RULES_SHEET) if lookups_workbook else None
        )
        stats["cadence_lookup"] = core.apply_expense_cadence_lookup(
            df, cadence_rules_sheet, spend_mask=spend_mask
        )

        if not config.skip_cadence_detection:
            analytics_ledger = core.build_analytics_ledger(df)
            profiles = core.analyze_merchant_cadence_profiles(analytics_ledger)
            if profiles:
                stats["cadence_detected"] = core.apply_detected_expense_cadence(
                    df, profiles, spend_mask=spend_mask
                )
        core.finalize_expense_cadence_defaults(df, spend_mask=spend_mask)
        cadence_review_df = core.build_cadence_review_df(df)

        _phase_progress(on_progress, percent=92, message="Applying custom rules (final pass)")
        if active_custom_rules:
            stats["custom_rules_applied"] = core.apply_custom_rules(df, active_custom_rules)

    with core.PhaseTimer.track(timer, "Save lookups"):
        if config.update_lookup_workbook and not config.skip_lookup_update:
            _phase_progress(on_progress, percent=95, message="Saving lookups")
            if use_db_lookups and conn is not None:
                from webapp.adapters.lookup_store import save_lookup_workbook_to_db

                save_lookup_workbook_to_db(
                    conn,
                    df,
                    lookup_path=lookup_path,
                    client=client,
                    model=model,
                    batch_size=classification_batch_size,
                    use_json_mode=use_json_mode,
                    suggested_business=suggested_business,
                    new_description_entries=new_description_entries,
                    rebuild_description_lookup=config.rebuild_description_lookup,
                    custom_rules_sheet=custom_rules_sheet,
                )
            else:
                core.update_lookup_workbook(
                    df,
                    lookup_path,
                    client=client,
                    model=model,
                    batch_size=classification_batch_size,
                    use_json_mode=use_json_mode,
                    suggested_business=suggested_business,
                    new_description_entries=new_description_entries,
                    rebuild_description_lookup=config.rebuild_description_lookup,
                    custom_rules_sheet=custom_rules_sheet,
                )

    _emit(
        on_progress,
        {"type": "done", "percent": 100, "message": "Pipeline complete", "stats": stats},
    )

    return PipelineResult(
        dataframe=df,
        new_description_entries=new_description_entries,
        suggested_business=suggested_business,
        custom_rules_sheet=custom_rules_sheet,
        cadence_review=cadence_review_df,
        ingest_warning_count=ingest_warning_count,
        stats=stats,
    )
