# Import & Categorize — upload and process

**Status:** Shipped  
**UI:** Import & Categorize tab

## Flow

1. User clicks **Choose CSV files** (multipart upload → `input/`)
2. Upload completes → **Run processing** starts automatically (SSE progress)
3. Pipeline categorizes rows and saves to `finance.db`; CSV moves to `processed/`

**Run processing** button re-runs the pipeline on any CSV still in `input/` (e.g. manually copied exports).

## APIs

| Endpoint | Role |
|----------|------|
| `POST /api/ingest/upload` | Copy CSV(s) to inbox |
| `GET /api/process/stream` | SSE progress for full pipeline |
| `POST /api/process` | Non-streaming batch process |

Legacy (not used by UI):

| Endpoint | Role |
|----------|------|
| `POST /api/ingest/upload-and-scan` | Upload + raw ingest without AI |
| `POST /api/ingest/scan` | Raw ingest for files already in inbox |

## Files

| File | Role |
|------|------|
| `webapp/services/inbox_upload.py` | Save uploads to inbox |
| `webapp/services/process.py` | `run_pipeline` + SQLite save |
| `webapp/static/app.js` | `uploadCsvFiles`, `runCategorizeStream` |
| `webapp/static/index.html` | Import & process panel |
