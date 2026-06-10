from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from webapp.agent.chat import chat, list_chat_history
from webapp.config import (
    CHAT_MODEL,
    DB_PATH,
    INBOX_DIR,
    PROCESSED_DIR,
    LLM_BASE_URL,
    LLM_PROVIDER,
    LOOKUP_FILE,
    PIPELINE_MODEL,
    STATIC_DIR,
)
from webapp.db.schema import get_connection, init_db
from webapp.services.categorize import (
    confirm_merchant,
    confirm_transaction,
    list_merchant_transactions,
    list_review_items,
)
from webapp.services.process import list_inbox_csv_paths, process_inbox_files
from webapp.services.review_options import get_review_options
from webapp.services.data_store import clear_data_store, table_counts
from webapp.services.inbox_upload import save_upload_to_inbox, scan_uploaded_files
from webapp.services.ingest import ingest_csv, scan_inbox
from webapp.services.custom_rules import add_custom_rule, compile_and_apply_custom_rules, list_custom_rules
from webapp.services.lookups_import import default_lookup_workbook_path, import_lookup_workbook
from webapp.services.cadence_insights import propose_cadence, propose_cadence_batch
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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    transaction_id: str | None = None


class IngestRequest(BaseModel):
    filename: str | None = None


class ClearDataRequest(BaseModel):
    confirm: str


class ImportLookupsRequest(BaseModel):
    path: str | None = None


class CustomRuleCreateRequest(BaseModel):
    rule: str = Field(min_length=1)


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
    limit: int = Field(default=10, ge=1, le=25)


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
        lookup_path = default_lookup_workbook_path()
        inbox_csv_files = sorted(p.name for p in INBOX_DIR.glob("*.csv"))
        return {
            "db_path": str(DB_PATH),
            "inbox_dir": str(INBOX_DIR),
            "processed_dir": str(PROCESSED_DIR),
            "inbox_csv_files": inbox_csv_files,
            "lookup_file": str(LOOKUP_FILE),
            "lookup_file_exists": lookup_path.is_file(),
            "llm_provider": LLM_PROVIDER,
            "llm_base_url": LLM_BASE_URL,
            "llm_model": CHAT_MODEL,
            "pipeline_model": PIPELINE_MODEL,
            "chat_model": CHAT_MODEL,
            "transaction_count": tx_count,
            "review_merchant_count": review_count,
            "months": [r["budget_month"] for r in months],
            "table_counts": counts,
        }
    finally:
        conn.close()


@app.post("/api/ingest/scan")
def api_ingest_scan(force: bool = False) -> dict[str, Any]:
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
    update_lookup_workbook: bool = True


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
            update_lookup_workbook=body.update_lookup_workbook,
        )
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
    update_lookup_workbook: bool = True,
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
                    update_lookup_workbook=update_lookup_workbook,
                    on_progress=on_progress,
                )
                result_box["data"] = {
                    "file_count": len(results),
                    "results": results,
                    "message": f"Processed {len(results)} file(s).",
                }
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
    conn = _conn()
    try:
        return propose_cadence_batch(conn, limit=body.limit)
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
    try:
        return list_custom_rules()
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/custom-rules")
def api_custom_rules_add(body: CustomRuleCreateRequest) -> dict[str, Any]:
    try:
        return add_custom_rule(body.rule)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/custom-rules/compile-apply")
def api_custom_rules_compile_apply() -> dict[str, Any]:
    conn = _conn()
    try:
        return compile_and_apply_custom_rules(conn)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
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


@app.post("/api/review/{merchant_key}/confirm")
def api_review_confirm(merchant_key: str, body: ReviewConfirmRequest) -> dict[str, Any]:
    conn = _conn()
    try:
        if body.transaction_id:
            updated = confirm_transaction(
                conn,
                body.transaction_id,
                ai_category=body.ai_category,
                ai_sub_category=body.ai_sub_category,
                expense_type=body.expense_type,
                flow_type=body.flow_type,
            )
            return {
                "merchant_key": merchant_key,
                "transaction_id": body.transaction_id,
                "rows_updated": updated,
            }
        updated = confirm_merchant(
            conn,
            merchant_key,
            ai_category=body.ai_category,
            ai_sub_category=body.ai_sub_category,
            expense_type=body.expense_type,
            flow_type=body.flow_type,
        )
        return {"merchant_key": merchant_key, "rows_updated": updated}
    finally:
        conn.close()


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    conn = _conn()
    try:
        lookup_path = default_lookup_workbook_path()
        return {
            "db_path": str(DB_PATH),
            "inbox_dir": str(INBOX_DIR),
            "processed_dir": str(PROCESSED_DIR),
            "lookup_file": str(lookup_path),
            "lookup_file_exists": lookup_path.is_file(),
            "table_counts": table_counts(conn),
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


@app.post("/api/settings/import-lookups")
def api_settings_import_lookups(body: ImportLookupsRequest | None = None) -> dict[str, Any]:
    path = Path(body.path) if body and body.path else default_lookup_workbook_path()
    if not path.is_absolute():
        from webapp.config import ROOT

        path = (ROOT / path).resolve()
    conn = _conn()
    try:
        return import_lookup_workbook(conn, path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/chat/history")
def api_chat_history() -> dict[str, Any]:
    conn = _conn()
    try:
        return {"messages": list_chat_history(conn)}
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
