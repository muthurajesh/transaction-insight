#!/usr/bin/env bash
# Full reset + seed transaction-lookups.xlsx with qwen2.5-coder:32b (Ollama).
#
# Usage:
#   ./scripts/reset_and_seed_pipeline_32b.sh --all --input-dir original-data
#       # fresh run: backs up, wipes state, processes Jul 2021 → May 2026
#   ./scripts/reset_and_seed_pipeline_32b.sh --all --input-dir original-data --no-reset
#       # resume: keep lookups/history; skip months whose output xlsx already exists
#   ./scripts/reset_and_seed_pipeline_32b.sh --all --input-dir original-data --no-reset --force
#       # reprocess every month without wiping shared state
#
# Prerequisites: Ollama running, model pulled:
#   ollama pull qwen2.5-coder:32b
#
# After CLI seeding, load the web app database (no LLM re-run):
#   python scripts/import_processed_to_db.py

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

RUN_ALL=false
NO_RESET=false
FORCE=false
INPUT_DIR="input"

usage() {
  cat <<'EOF'
Full reset + seed transaction-lookups.xlsx with qwen2.5-coder:32b (Ollama).

Usage:
  ./scripts/reset_and_seed_pipeline_32b.sh [--input-dir DIR]
      Pilot: first month only (July 2021) from input/ or --input-dir

  ./scripts/reset_and_seed_pipeline_32b.sh --all [--input-dir DIR]
      Fresh full run: backup + wipe state, then all months chronologically

  ./scripts/reset_and_seed_pipeline_32b.sh --all --no-reset [--input-dir DIR]
      Resume after interrupt: keep lookups/history; skip months with existing
      output/<stem>-Insights.xlsx

Options:
  --all           Process every ExportData-<Month>-<Year>.csv (chronological)
  --input-dir DIR Source folder (default: input)
  --no-reset      Do not backup or delete lookups, history, or finance.db
  --force         With --no-reset, reprocess months even if output xlsx exists
  -h, --help      Show this help

Prerequisites:
  ollama pull qwen2.5-coder:32b
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      RUN_ALL=true
      shift
      ;;
    --no-reset)
      NO_RESET=true
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    --input-dir)
      if [[ $# -lt 2 ]]; then
        echo "Error: --input-dir requires a path" >&2
        exit 1
      fi
      INPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1 (try --help)" >&2
      exit 1
      ;;
  esac
done

if [[ "$FORCE" == true && "$NO_RESET" != true ]]; then
  echo "Error: --force requires --no-reset" >&2
  exit 1
fi

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "Input directory not found: $INPUT_DIR" >&2
  exit 1
fi

if [[ "$NO_RESET" == true ]]; then
  echo "=== Resume mode (--no-reset): keeping existing state ==="
  echo "  Lookups:  scripts/transaction-lookups.xlsx"
  echo "  History:  scripts/transaction-history.xlsx"
  echo "  Database: data/finance.db"
else
  STAMP="$(date +%Y%m%d)"
  BACKUP_DIR="backups/${STAMP}"
  mkdir -p "$BACKUP_DIR"

  echo "=== Backing up current state to ${BACKUP_DIR} ==="
  cp scripts/transaction-lookups.xlsx "$BACKUP_DIR/" 2>/dev/null || true
  cp scripts/transaction-history.xlsx "$BACKUP_DIR/" 2>/dev/null || true
  cp data/finance.db "$BACKUP_DIR/" 2>/dev/null || true

  echo "=== Clearing pipeline + web state ==="
  rm -f scripts/transaction-lookups.xlsx
  rm -f scripts/transaction-history.xlsx
  rm -f data/finance.db

  if [[ "$INPUT_DIR" == "input" ]]; then
    echo "=== Restoring CSVs from processed/ to input/ (if any) ==="
    shopt -s nullglob
    for csv in processed/*.csv; do
      cp -n "$csv" input/ 2>/dev/null || cp "$csv" input/
    done
    shopt -u nullglob
  fi
fi

export FINANCE_MOVE_PROCESSED=false

output_path_for() {
  local input_csv="$1"
  local stem
  stem="$(basename "$input_csv" .csv)"
  echo "output/${stem}-Insights.xlsx"
}

run_one() {
  local input_csv="$1"
  local stem output_path
  stem="$(basename "$input_csv" .csv)"
  output_path="$(output_path_for "$input_csv")"

  if [[ "$NO_RESET" == true && "$FORCE" != true && -f "$output_path" ]]; then
    echo ""
    echo "=== Skipping (output exists): $(basename "$input_csv") -> ${output_path} ==="
    return 0
  fi

  echo ""
  echo "=== Processing: $input_csv ==="
  python scripts/process_transactions.py \
    --input "$input_csv" \
    --output "$output_path" \
    --provider ollama \
    --model qwen2.5-coder:32b \
    --update-history
}

list_sorted_exports() {
  python scripts/split_export_by_month.py --list-sorted "$INPUT_DIR"
}

# Bash 3.2 (macOS default) lacks mapfile and negative array indices.
load_sorted_exports() {
  FILES=()
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      FILES+=("$line")
    fi
  done < <(list_sorted_exports)
}

last_file() {
  local n=${#FILES[@]}
  if [[ "$n" -eq 0 ]]; then
    echo ""
  else
    echo "${FILES[$((n - 1))]}"
  fi
}

if [[ "$RUN_ALL" == true ]]; then
  load_sorted_exports
  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No ExportData-*.csv files found in ${INPUT_DIR}/." >&2
    exit 1
  fi
  echo "=== Processing ${#FILES[@]} file(s) from ${INPUT_DIR}/ (chronological) ==="
  echo "  First: $(basename "${FILES[0]}")"
  echo "  Last:  $(basename "$(last_file)")"
  if [[ "$NO_RESET" == true && "$FORCE" != true ]]; then
    echo "  Skipping months that already have output/*-Insights.xlsx"
  fi
  SKIPPED=0
  PROCESSED=0
  for csv in "${FILES[@]}"; do
    output_path="$(output_path_for "$csv")"
    if [[ "$NO_RESET" == true && "$FORCE" != true && -f "$output_path" ]]; then
      SKIPPED=$((SKIPPED + 1))
      run_one "$csv"
    else
      run_one "$csv"
      PROCESSED=$((PROCESSED + 1))
    fi
  done
  echo ""
  echo "=== Batch summary ==="
  echo "  Processed this run: ${PROCESSED}"
  if [[ "$NO_RESET" == true && "$FORCE" != true ]]; then
    echo "  Skipped (existing output): ${SKIPPED}"
  fi
else
  load_sorted_exports
  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No ExportData-*.csv files found in ${INPUT_DIR}/." >&2
    echo "Split your master export first: python scripts/split_export_by_month.py" >&2
    exit 1
  fi
  PILOT="${FILES[0]}"
  echo "=== Pilot run from ${INPUT_DIR}/ (first month: $(basename "$PILOT")) ==="
  run_one "$PILOT"
  echo ""
  if [[ "$NO_RESET" == true ]]; then
    echo "Pilot step done. Continue with:"
    echo "  ./scripts/reset_and_seed_pipeline_32b.sh --all --no-reset --input-dir ${INPUT_DIR}"
  else
    echo "Pilot complete. Continue with a fresh full run:"
    echo "  ./scripts/reset_and_seed_pipeline_32b.sh --all --input-dir ${INPUT_DIR}"
    echo "Or resume without wiping (after a partial --all):"
    echo "  ./scripts/reset_and_seed_pipeline_32b.sh --all --no-reset --input-dir ${INPUT_DIR}"
  fi
fi

echo ""
echo "Done. Lookup workbook: scripts/transaction-lookups.xlsx"
echo "History workbook:      scripts/transaction-history.xlsx"
