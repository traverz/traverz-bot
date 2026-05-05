#!/bin/sh
set -e

TRAVERZ_DIR="${HOME}/.traverz"
WORKSPACE_DIR="${TRAVERZ_WORKSPACE:-${TRAVERZ_DIR}/workspace}"
GCS_BUCKET="${TRAVERZ_GCS_BUCKET:-}"

# ── Optional: mount GCS bucket for persistent memory ──────────────────────
if [ -n "${GCS_BUCKET}" ]; then
    mkdir -p "${WORKSPACE_DIR}"
    echo "[traverz] Mounting gs://${GCS_BUCKET} at ${WORKSPACE_DIR} ..."
    gcsfuse \
        --implicit-dirs \
        --file-mode=0644 \
        --dir-mode=0755 \
        --uid=1000 \
        --gid=1000 \
        "${GCS_BUCKET}" "${WORKSPACE_DIR}" || {
        echo "[traverz] WARNING: gcsfuse mount failed, using local workspace." >&2
    }
fi

# ── Validate writable workspace ────────────────────────────────────────────
mkdir -p "${WORKSPACE_DIR}"
if [ ! -w "${WORKSPACE_DIR}" ]; then
    echo "[traverz] ERROR: workspace ${WORKSPACE_DIR} is not writable." >&2
    exit 1
fi

# ── Config: use traverz_config.json if no config exists yet ───────────────
CONFIG_PATH="${HOME}/.traverz/config.json"
if [ ! -f "${CONFIG_PATH}" ] && [ -f "/app/traverz_config.json" ]; then
    mkdir -p "${HOME}/.traverz"
    cp /app/traverz_config.json "${CONFIG_PATH}"
    echo "[traverz] Installed default config from traverz_config.json"
fi
# ── Agent workspace: install AGENTS.md so the bot has Traverz instructions ───
AGENT_WORKSPACE="/tmp/workspace"
if [ -f "/app/traverz_agents.md" ]; then
    mkdir -p "${AGENT_WORKSPACE}"
    cp /app/traverz_agents.md "${AGENT_WORKSPACE}/AGENTS.md"
    echo "[traverz] Installed AGENTS.md to agent workspace"
fi
exec traverz "$@"
