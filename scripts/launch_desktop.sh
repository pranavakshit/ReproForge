#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RENDERER="${1:-hardware}"
RELEASE_BIN="${REPO_ROOT}/desktop/target/release/reproforge_desktop"

if [ -x "${REPO_ROOT}/.venv/bin/alembic" ] && [ -f "${REPO_ROOT}/alembic.ini" ]; then
    PYTHONPATH="${REPO_ROOT}" "${REPO_ROOT}/.venv/bin/alembic" upgrade head >/dev/null || true
fi

if [ ! -x "${RELEASE_BIN}" ]; then
    echo "[ReproForge] Building native release binary..."
    cargo build --release --manifest-path "${REPO_ROOT}/desktop/Cargo.toml"
fi

echo "[ReproForge] Launching Desktop Studio (--renderer=${RENDERER})..."
exec "${RELEASE_BIN}" "--renderer=${RENDERER}"
