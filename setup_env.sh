#!/usr/bin/env bash
# =============================================================================
#  One-shot environment setup for TRILEMMA.
#
#  Creates a Python virtual-env (.venv) with the runtime stack, installs the
#  pinned opencode CLI (the agent harness every assistant runs in), checks that
#  the shipped worlds and configs are consistent, and stores your OpenRouter key.
#
#  Usage:
#       bash setup_env.sh              # runtime only
#       bash setup_env.sh --analysis   # + judge/figure dependencies
#
#  Idempotent: safe to re-run. Nothing is installed system-wide except opencode
#  (a global npm package, or a standalone binary in ./.bin on Linux without npm).
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
VENV="$HERE/.venv"
PY_VERSION="3.12"
OPENCODE_VERSION="1.18.25"          # the version every paper run used — pinned
EXTRAS=""
[ "${1:-}" = "--analysis" ] && EXTRAS="[analysis]"

b(){ printf '\033[1m%s\033[0m\n' "$*"; }
step(){ printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok(){   printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
err(){  printf '  \033[31m✗\033[0m %s\n' "$*"; }

b "TRILEMMA — environment setup"
b "repo: $HERE"

# --- 1. Python virtual environment ---------------------------------------
step "1/4  Python virtual environment (.venv)"
if command -v uv >/dev/null 2>&1; then
    ok "uv found ($(uv --version))"
    uv venv --python "$PY_VERSION" "$VENV" >/dev/null
    PYBIN="$VENV/bin/python"
    pipinstall(){ uv pip install --python "$PYBIN" "$@"; }
else
    CAND=""
    for c in python3.12 python3.13 python3; do
        command -v "$c" >/dev/null 2>&1 || continue
        if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)'; then
            CAND="$c"; break
        fi
    done
    if [ -z "$CAND" ]; then
        err "No Python >=3.12 found, and 'uv' is not installed."
        err "Fix: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    ok "using $CAND ($($CAND --version 2>&1))"
    "$CAND" -m venv "$VENV"
    PYBIN="$VENV/bin/python"
    "$PYBIN" -m pip install --quiet --upgrade pip setuptools wheel
    pipinstall(){ "$PYBIN" -m pip install "$@"; }
fi
ok "venv ready: $VENV"

# --- 2. Python dependencies ----------------------------------------------
step "2/4  Python dependencies"
pipinstall -e "$HERE$EXTRAS"
"$PYBIN" - <<'PYEOF'
import importlib, sys
bad = []
for m in ["mcp.server", "uvicorn", "starlette", "pydantic", "httpx", "yaml", "certifi",
          "trilemma.env.runner", "trilemma.env.slack_server", "trilemma.env.proxy"]:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m}: {type(e).__name__}: {e}")
if bad:
    print("  IMPORT FAILURES:\n   - " + "\n   - ".join(bad)); sys.exit(1)
print(f"  ok: python {sys.version.split()[0]}")
PYEOF
"$PYBIN" trilemma/fixtures/build_fixtures.py --check >/dev/null && ok "worlds match their builder"
"$PYBIN" configs/make_configs.py --check | tail -1 | sed 's/^/  /'

# --- 3. opencode ----------------------------------------------------------
step "3/4  opencode $OPENCODE_VERSION (the assistants' harness)"
have_oc(){ command -v opencode >/dev/null 2>&1 && opencode --version 2>/dev/null | grep -q "$OPENCODE_VERSION"; }
if have_oc; then
    ok "opencode $OPENCODE_VERSION already on PATH"
elif command -v npm >/dev/null 2>&1; then
    ok "installing opencode-ai@$OPENCODE_VERSION globally (npm)"
    npm install -g "opencode-ai@$OPENCODE_VERSION"
    have_oc && ok "opencode installed" || warn "installed, but 'opencode' is not on PATH: export PATH=\"\$(npm config get prefix)/bin:\$PATH\""
elif [ "$(uname)" = "Linux" ]; then
    ARCH=$(uname -m); case "$ARCH" in x86_64) A=x64;; aarch64) A=arm64;; *) err "unsupported arch $ARCH"; exit 1;; esac
    mkdir -p "$HERE/.bin" && TMP="$(mktemp -d)"
    curl -fSL -o "$TMP/oc.tar.gz" \
        "https://github.com/sst/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${A}.tar.gz"
    tar -xzf "$TMP/oc.tar.gz" -C "$TMP"
    mv "$(find "$TMP" -type f -name opencode | head -1)" "$HERE/.bin/opencode"
    chmod +x "$HERE/.bin/opencode"; rm -rf "$TMP"
    ok "standalone binary in .bin/opencode — add it to PATH: export PATH=\"$HERE/.bin:\$PATH\""
else
    warn "npm not found — install Node.js, then: npm install -g opencode-ai@$OPENCODE_VERSION"
fi
if command -v opencode >/dev/null 2>&1 && ! have_oc; then
    warn "opencode on PATH is $(opencode --version 2>/dev/null) — the paper used $OPENCODE_VERSION"
fi

# --- 4. API key -----------------------------------------------------------
step "4/4  OpenRouter API key"
SECRETS="$HERE/.secrets.env"
if [ -f "$SECRETS" ] && grep -Eq 'OPENROUTER_API_KEY=.{6,}' "$SECRETS" 2>/dev/null; then
    ok ".secrets.env already holds a key"
elif [ -n "${OPENROUTER_API_KEY:-}" ]; then
    ( umask 177; printf 'export OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY" > "$SECRETS" )
    ok "captured OPENROUTER_API_KEY from your shell into .secrets.env (chmod 600, git-ignored)"
elif [ -t 0 ]; then
    warn "no key found. Paste your OpenRouter key (sk-or-…) and press Enter, or just Enter to skip:"
    printf '  key> '
    read -r -s KEY || true; echo
    if [ -n "${KEY:-}" ]; then
        ( umask 177; printf 'export OPENROUTER_API_KEY=%s\n' "$KEY" > "$SECRETS" )
        ok "saved to .secrets.env (chmod 600, git-ignored)"
    else
        warn "skipped — copy .env.example to .secrets.env and fill it in"
    fi
else
    warn "non-interactive shell — export OPENROUTER_API_KEY=… or fill in .secrets.env"
fi

b ""
b "Done. Next:"
echo "  source .venv/bin/activate && source .secrets.env"
echo "  python -m trilemma.run --config configs/main/glm-5.3-flash_s0.yaml --dry-run   # free"
echo "  python -m trilemma.run --config configs/main/glm-5.3-flash_s0.yaml             # one rollout"
