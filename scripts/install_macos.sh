#!/usr/bin/env bash
# Transaction Insight — macOS setup (Python venv + local Ollama or OpenAI cloud).
# Prefer running via root install.sh (curl bootstrap). From a clone:
#   bash scripts/install_macos.sh
#
# Env overrides:
#   INSTALL_DIR   — repo root (default: directory containing this script's parent)
#   LLM_MODE      — local|cloud (skip interactive prompt)
#   FORCE_ENV=1   — overwrite existing config/.env
#   SKIP_OLLAMA_PULL=1 — skip ollama pull (local mode)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$REPO_ROOT}"
OLLAMA_MODEL_DEFAULT="qwen2.5:7b"
MIN_FREE_GB_LOCAL=5

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_macos() {
  [[ "$(uname -s)" == "Darwin" ]] || die "This installer supports macOS only (got $(uname -s))."
}

check_disk_space() {
  local avail_kb avail_gb
  avail_kb="$(df -k "$INSTALL_DIR" 2>/dev/null | awk 'NR==2 {print $4}')" || return 0
  avail_gb=$((avail_kb / 1024 / 1024))
  if (( avail_gb < MIN_FREE_GB_LOCAL )); then
    warn "Only ~${avail_gb}GB free under $INSTALL_DIR (recommend ≥${MIN_FREE_GB_LOCAL}GB for local Ollama)."
  fi
}

python_major_minor() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

python_ok() {
  local ver
  ver="$(python_major_minor "$1" 2>/dev/null)" || return 1
  local major minor
  major="${ver%%.*}"
  minor="${ver#*.}"
  (( major > 3 || (major == 3 && minor >= 11) ))
}

ensure_python() {
  local py=""
  if command -v python3 >/dev/null 2>&1 && python_ok python3; then
    py="$(command -v python3)"
  elif command -v python3.12 >/dev/null 2>&1 && python_ok python3.12; then
    py="$(command -v python3.12)"
  elif command -v python3.11 >/dev/null 2>&1 && python_ok python3.11; then
    py="$(command -v python3.11)"
  fi

  if [[ -z "$py" ]]; then
    if command -v brew >/dev/null 2>&1; then
      log "Installing Python 3.12 via Homebrew…"
      brew install python@3.12
      if command -v python3.12 >/dev/null 2>&1 && python_ok python3.12; then
        py="$(command -v python3.12)"
      elif command -v python3 >/dev/null 2>&1 && python_ok python3; then
        py="$(command -v python3)"
      fi
    fi
  fi

  [[ -n "$py" ]] || die "Python 3.11+ required. Install with: brew install python@3.12"
  echo "$py"
}

setup_venv() {
  local py="$1"
  cd "$INSTALL_DIR"
  if [[ ! -d .venv ]]; then
    log "Creating virtualenv (.venv)…"
    "$py" -m venv .venv
  else
    log "Using existing .venv"
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  log "Installing Python dependencies…"
  pip install --upgrade pip
  pip install -r requirements.txt
}

choose_llm_mode() {
  if [[ -n "${LLM_MODE:-}" ]]; then
    case "${LLM_MODE}" in
      local|cloud) echo "$LLM_MODE"; return ;;
      *) die "LLM_MODE must be local or cloud (got: $LLM_MODE)" ;;
    esac
  fi
  if [[ ! -t 0 ]]; then
    warn "Non-interactive stdin; defaulting to local Ollama. Set LLM_MODE=local|cloud to choose."
    echo local
    return
  fi
  # Prompts must go to stderr — stdout is captured by mode="$(choose_llm_mode)".
  printf '\n' >&2
  local choice
  read -r -p "Type 1 for Local LLM or 2 for Cloud LLM? [1]: " choice || true
  case "${choice:-1}" in
    2) echo cloud ;;
    *) echo local ;;
  esac
}

confirm_overwrite_env() {
  local preset_label="$1"
  local env_path="$INSTALL_DIR/config/.env"
  if [[ ! -f "$env_path" ]]; then
    return 0
  fi
  if [[ "${FORCE_ENV:-0}" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    warn "config/.env already exists; leaving it unchanged (set FORCE_ENV=1 to overwrite)."
    return 1
  fi
  local ans
  printf '\n' >&2
  read -r -p "Replace your existing config/.env with the ${preset_label} preset? [y/N] " ans || true
  case "${ans:-}" in
    y|Y|yes|YES) return 0 ;;
    *) warn "Keeping your existing config/.env"; return 1 ;;
  esac
}

write_env_from_preset() {
  local preset="$1"
  local preset_label="$2"
  local src="$INSTALL_DIR/config/$preset"
  local dest="$INSTALL_DIR/config/.env"
  [[ -f "$src" ]] || die "Missing preset: $src"
  if confirm_overwrite_env "$preset_label"; then
    cp "$src" "$dest"
    log "Wrote config/.env from $preset"
    return 0
  fi
  return 1
}

set_openai_api_key() {
  local env_path="$INSTALL_DIR/config/.env"
  local key="${OPENAI_API_KEY_INPUT:-}"
  if [[ -z "$key" && -t 0 ]]; then
    read -r -p "Paste OpenAI API key (Enter to skip): " key || true
  fi
  if [[ -z "$key" ]]; then
    warn "OPENAI_API_KEY left empty. Edit config/.env before chat/import will work:"
    printf '  OPENAI_API_KEY=sk-...\n'
    return 0
  fi
  if grep -q '^OPENAI_API_KEY=' "$env_path"; then
    # Escape for sed replacement; use a delimiter unlikely in keys
    local escaped
    escaped="$(printf '%s' "$key" | sed 's/[\/&]/\\&/g')"
    sed -i '' "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=${escaped}|" "$env_path"
  else
    printf '\nOPENAI_API_KEY=%s\n' "$key" >>"$env_path"
  fi
  log "Saved OPENAI_API_KEY in config/.env"
}

ensure_ollama() {
  if ! command -v ollama >/dev/null 2>&1; then
    log "Installing Ollama…"
    if ! curl -fsSL https://ollama.com/install.sh | sh; then
      warn "Automatic Ollama install failed. Opening download page…"
      open "https://ollama.com/download" 2>/dev/null || true
      die "Install Ollama from https://ollama.com/download, then re-run this script."
    fi
  fi
  command -v ollama >/dev/null 2>&1 || die "ollama not found on PATH after install."

  if ! curl -fsS --max-time 2 "http://127.0.0.1:11434/v1/models" >/dev/null 2>&1; then
    log "Starting Ollama…"
    open -a Ollama 2>/dev/null || true
    local i
    for i in $(seq 1 30); do
      if curl -fsS --max-time 2 "http://127.0.0.1:11434/v1/models" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi
  curl -fsS --max-time 5 "http://127.0.0.1:11434/v1/models" >/dev/null \
    || die "Ollama is not responding at http://127.0.0.1:11434 — open the Ollama app and retry."
}

pull_ollama_model() {
  if [[ "${SKIP_OLLAMA_PULL:-0}" == "1" ]]; then
    warn "SKIP_OLLAMA_PULL=1 — not pulling $OLLAMA_MODEL_DEFAULT"
    return 0
  fi
  log "Pulling Ollama model $OLLAMA_MODEL_DEFAULT (may take several minutes)…"
  ollama pull "$OLLAMA_MODEL_DEFAULT"
}

smoke_local() {
  local body
  body="$(curl -fsS --max-time 10 "http://127.0.0.1:11434/v1/models" 2>/dev/null)" || {
    warn "Could not list Ollama models for smoke check."
    return 0
  }
  if printf '%s' "$body" | grep -q "qwen2.5:7b\|qwen2.5:7b-instruct\|\"qwen2.5:7b\""; then
    log "Smoke check OK: $OLLAMA_MODEL_DEFAULT is available in Ollama."
  elif printf '%s' "$body" | grep -qi "qwen2.5"; then
    log "Smoke check: Ollama is up (qwen2.5 model listed)."
  else
    warn "Ollama is up but $OLLAMA_MODEL_DEFAULT was not clearly listed. Run: ollama pull $OLLAMA_MODEL_DEFAULT"
  fi
}

print_done() {
  cat <<EOF

────────────────────────────────────────
Setup complete.

Next steps:
  cd "$INSTALL_DIR"
  ./start.sh

Then open http://127.0.0.1:8000
Import samples/sample_transactions.csv (or your bank CSV) from Import.

More LLM options: docs/setup/LLM_SETUP.md
────────────────────────────────────────
EOF
}

main() {
  require_macos
  [[ -d "$INSTALL_DIR" ]] || die "INSTALL_DIR does not exist: $INSTALL_DIR"
  [[ -f "$INSTALL_DIR/requirements.txt" ]] || die "Not a Transaction Insight repo: $INSTALL_DIR"
  [[ -f "$INSTALL_DIR/start.sh" ]] || die "Missing start.sh in $INSTALL_DIR"

  log "Transaction Insight setup (macOS)"
  printf 'Install dir: %s\n' "$INSTALL_DIR"
  check_disk_space

  local py
  py="$(ensure_python)"
  log "Using Python: $py ($(python_major_minor "$py"))"
  setup_venv "$py"

  local mode
  mode="$(choose_llm_mode)"
  log "LLM mode: $mode"

  if [[ "$mode" == "local" ]]; then
    ensure_ollama
    pull_ollama_model
    write_env_from_preset ".env.ollama" "Local LLM (Ollama, qwen2.5:7b)" || true
    smoke_local
  else
    if write_env_from_preset ".env.openai" "Cloud LLM (OpenAI)"; then
      set_openai_api_key
    else
      warn "Skipped rewriting .env; set OPENAI_API_KEY in config/.env if using cloud."
    fi
    if [[ -f "$INSTALL_DIR/config/.env" ]] \
      && grep -Eq '^OPENAI_API_KEY=\s*$' "$INSTALL_DIR/config/.env"; then
      warn "Cloud mode: OPENAI_API_KEY is empty — chat/import will fail until you set it."
    fi
  fi

  chmod +x "$INSTALL_DIR/start.sh" 2>/dev/null || true
  print_done
}

main "$@"
