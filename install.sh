#!/usr/bin/env bash
# Transaction Insight — macOS curl bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/develop/install.sh | bash
#   # or a release tag that includes this file; set INSTALL_REF to match
#
# Or from a local clone / after download:
#   bash install.sh
#
# Env overrides:
#   INSTALL_DIR  — clone destination (default: ~/transaction-insight)
#   INSTALL_REF  — git tag/branch (default: latest v* tag, else v0.1.0)
#   LLM_MODE     — local|cloud (passed through to scripts/install_macos.sh)
#   FORCE_ENV=1  — overwrite config/.env
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/muthurajesh/transaction-insight.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/transaction-insight}"

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "This installer supports macOS only."

command -v git >/dev/null 2>&1 || die "git is required. Install Xcode Command Line Tools: xcode-select --install"
command -v curl >/dev/null 2>&1 || die "curl is required."

resolve_ref() {
  if [[ -n "${INSTALL_REF:-}" ]]; then
    echo "$INSTALL_REF"
    return
  fi
  local latest
  latest="$(git ls-remote --tags --refs "$REPO_URL" 'v*' 2>/dev/null \
    | awk -F/ '{print $NF}' \
    | sort -V \
    | tail -1)" || true
  if [[ -n "$latest" ]]; then
    echo "$latest"
  else
    echo "v0.1.0"
  fi
}

INSTALL_REF="$(resolve_ref)"
log "Installing Transaction Insight ($INSTALL_REF) → $INSTALL_DIR"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  log "Existing clone found; updating…"
  cd "$INSTALL_DIR"
  remote_url="$(git remote get-url origin 2>/dev/null || true)"
  if [[ -n "$remote_url" ]] && [[ "$remote_url" != *transaction-insight* ]]; then
    die "INSTALL_DIR exists but origin is not transaction-insight: $remote_url"
  fi
  if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    die "Working tree is dirty at $INSTALL_DIR — commit/stash changes or choose another INSTALL_DIR."
  fi
  git fetch --tags --depth 1 origin "$INSTALL_REF" 2>/dev/null \
    || git fetch --tags origin
  git checkout "$INSTALL_REF"
elif [[ -e "$INSTALL_DIR" ]]; then
  die "$INSTALL_DIR exists but is not a git clone. Remove it or set INSTALL_DIR to another path."
else
  log "Cloning repository…"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --branch "$INSTALL_REF" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

[[ -f "$INSTALL_DIR/scripts/install_macos.sh" ]] \
  || die "Missing scripts/install_macos.sh in $INSTALL_DIR (ref $INSTALL_REF may predate the installer)."

chmod +x "$INSTALL_DIR/scripts/install_macos.sh" "$INSTALL_DIR/start.sh" 2>/dev/null || true
export INSTALL_DIR
exec bash "$INSTALL_DIR/scripts/install_macos.sh"
