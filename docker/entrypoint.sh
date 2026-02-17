#!/usr/bin/env bash
set -euo pipefail

SESSION_ARGS=()
if [[ -n "${CODEX_SESSION_ID:-}" ]]; then
  SESSION_ARGS+=("--session-id" "${CODEX_SESSION_ID}")
fi

exec "$@" "${SESSION_ARGS[@]}"
