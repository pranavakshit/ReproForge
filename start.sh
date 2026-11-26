#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [[ "${1:-}" == "--api" ]]; then
    PORT="${2:-8765}"
    export PYTHONPATH="${SCRIPT_DIR}"
    if [ -x "${SCRIPT_DIR}/.venv/bin/alembic" ] && [ -f "${SCRIPT_DIR}/alembic.ini" ]; then
        "${SCRIPT_DIR}/.venv/bin/alembic" upgrade head >/dev/null || true
    fi
    echo "[ReproForge] Starting optional FastAPI server on http://127.0.0.1:${PORT} ..."
    exec "${SCRIPT_DIR}/.venv/bin/uvicorn" backend.app.api.app:app --host 127.0.0.1 --port "${PORT}"
fi

exec "${SCRIPT_DIR}/scripts/launch_desktop.sh" "${1:-hardware}"
