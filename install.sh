#!/usr/bin/env bash
# Transaction Insight — macOS curl bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/develop/install.sh | bash
#   # or a release tag that includes this file; set INSTALL_REF to match
#
# Or from a local clone / after download:
#   bash install.sh
#
# Installs into the folder you run it from when that folder is empty; otherwise
# into ./transaction-insight under it.
#
# Env overrides:
#   INSTALL_DIR  — clone destination (default: see above)
#   INSTALL_REF  — git tag/branch (default: latest v* tag, else DEFAULT_REF)
#   DEFAULT_REF  — branch used when no usable tag exists (default: develop)
#   LLM_MODE     — local|cloud (passed through to scripts/install_macos.sh)
#   FORCE_ENV=1  — overwrite config/.env
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/muthurajesh/transaction-insight.git}"
DEFAULT_REF="${DEFAULT_REF:-develop}"

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Finder leaves .DS_Store behind in folders the user considers empty.
dir_has_content() {
  [[ -n "$(ls -A "$1" 2>/dev/null | grep -v '^\.DS_Store$')" ]]
}

is_ti_clone() {
  [[ -d "$1/.git" ]] || return 1
  [[ "$(git -C "$1" remote get-url origin 2>/dev/null || true)" == *transaction-insight* ]]
}

# Install where the user is standing — updating in place when it is already a
# clone. Fall back to a subfolder so a stray run in a working folder never mixes
# the repo into their files.
if is_ti_clone "$PWD" || ! dir_has_content "$PWD"; then
  INSTALL_DIR="${INSTALL_DIR:-$PWD}"
else
  INSTALL_DIR="${INSTALL_DIR:-$PWD/transaction-insight}"
fi

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
    echo "$DEFAULT_REF"
  fi
}

# A pinned ref is honoured exactly; an auto-detected one may be superseded below.
REF_PINNED=0
[[ -n "${INSTALL_REF:-}" ]] && REF_PINNED=1
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
elif [[ -e "$INSTALL_DIR" ]] && dir_has_content "$INSTALL_DIR"; then
  die "$INSTALL_DIR is not empty and is not a Transaction Insight clone. Empty it or set INSTALL_DIR to another path."
elif [[ -d "$INSTALL_DIR" ]]; then
  # git clone refuses any existing destination, so stage the clone and move it in.
  log "Cloning repository into ${INSTALL_DIR}…"
  tmp_clone="$(mktemp -d "${TMPDIR:-/tmp}/ti-clone.XXXXXX")"
  rm -rf "$tmp_clone"
  git clone --branch "$INSTALL_REF" --depth 1 "$REPO_URL" "$tmp_clone"
  (shopt -s dotglob nullglob; mv "$tmp_clone"/* "$INSTALL_DIR"/)
  rmdir "$tmp_clone" 2>/dev/null || true
else
  log "Cloning repository…"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --branch "$INSTALL_REF" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# The newest tag can predate the installer (e.g. v0.1.0). Auto-detected refs move
# to DEFAULT_REF rather than failing; a pinned INSTALL_REF still errors out.
if [[ ! -f "$INSTALL_DIR/scripts/install_macos.sh" && "$REF_PINNED" == "0" && "$INSTALL_REF" != "$DEFAULT_REF" ]]; then
  log "Ref $INSTALL_REF predates the installer — using $DEFAULT_REF instead…"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$DEFAULT_REF"
  git -C "$INSTALL_DIR" checkout -q FETCH_HEAD
  INSTALL_REF="$DEFAULT_REF"
fi

[[ -f "$INSTALL_DIR/scripts/install_macos.sh" ]] \
  || die "Missing scripts/install_macos.sh in $INSTALL_DIR (ref $INSTALL_REF predates the installer). Retry with: INSTALL_REF=$DEFAULT_REF"

chmod +x "$INSTALL_DIR/scripts/install_macos.sh" "$INSTALL_DIR/start.sh" 2>/dev/null || true
export INSTALL_DIR
exec bash "$INSTALL_DIR/scripts/install_macos.sh"
