from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from webapp.agent.chat import chat
from webapp.agent.chat_history import export_chat_history, list_chat_history, list_chat_history_page
from webapp.config import (
    CHAT_MODEL,
    DB_PATH,
    INBOX_DIR,
    PROCESSED_DIR,
    LLM_BASE_URL,
    LLM_PROVIDER,
    PIPELINE_MODEL,
    STATIC_DIR,
    UI_SHOW_CADENCE,
)
from webapp.db.schema import get_connection, init_db
from webapp.services.categorize import (
    list_merchant_transactions,
    list_review_items,
)
from webapp.services.review_confirm import confirm_merchant_or_transaction, confirm_preview
from webapp.services.review_suggest import (
    REVIEW_SUGGEST_BATCH_LIMITS,
    suggest_labels_bulk,
    suggest_labels_for_merchant,
)
from webapp.services.process import list_inbox_csv_paths, process_inbox_files
from webapp.services.classification_audit import (
    audit_summary,
    dismiss_finding,
    list_findings,
    open_merchant_payload,
    run_classification_audit,
    schedule_post_import_audit,
)
from webapp.services.review_options import get_review_options
from webapp.services.data_store import clear_data_store, table_counts
from webapp.services.inbox_upload import save_upload_to_inbox, scan_uploaded_files
from webapp.services.ingest import ingest_csv, scan_inbox
from webapp.services.custom_rules import (
    add_custom_rule,
    apply_custom_rule_by_id,
    compile_and_apply_custom_rules,
    delete_custom_rule,
    get_custom_rule,
    list_custom_rules,
    preview_custom_rule,
    save_and_apply_custom_rule,
    update_custom_rule,
)
from webapp.services.cadence_insights import propose_cadence
from webapp.services.edit_cadence_suggest import (
    CADENCE_SUGGEST_BATCH_LIMITS,
    CadenceSuggestFilters,
    count_merchants_needing_cadence,
    list_merchants_needing_cadence,
    suggest_cadence_bulk,
)
from webapp.services.edit_insights import analyze_edit
from webapp.services.expense_cadence import (
    effective_amount,
    get_cadence_rule,
    list_cadence_rules,
    resolve_effective_cadence,
    upsert_cadence_rule,
)
from webapp.services.transaction_edit import (
    bulk_update_labels,
    get_transaction,
    list_matching_transactions,
    search_transactions,
)
from webapp.services.taxonomy_rules import (
    analyze_taxonomy,
    apply_taxonomy_proposals,
    preview_taxonomy_proposals,
    suggest_taxonomy_proposals_with_llm,
)
from webapp.services import custom_reports as custom_report_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    from webapp.llm.request_log import setup_llm_logging

    setup_llm_logging()
    init_db(DB_PATH)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Transaction Insight", lifespan=lifespan)


def _conn() -> sqlite3.Connection:
    return get_connection(DB_PATH)


class ChatRequest(BaseModel):
    message: str


class ReviewConfirmRequest(BaseModel):
    ai_category: str
    ai_sub_category: str = ""
    expense_type: str = "Variable"
    flow_type: str = "Expense"
    classification: str = "Personal"
    transaction_id: str | None = None
    scope: str = "pending"
    replace_conflicting_rule: bool = False


class ReviewConfirmPreviewRequest(BaseModel):
    ai_category: str
    ai_sub_category: str = ""
    expense_type: str = "Variable"
    flow_type: str = "Expense"
    classification: str = "Personal"


class ReviewSuggestRequest(BaseModel):
    transaction_id: str | None = None


class ReviewSuggestBatchRequest(BaseModel):
    limit: int = Field(10, ge=10, le=100)


class IngestRequest(BaseModel):
    filename: str | None = None


class ClearDataRequest(BaseModel):
    confirm: str


class TaxonomyProposalsRequest(BaseModel):
    proposals: list[dict[str, Any]] = Field(min_length=1)
    reconcile: bool = False
    sample_limit: int | None = None


class TaxonomyApplyRequest(BaseModel):
    proposals: list[dict[str, Any]] = Field(min_length=1)
    reconcile: bool = False
    confirm: str = ""


class CustomRuleCreateRequest(BaseModel):
    rule: str = Field(min_length=1)


class CustomRuleUpdateRequest(BaseModel):
    rule: str | None = None
    status: str | None = None


class CustomRulePreviewRequest(BaseModel):
    rule_text: str | None = None
    rule_id: int | None = None
    limit: int = 50
    offset: int = 0


class TransactionCadencePayload(BaseModel):
    cadence_kind: str = Field(min_length=1)
    period_count: int | None = Field(default=None, ge=1)
    period_unit: str | None = None
    include_in_run_rate: bool | None = None
    cadence_note: str = ""


class TransactionBulkLabelRequest(BaseModel):
    transaction_ids: list[str] = Field(min_length=1)
    ai_category: str = Field(min_length=1)
    ai_sub_category: str = ""
    flow_type: str | None = None
    expense_type: str | None = None
    classification: str | None = None
    new_merchant_key: str = Field(min_length=1)
    update_merchant_label: bool = False
    merchant_key: str | None = None
    cadence: TransactionCadencePayload | None = None
    cadence_scope: str = "transaction"


class EditLabelsSnapshot(BaseModel):
    ai_category: str = ""
    ai_sub_category: str = ""
    expense_type: str = ""
    classification: str = ""


class EditInsightRequest(BaseModel):
    merchant_key: str = Field(min_length=1)
    scope: str = "single"
    rows_updated: int = Field(ge=1)
    before: EditLabelsSnapshot
    after: EditLabelsSnapshot
    amount: float | None = None
    update_merchant_label: bool = False


class CadenceRuleRequest(BaseModel):
    merchant_key: str = Field(min_length=1)
    cadence_kind: str = Field(min_length=1)
    period_count: int | None = Field(default=None, ge=1)
    period_unit: str | None = None
    include_in_run_rate: bool | None = None
    notes: str = ""
    enabled: bool = True


class CadenceProposeRequest(BaseModel):
    merchant_key: str | None = None
    transaction_id: str | None = None
    hint: str = ""


class CadenceProposeBatchRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class CadenceSuggestBatchRequest(BaseModel):
    limit: int = Field(default=10, ge=10, le=50)
    q: str = ""
    month: str = ""
    category: str = ""
    sub_category: str = ""
    expense_type: str = ""
    classification: str = ""


def _cadence_suggest_filters_from_request(
    *,
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    classification: str = "",
) -> CadenceSuggestFilters:
    return CadenceSuggestFilters(
        q=q,
        month=month,
        category=category,
        sub_category=sub_category,
        expense_type=expense_type,
        classification=classification,
    )


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    conn = _conn()
    try:
        tx_count = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]
        review_count = conn.execute(
            "SELECT COUNT(DISTINCT merchant_key) AS c FROM transactions WHERE label_status IN ('needs_review', 'pending')"
        ).fetchone()["c"]
        months = conn.execute(
            "SELECT DISTINCT budget_month FROM transactions ORDER BY budget_month DESC"
        ).fetchall()
        counts = table_counts(conn)
        inbox_csv_files = sorted(p.name for p in INBOX_DIR.glob("*.csv"))
        return {
            "db_path": str(DB_PATH),
            "inbox_dir": str(INBOX_DIR),
            "processed_dir": str(PROCESSED_DIR),
            "inbox_csv_files": inbox_csv_files,
            "lookup_source": "sqlite",
            "llm_provider": LLM_PROVIDER,
            "llm_base_url": LLM_BASE_URL,
            "llm_model": CHAT_MODEL,
            "pipeline_model": PIPELINE_MODEL,
            "chat_model": CHAT_MODEL,
            "transaction_count": tx_count,
            "review_merchant_count": review_count,
            "months": [r["budget_month"] for r in months],
            "table_counts": counts,
            "ui_show_cadence": UI_SHOW_CADENCE,
        }
    finally:
        conn.close()


async def _save_uploaded_csvs(
    files: list[UploadFile],
) -> tuple[list[dict[str, Any]], list[Path]]:
    if not files:
        raise HTTPException(400, "Select at least one CSV file")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    uploads: list[dict[str, Any]] = []
    saved_paths: list[Path] = []

    for upload in files:
        raw_name = upload.filename or "upload.csv"
        try:
            content = await upload.read()
            saved = save_upload_to_inbox(
                INBOX_DIR,
                filename=raw_name,
                content=content,
            )
            uploads.append({**saved, "ok": True})
            saved_paths.append(INBOX_DIR / saved["saved_as"])
        except ValueError as exc:
            uploads.append({"original_name": raw_name, "ok": False, "error": str(exc)})
        except Exception as exc:
            uploads.append({"original_name": raw_name, "ok": False, "error": str(exc)})

    return uploads, saved_paths


@app.post("/api/ingest/upload")
async def api_ingest_upload(
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """Copy CSV uploads into the inbox; Run processing enriches and saves to SQLite."""
    uploads, saved_paths = await _save_uploaded_csvs(files)
    if not saved_paths:
        return {"uploads": uploads}
    return {"uploads": uploads, "saved_count": len(saved_paths)}


@app.post("/api/ingest/scan")
def api_ingest_scan(force: bool = False) -> dict[str, Any]:
    """Legacy raw CSV import without pipeline categorization."""
    conn = _conn()
    try:
        return {"results": scan_inbox(conn, INBOX_DIR, force=force)}
    finally:
        conn.close()


@app.post("/api/ingest/upload-and-scan")
async def api_ingest_upload_and_scan(
    files: list[UploadFile] = File(...),
    force: bool = False,
) -> dict[str, Any]:
    """Legacy: upload then raw ingest. Prefer /api/ingest/upload + /api/process."""
    uploads, saved_paths = await _save_uploaded_csvs(files)
    if not saved_paths:
        return {"uploads": uploads, "results": []}

    conn = _conn()
    try:
        results = scan_uploaded_files(conn, saved_paths, force=force)
        return {"uploads": uploads, "results": results}
    finally:
        conn.close()


@app.post("/api/ingest/file")
def api_ingest_file(body: IngestRequest) -> dict[str, Any]:
    if not body.filename:
        raise HTTPException(400, "filename required")
    path = INBOX_DIR / body.filename
    if not path.is_file():
        raise HTTPException(404, f"Not found in inbox: {body.filename}")
    conn = _conn()
    try:
        return ingest_csv(conn, path, force=False)
    finally:
        conn.close()


class ProcessRequest(BaseModel):
    filename: str | None = None
    skip_lookup_update: bool = False
    save_lookups: bool = True


class CustomReportFinalizeRequest(BaseModel):
    sql_template: str = Field(min_length=1)
    conversation_summary: str = ""
    original_question: str = ""
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)


class CustomReportSaveRequest(BaseModel):
    name: str = Field(min_length=1)
    sql_template: str = Field(min_length=1)
    description: str = ""
    original_question: str = ""
    report_prompt: str = ""
    report_config: dict[str, Any] = Field(default_factory=dict)
    parameters: list[str] | None = None
    parent_report_id: str | None = None
    auto_finalize: bool = False
    conversation_summary: str = ""
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)


class CustomReportUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    sql_template: str | None = None
    report_prompt: str | None = None
    report_config: dict[str, Any] | None = None
    original_question: str | None = None
    parameters: list[str] | None = None


class CustomReportForkRequest(BaseModel):
    new_name: str = Field(min_length=1)
    description: str | None = None
    sql_template: str | None = None
    report_prompt: str | None = None
    report_config: dict[str, Any] | None = None


class CustomReportRunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    max_rows: int = Field(500, ge=1, le=2000)


@app.post("/api/process")
def api_process(body: ProcessRequest | None = None) -> dict[str, Any]:
    body = body or ProcessRequest()
    try:
        paths = list_inbox_csv_paths(INBOX_DIR, filename=body.filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not paths:
        raise HTTPException(400, "No CSV files in input/")
    conn = _conn()
    try:
        results = process_inbox_files(
            conn,
            paths,
            skip_lookup_update=body.skip_lookup_update,
            skip_cadence_detection=True,
            save_lookups=body.save_lookups,
        )
        schedule_post_import_audit([r.get("file", "") for r in results if r.get("file")])
        return {
            "file_count": len(results),
            "results": results,
            "message": f"Processed {len(results)} file(s).",
        }
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/process/stream")
def api_process_stream(
    filename: str | None = None,
    skip_lookup_update: bool = False,
    save_lookups: bool = True,
) -> StreamingResponse:
    """SSE progress for shared CLI categorization pipeline."""

    import queue
    import threading

    try:
        paths = list_inbox_csv_paths(INBOX_DIR, filename=filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not paths:
        raise HTTPException(400, "No CSV files in input/")

    def generate():
        event_q: queue.Queue[dict[str, Any] | None] = queue.Queue()
        result_box: dict[str, Any] = {}
        error_box: dict[str, str] = {}

        def worker() -> None:
            conn = _conn()
            try:

                def on_progress(ev: dict[str, Any]) -> None:
                    event_q.put(ev)

                results = process_inbox_files(
                    conn,
                    paths,
                    skip_lookup_update=skip_lookup_update,
                    skip_cadence_detection=True,
                    save_lookups=save_lookups,
                    on_progress=on_progress,
                )
                result_box["data"] = {
                    "file_count": len(results),
                    "results": results,
                    "message": f"Processed {len(results)} file(s).",
                }
                schedule_post_import_audit(
                    [r.get("file", "") for r in results if r.get("file")]
                )
            except Exception as exc:
                error_box["message"] = str(exc)
            finally:
                conn.close()
                event_q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            ev = event_q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev)}\n\n"

        if error_box:
            yield f"data: {json.dumps({'type': 'error', 'message': error_box['message']})}\n\n"
        elif result_box:
            payload = {
                "type": "done",
                "file_count": result_box["data"]["file_count"],
                "results": result_box["data"]["results"],
                "message": result_box["data"]["message"],
            }
            if result_box["data"]["file_count"] == 1:
                payload["result"] = result_box["data"]["results"][0]
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/classification-audit/summary")
def api_classification_audit_summary() -> dict[str, Any]:
    conn = _conn()
    try:
        return audit_summary(conn)
    finally:
        conn.close()


@app.get("/api/classification-audit/findings")
def api_classification_audit_findings(status: str = "open") -> list[dict[str, Any]]:
    conn = _conn()
    try:
        return list_findings(conn, status=status)
    finally:
        conn.close()


class ClassificationAuditRunRequest(BaseModel):
    sample_size: int | None = None


@app.post("/api/classification-audit/run")
def api_classification_audit_run(
    body: ClassificationAuditRunRequest | None = None,
) -> dict[str, Any]:
    body = body or ClassificationAuditRunRequest()
    conn = _conn()
    try:
        return run_classification_audit(
            conn,
            run_type="manual",
            sample_size=body.sample_size,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/classification-audit/findings/{finding_id}/dismiss")
def api_classification_audit_dismiss(finding_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        ok = dismiss_finding(conn, finding_id)
        if not ok:
            raise HTTPException(404, "Open finding not found")
        return {"dismissed": True, "id": finding_id}
    finally:
        conn.close()


@app.get("/api/classification-audit/findings/{finding_id}/open-merchant")
def api_classification_audit_open_merchant(finding_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            return open_merchant_payload(conn, finding_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/review/options")
def api_review_options() -> dict[str, Any]:
    conn = _conn()
    try:
        return get_review_options(conn)
    finally:
        conn.close()


@app.get("/api/review")
def api_review() -> list[dict[str, Any]]:
    conn = _conn()
    try:
        return list_review_items(conn)
    finally:
        conn.close()


@app.get("/api/review/{merchant_key}/transactions")
def api_review_transactions(merchant_key: str) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        return list_merchant_transactions(conn, merchant_key, review_only=True)
    finally:
        conn.close()


@app.post("/api/review/suggest-batch")
def api_review_suggest_batch(body: ReviewSuggestBatchRequest | None = None) -> dict[str, Any]:
    body = body or ReviewSuggestBatchRequest()
    if body.limit not in REVIEW_SUGGEST_BATCH_LIMITS:
        raise HTTPException(400, "limit must be one of: 10, 25, 50, 100")
    conn = _conn()
    try:
        try:
            return suggest_labels_bulk(conn, limit=body.limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/review/{merchant_key}/suggest-labels")
def api_review_suggest_labels(
    merchant_key: str, body: ReviewSuggestRequest | None = None
) -> dict[str, Any]:
    body = body or ReviewSuggestRequest()
    conn = _conn()
    try:
        try:
            return suggest_labels_for_merchant(
                conn,
                merchant_key,
                transaction_id=body.transaction_id,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/cadence-rules")
def api_cadence_rules_list() -> dict[str, Any]:
    conn = _conn()
    try:
        return {"rules": list_cadence_rules(conn)}
    finally:
        conn.close()


@app.post("/api/cadence-rules/propose")
def api_cadence_rules_propose(body: CadenceProposeRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            return propose_cadence(
                conn,
                merchant_key=body.merchant_key,
                transaction_id=body.transaction_id,
                hint=body.hint,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/cadence-rules/propose-batch")
def api_cadence_rules_propose_batch(body: CadenceProposeBatchRequest | None = None) -> dict[str, Any]:
    body = body or CadenceProposeBatchRequest()
    mapped = body.limit
    if mapped not in CADENCE_SUGGEST_BATCH_LIMITS:
        mapped = min(
            (x for x in sorted(CADENCE_SUGGEST_BATCH_LIMITS) if x >= mapped),
            default=max(CADENCE_SUGGEST_BATCH_LIMITS),
        )
    conn = _conn()
    try:
        result = suggest_cadence_bulk(conn, limit=mapped)
        return {
            "count": result.get("suggestion_count", 0),
            "proposals": result.get("suggestions", []),
        }
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/cadence-rules")
def api_cadence_rules_upsert(body: CadenceRuleRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            rule = upsert_cadence_rule(
                conn,
                merchant_key=body.merchant_key,
                cadence_kind=body.cadence_kind,
                period_count=body.period_count,
                period_unit=body.period_unit,
                include_in_run_rate=body.include_in_run_rate,
                notes=body.notes,
                source="user",
                enabled=body.enabled,
            )
            conn.commit()
            return {"rule": rule}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/cadence-rules/{merchant_key}")
def api_cadence_rule_get(merchant_key: str) -> dict[str, Any]:
    conn = _conn()
    try:
        rule = get_cadence_rule(conn, merchant_key)
        if not rule:
            raise HTTPException(404, "Cadence rule not found")
        return rule
    finally:
        conn.close()


@app.get("/api/transactions/{transaction_id}/cadence")
def api_transaction_cadence(
    transaction_id: str,
    cadence_kind: str | None = None,
    period_count: int | None = None,
    period_unit: str | None = None,
    include_in_run_rate: bool | None = None,
) -> dict[str, Any]:
    conn = _conn()
    try:
        tx = get_transaction(conn, transaction_id)
        if not tx:
            raise HTTPException(404, "Transaction not found")
        resolved = resolve_effective_cadence(conn, transaction_id=transaction_id, tx_row=tx)
        preview = cadence_kind is not None and str(cadence_kind).strip() != ""
        if preview:
            effective = {
                "cadence_kind": cadence_kind,
                "period_count": period_count,
                "period_unit": period_unit,
                "include_in_run_rate": include_in_run_rate,
                "cadence_source": "preview",
                "cadence_note": "",
                "layer": "preview",
            }
        else:
            effective = resolved
        amount = float(tx.get("amount") or 0)
        views = {
            view: effective_amount(
                amount,
                view=view,
                kind=effective["cadence_kind"],
                period_count=effective.get("period_count"),
                period_unit=effective.get("period_unit"),
                include_in_run_rate=effective.get("include_in_run_rate"),
            )
            for view in ("cash", "core", "normalized")
        }
        return {
            "transaction_id": transaction_id,
            "merchant_key": tx.get("merchant_key"),
            "amount": amount,
            "effective_cadence": effective,
            "resolved_cadence": resolved,
            "effective_amounts": views,
            "preview": preview,
        }
    finally:
        conn.close()


@app.get("/api/custom-rules")
def api_custom_rules_list() -> dict[str, Any]:
    conn = _conn()
    try:
        return list_custom_rules(conn)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-rules")
def api_custom_rules_add(body: CustomRuleCreateRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        return add_custom_rule(conn, body.rule)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-rules/preview")
def api_custom_rules_preview(body: CustomRulePreviewRequest) -> dict[str, Any]:
    if not (body.rule_text or "").strip() and body.rule_id is None:
        raise HTTPException(400, "rule_text or rule_id is required")
    conn = _conn()
    try:
        return preview_custom_rule(
            conn,
            rule_text=body.rule_text,
            rule_id=body.rule_id,
            limit=body.limit,
            offset=body.offset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/custom-rules/{rule_id}")
def api_custom_rules_get(rule_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        return get_custom_rule(conn, rule_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.put("/api/custom-rules/{rule_id}")
def api_custom_rules_update(rule_id: int, body: CustomRuleUpdateRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        return update_custom_rule(conn, rule_id, rule_text=body.rule, status=body.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.delete("/api/custom-rules/{rule_id}")
def api_custom_rules_delete(rule_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        return delete_custom_rule(conn, rule_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-rules/{rule_id}/apply")
def api_custom_rules_apply_one(rule_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        result = apply_custom_rule_by_id(conn, rule_id)
        if not result.get("ok"):
            raise HTTPException(400, result.get("message", "Apply failed"))
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-rules/compile-apply")
def api_custom_rules_compile_apply() -> dict[str, Any]:
    conn = _conn()
    try:
        result = compile_and_apply_custom_rules(conn)
        rows = int(result.get("rows_updated") or 0)
        errors = result.get("compile_errors") or []
        if errors:
            err_text = errors[0].get("error", "Unknown error")
            result["ok"] = False
            result["message"] = f"Could not apply rules: {err_text}"
        else:
            result["ok"] = True
            result["message"] = f"Updated {rows} transaction(s)."
        listing = list_custom_rules(conn)
        result["rules"] = listing.get("rules") or []
        return result
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-rules/save-apply")
def api_custom_rules_save_apply(body: CustomRuleCreateRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        return save_and_apply_custom_rule(conn, body.rule)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/transactions/cadence-suggest-count")
def api_transactions_cadence_suggest_count(
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    classification: str = "",
) -> dict[str, Any]:
    conn = _conn()
    try:
        filters = _cadence_suggest_filters_from_request(
            q=q,
            month=month,
            category=category,
            sub_category=sub_category,
            expense_type=expense_type,
            classification=classification,
        )
        count = count_merchants_needing_cadence(conn, filters=filters)
        return {
            "count": count,
            "filters": filters.to_dict(),
            "filter_labels": filters.active_labels(),
        }
    finally:
        conn.close()


@app.get("/api/transactions/cadence-suggest-merchants")
def api_transactions_cadence_suggest_merchants(
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    classification: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    conn = _conn()
    try:
        filters = _cadence_suggest_filters_from_request(
            q=q,
            month=month,
            category=category,
            sub_category=sub_category,
            expense_type=expense_type,
            classification=classification,
        )
        total = count_merchants_needing_cadence(conn, filters=filters)
        merchants = list_merchants_needing_cadence(
            conn, limit=limit, offset=offset, filters=filters
        )
        return {
            "merchants": merchants,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": filters.to_dict(),
            "filter_labels": filters.active_labels(),
        }
    finally:
        conn.close()


@app.post("/api/transactions/cadence-suggest-batch")
def api_transactions_cadence_suggest_batch(
    body: CadenceSuggestBatchRequest | None = None,
) -> dict[str, Any]:
    body = body or CadenceSuggestBatchRequest()
    if body.limit not in CADENCE_SUGGEST_BATCH_LIMITS:
        raise HTTPException(400, "limit must be one of: 10, 25, 50")
    conn = _conn()
    try:
        filters = _cadence_suggest_filters_from_request(
            q=body.q,
            month=body.month,
            category=body.category,
            sub_category=body.sub_category,
            expense_type=body.expense_type,
            classification=body.classification,
        )
        return suggest_cadence_bulk(conn, limit=body.limit, filters=filters)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/transactions/search")
def api_transactions_search(
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    classification: str = "",
    cadence_kind: str = "",
    cadence_period: str = "",
    include_in_run_rate: str = "",
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            return search_transactions(
                conn,
                q=q,
                month=month,
                category=category,
                sub_category=sub_category,
                expense_type=expense_type,
                classification=classification,
                cadence_kind=cadence_kind,
                cadence_period=cadence_period,
                include_in_run_rate=include_in_run_rate,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_dir=sort_dir,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/transactions/{transaction_id}")
def api_transaction_get(transaction_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        row = get_transaction(conn, transaction_id)
        if not row:
            raise HTTPException(404, "Transaction not found")
        return row
    finally:
        conn.close()


@app.get("/api/transactions/{transaction_id}/matches")
def api_transaction_matches(transaction_id: str, scope: str = "single") -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            matches = list_matching_transactions(conn, transaction_id, scope=scope)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        source = get_transaction(conn, transaction_id)
        if not source:
            raise HTTPException(404, "Transaction not found")
        return {"scope": scope, "source": source, "matches": matches}
    finally:
        conn.close()


@app.post("/api/transactions/bulk-label")
def api_transactions_bulk_label(body: TransactionBulkLabelRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            cadence_payload = body.cadence.model_dump() if body.cadence else None
            return bulk_update_labels(
                conn,
                transaction_ids=body.transaction_ids,
                ai_category=body.ai_category,
                ai_sub_category=body.ai_sub_category,
                flow_type=body.flow_type,
                expense_type=body.expense_type,
                classification=body.classification,
                new_merchant_key=body.new_merchant_key,
                update_merchant_label=body.update_merchant_label,
                merchant_key=body.merchant_key,
                cadence=cadence_payload,
                cadence_scope=body.cadence_scope,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/transactions/edit-insight")
def api_transactions_edit_insight(body: EditInsightRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            return analyze_edit(
                conn,
                merchant_key=body.merchant_key,
                scope=body.scope,
                rows_updated=body.rows_updated,
                before=body.before.model_dump(),
                after=body.after.model_dump(),
                amount=body.amount,
                update_merchant_label=body.update_merchant_label,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/review/{merchant_key}/confirm-preview")
def api_review_confirm_preview(
    merchant_key: str, body: ReviewConfirmPreviewRequest
) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            return confirm_preview(
                conn,
                merchant_key,
                ai_category=body.ai_category,
                ai_sub_category=body.ai_sub_category,
                expense_type=body.expense_type,
                flow_type=body.flow_type,
                classification=body.classification,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/review/{merchant_key}/confirm")
def api_review_confirm(merchant_key: str, body: ReviewConfirmRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        try:
            return confirm_merchant_or_transaction(
                conn,
                merchant_key,
                ai_category=body.ai_category,
                ai_sub_category=body.ai_sub_category,
                expense_type=body.expense_type,
                flow_type=body.flow_type,
                classification=body.classification,
                transaction_id=body.transaction_id,
                scope=body.scope,
                replace_conflicting_rule=body.replace_conflicting_rule,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/taxonomy-rules/analyze")
def api_taxonomy_analyze() -> dict[str, Any]:
    """Heuristic duplicate detection — no LLM, no writes."""
    conn = _conn()
    try:
        return analyze_taxonomy(conn)
    finally:
        conn.close()


@app.post("/api/taxonomy-rules/suggest")
def api_taxonomy_suggest() -> dict[str, Any]:
    """LLM + heuristic taxonomy proposals for user review (never auto-applied)."""
    conn = _conn()
    try:
        return suggest_taxonomy_proposals_with_llm(conn)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"LLM suggestion failed: {exc}") from exc
    finally:
        conn.close()


@app.post("/api/taxonomy-rules/preview")
def api_taxonomy_preview(body: TaxonomyProposalsRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        return preview_taxonomy_proposals(
            conn,
            body.proposals,
            reconcile=body.reconcile,
            sample_limit=body.sample_limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/taxonomy-rules/apply")
def api_taxonomy_apply(body: TaxonomyApplyRequest) -> dict[str, Any]:
    if body.confirm != "APPLY":
        raise HTTPException(
            400,
            'Confirmation required: send {"confirm": "APPLY", "proposals": [...]}',
        )
    conn = _conn()
    try:
        return apply_taxonomy_proposals(
            conn, body.proposals, reconcile=body.reconcile
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    conn = _conn()
    try:
        return {
            "db_path": str(DB_PATH),
            "inbox_dir": str(INBOX_DIR),
            "processed_dir": str(PROCESSED_DIR),
            "lookup_source": "sqlite",
            "table_counts": table_counts(conn),
            "ui_show_cadence": UI_SHOW_CADENCE,
        }
    finally:
        conn.close()


@app.post("/api/settings/clear")
def api_settings_clear(body: ClearDataRequest) -> dict[str, Any]:
    if body.confirm != "CLEAR":
        raise HTTPException(
            400,
            'Confirmation required: send {"confirm": "CLEAR"}',
        )
    conn = _conn()
    try:
        return clear_data_store(conn)
    finally:
        conn.close()


@app.get("/api/custom-reports")
def api_list_custom_reports() -> dict[str, Any]:
    conn = _conn()
    try:
        reports = custom_report_service.list_custom_reports(conn)
        return {"reports": reports, "count": len(reports)}
    finally:
        conn.close()


@app.get("/api/custom-reports/{report_id}")
def api_get_custom_report(report_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        report = custom_report_service.get_custom_report(conn, report_id)
        if not report:
            raise HTTPException(404, f"Custom report not found: {report_id}")
        return report
    finally:
        conn.close()


@app.post("/api/custom-reports/finalize")
def api_finalize_custom_report(body: CustomReportFinalizeRequest) -> dict[str, Any]:
    try:
        return custom_report_service.finalize_report_from_conversation(
            sql_template=body.sql_template,
            conversation_summary=body.conversation_summary,
            original_question=body.original_question,
            tool_trace=body.tool_trace,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/custom-reports")
def api_save_custom_report(body: CustomReportSaveRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        report_prompt = body.report_prompt
        description = body.description
        report_config = body.report_config
        sql_template = body.sql_template
        parameters = body.parameters

        if body.auto_finalize and not report_prompt.strip():
            finalized = custom_report_service.finalize_report_from_conversation(
                sql_template=sql_template,
                conversation_summary=body.conversation_summary,
                original_question=body.original_question,
                tool_trace=body.tool_trace,
            )
            report_prompt = finalized.get("report_prompt", "")
            if not description.strip():
                description = finalized.get("description", "")
            if not report_config:
                report_config = finalized.get("report_config") or {}
            sql_template = finalized.get("sql_template", sql_template)
            if parameters is None:
                parameters = finalized.get("parameters")

        saved = custom_report_service.save_custom_report(
            conn,
            name=body.name,
            sql_template=sql_template,
            description=description,
            original_question=body.original_question,
            report_prompt=report_prompt,
            report_config=report_config,
            parameters=parameters,
            parent_report_id=body.parent_report_id,
        )
        return {"ok": True, "report": saved}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.patch("/api/custom-reports/{report_id}")
def api_update_custom_report(report_id: str, body: CustomReportUpdateRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        updated = custom_report_service.update_custom_report(
            conn,
            report_id,
            name=body.name,
            description=body.description,
            sql_template=body.sql_template,
            report_prompt=body.report_prompt,
            report_config=body.report_config,
            original_question=body.original_question,
            parameters=body.parameters,
        )
        return {"ok": True, "report": updated}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-reports/{report_id}/fork")
def api_fork_custom_report(report_id: str, body: CustomReportForkRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        forked = custom_report_service.fork_custom_report(
            conn,
            report_id,
            new_name=body.new_name,
            description=body.description,
            sql_template=body.sql_template,
            report_prompt=body.report_prompt,
            report_config=body.report_config,
        )
        return {"ok": True, "report": forked}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/custom-reports/{report_id}/run")
def api_run_custom_report(report_id: str, body: CustomReportRunRequest | None = None) -> dict[str, Any]:
    body = body or CustomReportRunRequest()
    conn = _conn()
    try:
        result = custom_report_service.run_custom_report(
            conn,
            report_id,
            params=body.params,
            max_rows=body.max_rows,
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.delete("/api/custom-reports/{report_id}")
def api_delete_custom_report(report_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        deleted = custom_report_service.delete_custom_report(conn, report_id)
        if not deleted:
            raise HTTPException(404, f"Custom report not found: {report_id}")
        return {"ok": True, "deleted": report_id}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/chat/history")
def api_chat_history(
    page: int | None = None,
    limit: int = 10,
    order: str = "desc",
) -> dict[str, Any]:
    conn = _conn()
    try:
        if page is not None:
            return list_chat_history_page(conn, page=page, limit=limit, order=order)
        return {"messages": list_chat_history(conn)}
    finally:
        conn.close()


@app.get("/api/chat/history/export")
def api_chat_history_export(format: str = "json") -> Response:
    conn = _conn()
    try:
        body, media_type, filename = export_chat_history(conn, format)
        return Response(
            content=body,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/chat")
def api_chat(body: ChatRequest) -> dict[str, Any]:
    if not body.message.strip():
        raise HTTPException(400, "message required")
    conn = _conn()
    try:
        return chat(conn, body.message.strip())
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    raise HTTPException(404, "UI not found")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
