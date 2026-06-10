# Run processing — file picker upload

**Status:** Not implemented  
**Pattern:** Mirror ingest upload-and-scan

## Goal

UX parity: user picks CSV from disk for **Run processing** without manually copying to `input/`.

## Reference implementation

Ingest already has:

- `POST /api/ingest/upload-and-scan`
- `webapp/services/inbox_upload.py`
- UI: “Choose CSV files & scan” on Import tab

## Proposed flow

1. User clicks **Choose files & process** on Import / Run section
2. Multipart upload → copy to `input/` (or temp staging)
3. Call existing `process_csv_file` / batch process for selected files only
4. Return job summary: files processed, row counts, errors

## API sketch

```http
POST /api/process/upload-and-run
Content-Type: multipart/form-data
files: [file1.csv, file2.csv]
```

Response:

```json
{
  "processed": [{"filename": "...", "rows": 42, "status": "ok"}],
  "errors": []
}
```

Reuse `inbox_upload.save_uploaded_files()` then existing process service.

## UI

- Button next to “Run processing” on all inbox files
- Same file input pattern as scan (`<input type="file" multiple accept=".csv">`)
- Progress / result toast

## Files

| File | Change |
|------|--------|
| `webapp/services/inbox_upload.py` | Shared save helper (if not already) |
| `webapp/main.py` | New endpoint |
| `webapp/static/app.js` | Button + fetch |
| `webapp/static/index.html` | File input |

## Non-goals

- Process files not in inbox without upload
- Parallel upload queue / background jobs (v1: synchronous like scan)

## Verification

1. Upload new CSV via picker
2. File lands in `input/`, processing runs
3. Transactions appear in DB / Confirm Categories

## Context

Roadmap §5 optional UX parity; ingest upload shipped in same project chat.
