#!/usr/bin/env bash
# setup.sh -- install the equipment-booking service on Linux / macOS / WSL2.
#
#   bash setup.sh [--python python3.12] [--skip-venv] [--no-tests] [--debug]
#                 [--offline-bundle out.tar.xz]
#
# Steps: 1) system checks  2) venv  3) (no heavy deps)  4) Python packages
#        5) .env + database  6) verify_install.py + tests
# No `set -e` on purpose: every important command is checked with `|| die`.

cd "$(dirname "$0")" || exit 1
LOG_PREFIX="[setup]"
set -u
set -o pipefail

if [[ -t 1 ]]; then
    C_BLUE=$'\033[34m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_OFF=""
fi
log()  { printf '%s %s%s%s\n' "$LOG_PREFIX" "$C_BLUE" "$*" "$C_OFF"; }
ok()   { printf '%s %s[OK] %s%s\n' "$LOG_PREFIX" "$C_GREEN" "$*" "$C_OFF"; }
warn() { printf '%s %s[WARN] %s%s\n' "$LOG_PREFIX" "$C_YELLOW" "$*" "$C_OFF" >&2; }
err()  { printf '%s %s[FAIL] %s%s\n' "$LOG_PREFIX" "$C_RED" "$*" "$C_OFF" >&2; }
die()  { err "$*"; err "Re-run with --debug for details; see docs/TROUBLESHOOTING.md."; exit 1; }
confirm() { local a; read -r -p "$LOG_PREFIX $1 [y/N] " a; [[ "$a" == "y" || "$a" == "Y" ]]; }

PYTHON="python3"; SKIP_VENV=0; RUN_TESTS=1; DEBUG=0; BUNDLE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python) PYTHON="$2"; shift 2 ;;
        --skip-venv) SKIP_VENV=1; shift ;;
        --no-tests) RUN_TESTS=0; shift ;;
        --no-download) shift ;;  # nothing to download: equipment list is bundled
        --debug) DEBUG=1; set -x; shift ;;
        --offline-bundle) BUNDLE="$2"; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

N=6
# ---------------------------------------------------------------- 1
log "Step 1/$N: system checks"
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found. Install Python 3.12 (apt install python3.12 python3.12-venv)"
PYV="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" || die "cannot run $PYTHON"
# Range from the pinning deps: SQLAlchemy 2.1 needs >= 3.11, aiogram 3.31 needs < 3.15
if ! "$PYTHON" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) else 1)'; then
    warn "Python $PYV is outside the supported range 3.11-3.14 (3.12 recommended)."
    confirm "Continue anyway?" || die "Install Python 3.12 and run: bash setup.sh --python python3.12"
fi
if [[ -n "${CONDA_DEFAULT_ENV:-}" ]]; then
    warn "Conda environment '$CONDA_DEFAULT_ENV' is active; the venv will be based on its Python."
    confirm "Continue?" || die "Run 'conda deactivate' and start again."
fi
ok "Step 1/$N passed (Python $PYV)"

# ---------------------------------------------------------------- 2
log "Step 2/$N: virtual environment .venv"
if [[ $SKIP_VENV -eq 0 ]]; then
    if [[ ! -x .venv/bin/python ]]; then
        "$PYTHON" -m venv .venv || die "venv creation failed (Debian/Ubuntu: apt install python3-venv)"
    fi
    PY=".venv/bin/python"
else
    PY="$PYTHON"
fi
"$PY" -m pip install --upgrade pip -q || die "pip upgrade failed (no internet? see REPRODUCIBILITY.md, offline install)"
ok "Step 2/$N passed"

# ---------------------------------------------------------------- 3
log "Step 3/$N: heavy dependencies -- none for this project (no GPU, no models)"
ok "Step 3/$N passed"

# ---------------------------------------------------------------- 4
log "Step 4/$N: Python packages"
LOCK="requirements-lock-linux.txt"
if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" && -f "$LOCK" ]]; then
    "$PY" -m pip install --require-hashes -r "$LOCK" -q || die "pip install from $LOCK failed"
else
    "$PY" -m pip install -r requirements.txt -q || die "pip install -r requirements.txt failed"
fi
"$PY" -m pip freeze > requirements-frozen.txt
ok "Step 4/$N passed"

# ---------------------------------------------------------------- 5
log "Step 5/$N: .env and database"
if [[ ! -f .env ]]; then
    cp .env.example .env || die "cannot create .env"
    SECRET="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sed -i.bak "s|^WEB_SECRET_KEY=.*|WEB_SECRET_KEY=$SECRET|" .env && rm -f .env.bak
    warn ".env created from .env.example -- fill BOT_TOKEN and both WEB_*_PASSWORD"
fi
"$PY" main.py init-db || die "database initialisation failed"
ok "Step 5/$N passed"

# ---------------------------------------------------------------- 6
log "Step 6/$N: verification"
"$PY" verify_install.py || die "verify_install.py reported failures"
if [[ $RUN_TESTS -eq 1 ]]; then
    "$PY" -m pytest -q || die "tests failed"
fi
ok "Step 6/$N passed"

if [[ -n "$BUNDLE" ]]; then
    log "Building offline bundle $BUNDLE"
    "$PY" -m pip download -r requirements.txt -d wheels -q || die "pip download failed"
    tar -cJf "$BUNDLE" --exclude=.venv --exclude=data --exclude=.env . || die "tar failed"
    ok "Offline bundle: $BUNDLE"
fi

echo
ok "Done. Next: edit .env, then run:  $PY main.py run"
ok "Staff invite links:                $PY main.py invites"
