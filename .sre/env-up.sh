#!/usr/bin/env bash
# env-up.sh — spin up a dev environment for a branch.
# Called by the SRE subagent; called by humans only to resume a stopped env.
#
# Usage:
#   env-up.sh [BRANCH_SLUG]
#
# If BRANCH_SLUG is omitted, uses current git branch (sanitized).
# Idempotent: called twice on the same branch returns the existing env.
#
# Routing:
#   - Default: Traefik-routed at master.<branch>.dev.docker-testbed.local.
#     Requires the shared Traefik ingress on the same Docker host
#     (.sre/traefik-up.sh). Adds a /etc/hosts entry on the laptop pointing
#     master.<branch>.dev.docker-testbed.local at the testbed's IP.
#   - Fallback: per-branch published port (hash-derived from project name)
#     when Traefik is not reachable. Reach via SSH local-port-forward.
#
# See docs/sre-decisions/2026-05-07-traefik-multi-env-routing.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
export DOCKER_HOST="${DEV_DOCKER_HOST:-}"

if [[ -n "${1:-}" ]]; then
  BRANCH_SLUG="$1"
else
  BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD)
  BRANCH_SLUG=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-*$//' | cut -c1-32)
fi

PROJECT_NAME="${USER}-${BRANCH_SLUG}"
ENV_FILE="${REPO_ROOT}/.env.local.${BRANCH_SLUG}"
INGRESS_HOST="master.${BRANCH_SLUG}.dev.docker-testbed.local"

# Pick the per-mode override file (local bind-mount vs. remote no-bind).
if [[ -n "$DEV_DOCKER_HOST" ]]; then
  COMPOSE_OVERRIDE="docker-compose.remote.yml"
else
  COMPOSE_OVERRIDE="docker-compose.override.yml"
fi

echo "Setting up dev environment: $PROJECT_NAME"

# ---------------------------------------------------------------------------
# Detect Traefik availability on the target Docker host
# ---------------------------------------------------------------------------
# Traefik mode requires the shared `sre_ingress` network AND the Traefik
# container running. We check both. If either is missing, we fall back to
# port-binding mode and print the SSH-tunnel instructions.
TRAEFIK_MODE=0
if docker network inspect sre_ingress >/dev/null 2>&1 \
   && docker ps --filter "name=^sre-ingress-traefik$" --format '{{.Names}}' 2>/dev/null \
        | grep -q '^sre-ingress-traefik$'; then
  TRAEFIK_MODE=1
  echo "Traefik ingress detected: using label-based routing."
else
  echo "Traefik ingress NOT detected: falling back to per-branch published port."
  echo "  (run .sre/traefik-up.sh on the dev host to enable label-based routing)"
fi

# ---------------------------------------------------------------------------
# Derive a deterministic fallback port (used only in non-Traefik mode)
# ---------------------------------------------------------------------------
PORT_OFFSET=$(($(echo -n "$PROJECT_NAME" | md5sum | head -c 3 | xargs -I {} printf '%d' 0x{}) % 900))
MASTER_PORT=$((8080 + PORT_OFFSET))

# ---------------------------------------------------------------------------
# Auto-detect docker socket GID (for prod-shaped containers; harmless in dev)
# ---------------------------------------------------------------------------
if [[ -n "$DEV_DOCKER_HOST" ]]; then
  DOCKER_HOST_PART="${DEV_DOCKER_HOST#ssh://}"
  DOCKER_HOST_REMOTE="${DOCKER_HOST_PART%:*}"
  DOCKER_GID=$(ssh "$DOCKER_HOST_REMOTE" "stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 999" 2>/dev/null || echo 999)
else
  DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || stat -f '%Lg' /var/run/docker.sock 2>/dev/null || echo 999)
fi

# ---------------------------------------------------------------------------
# Manage the per-branch .env file (gitignored)
# ---------------------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# Local overrides for this branch — never commit.
# Uncomment and set API keys if testing integrations:
# ANTHROPIC_API_KEY=sk-...
# GH_TOKEN=ghp_...
# OPENAI_API_KEY=sk-...

# Container names scoped to this project (prevents collisions across envs).
MASTER_CONTAINER_NAME=${PROJECT_NAME}-master
MOSQUITTO_CONTAINER_NAME=${PROJECT_NAME}-mosquitto

# Branch slug used by Traefik labels and hostname.
BRANCH_SLUG=${BRANCH_SLUG}

# Fallback port (used only if Traefik is not running on the dev host).
MASTER_PORT=${MASTER_PORT}

# Docker group GID — auto-detected; do not edit unless you know what you're doing.
DOCKER_GID=${DOCKER_GID}
EOF
  echo "Created $ENV_FILE"
else
  # Refresh auto-detected fields without disturbing user-entered values.
  if grep -q "^DOCKER_GID=" "$ENV_FILE"; then
    sed -i.bak "s/^DOCKER_GID=.*/DOCKER_GID=${DOCKER_GID}/" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    echo "DOCKER_GID=${DOCKER_GID}" >> "$ENV_FILE"
  fi
  if grep -q "^BRANCH_SLUG=" "$ENV_FILE"; then
    sed -i.bak "s/^BRANCH_SLUG=.*/BRANCH_SLUG=${BRANCH_SLUG}/" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    echo "BRANCH_SLUG=${BRANCH_SLUG}" >> "$ENV_FILE"
  fi
fi

# Export the basics so docker compose interpolation sees them.
export MASTER_CONTAINER_NAME="${PROJECT_NAME}-master"
export MOSQUITTO_CONTAINER_NAME="${PROJECT_NAME}-mosquitto"
export BRANCH_SLUG
export MASTER_PORT

set -a
# shellcheck disable=SC1091
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
set +a

# ---------------------------------------------------------------------------
# In Traefik mode: ensure the sre_ingress network exists so compose can attach.
# In fallback mode: strip sre_ingress from the override on the fly.
# ---------------------------------------------------------------------------
EFFECTIVE_OVERRIDE="$COMPOSE_OVERRIDE"
TMP_OVERRIDE=""
if (( TRAEFIK_MODE == 1 )); then
  if ! docker network inspect sre_ingress >/dev/null 2>&1; then
    docker network create sre_ingress >/dev/null
  fi
else
  # Generate a one-shot override with the sre_ingress lines removed.
  TMP_OVERRIDE="$(mktemp -t sre-override.XXXXXX).yml"
  # Drop:
  #   - the `- sre_ingress` line under service-level networks:
  #   - the top-level networks: -> sre_ingress: { external: true, name: sre_ingress }
  # Keep everything else (including labels — Traefik labels are inert without
  # a Traefik instance to read them).
  awk '
    /^[[:space:]]*-[[:space:]]+sre_ingress[[:space:]]*$/ { next }
    /^[[:space:]]*sre_ingress:[[:space:]]*$/ { skip = 1; next }
    skip && /^[[:space:]]+(external|name):/ { next }
    skip && /^[^[:space:]]/ { skip = 0 }
    skip && /^[[:space:]]*$/ { next }
    { skip = 0; print }
  ' "$COMPOSE_OVERRIDE" > "$TMP_OVERRIDE"
  EFFECTIVE_OVERRIDE="$TMP_OVERRIDE"

  # In fallback mode we must publish the port from the master service. Append
  # a tiny additional override that re-introduces `ports:`. We do this in a
  # second file so the awk-rewritten override stays minimal.
  PORT_OVERRIDE="$(mktemp -t sre-port.XXXXXX).yml"
  cat > "$PORT_OVERRIDE" <<EOF
services:
  master:
    ports:
      - "${MASTER_PORT}:8080"
EOF
fi

trap '[[ -n "$TMP_OVERRIDE" ]] && rm -f "$TMP_OVERRIDE" "${PORT_OVERRIDE:-}"' EXIT

# ---------------------------------------------------------------------------
# Idempotent re-up if the env is already running.
# ---------------------------------------------------------------------------
CONTAINER_COUNT=$(docker compose -p "$PROJECT_NAME" ps -q 2>/dev/null | wc -l | tr -d ' ')
if [[ "$CONTAINER_COUNT" -gt 0 ]]; then
  echo "Environment already running. Re-applying compose to pick up any label changes..."
fi

# ---------------------------------------------------------------------------
# Build runtime image on the remote host (no bind mounts available).
# ---------------------------------------------------------------------------
if [[ -n "$DEV_DOCKER_HOST" ]]; then
  echo "Building runtime image on remote Docker host..."
  docker build -t codex-slack-master:runtime .
fi

# ---------------------------------------------------------------------------
# Bring up the stack.
# ---------------------------------------------------------------------------
COMPOSE_ARGS=( -f docker-compose.yml -f "$EFFECTIVE_OVERRIDE" )
if (( TRAEFIK_MODE == 0 )); then
  COMPOSE_ARGS+=( -f "$PORT_OVERRIDE" )
fi

echo "Starting services for $PROJECT_NAME (mode: $( ((TRAEFIK_MODE)) && echo traefik || echo fallback-port ))..."
docker compose "${COMPOSE_ARGS[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE" up -d

# ---------------------------------------------------------------------------
# Wait for health.
# ---------------------------------------------------------------------------
echo "Waiting for services to be healthy..."
attempt=0
max_attempts=30
while (( attempt < max_attempts )); do
  if docker compose -p "$PROJECT_NAME" ps master 2>/dev/null | grep -q "healthy\|running"; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if ! docker compose -p "$PROJECT_NAME" ps master 2>/dev/null | grep -q "healthy\|running"; then
  echo "ERROR: master service failed to start. Logs:"
  docker compose -p "$PROJECT_NAME" logs master
  exit 1
fi

# ---------------------------------------------------------------------------
# Manage /etc/hosts on the laptop (Traefik mode only).
# ---------------------------------------------------------------------------
manage_hosts_entry() {
  local hostname="$1" ip="$2"
  local hosts_file=/etc/hosts
  local marker="# sre-env: ${USER}/${BRANCH_SLUG}"
  local line="${ip}\t${hostname}\t${marker}"

  # Already present and up to date?
  if grep -qE "^[[:space:]]*[0-9.]+[[:space:]]+${hostname}([[:space:]]|$)" "$hosts_file" 2>/dev/null; then
    if grep -qE "^[[:space:]]*${ip}[[:space:]]+${hostname}([[:space:]]|$)" "$hosts_file" 2>/dev/null; then
      echo "/etc/hosts entry for ${hostname} is already current."
      return 0
    fi
    # Stale entry — remove it.
    if sudo -n true 2>/dev/null; then
      sudo sed -i.bak "/[[:space:]]${hostname}[[:space:]]/d" "$hosts_file" \
        && sudo rm -f "${hosts_file}.bak"
    else
      echo "Stale /etc/hosts entry for ${hostname} (different IP). Run:"
      echo "  sudo sed -i '' \"/[[:space:]]${hostname}[[:space:]]/d\" /etc/hosts"
      return 1
    fi
  fi

  if sudo -n true 2>/dev/null; then
    printf "%b\n" "$line" | sudo tee -a "$hosts_file" >/dev/null
    echo "Added /etc/hosts entry: ${ip} ${hostname}"
  else
    cat <<EOF
NOTE: cannot write /etc/hosts non-interactively. Add this line yourself:
  ${ip}    ${hostname}    ${marker}

(or run: echo "${ip} ${hostname} ${marker}" | sudo tee -a /etc/hosts)
EOF
    return 1
  fi
}

if (( TRAEFIK_MODE == 1 )); then
  # Resolve the testbed IP from the SSH host once. Prefer the first IPv4 we see.
  TESTBED_IP=""
  if [[ -n "$DEV_DOCKER_HOST" ]]; then
    DOCKER_HOST_PART="${DEV_DOCKER_HOST#ssh://}"
    DOCKER_HOST_REMOTE="${DOCKER_HOST_PART%:*}"
    DOCKER_HOST_REMOTE="${DOCKER_HOST_REMOTE#*@}"
    # Resolve via macOS dscacheutil to A records only.
    TESTBED_IP=$(dscacheutil -q host -a name "$DOCKER_HOST_REMOTE" 2>/dev/null \
                  | awk '/^ip_address:/ { print $2; exit }')
    if [[ -z "$TESTBED_IP" ]]; then
      # Fall back to host(1) if available.
      TESTBED_IP=$(host -t A "$DOCKER_HOST_REMOTE" 2>/dev/null \
                    | awk '/has address/ { print $4; exit }')
    fi
  else
    TESTBED_IP="127.0.0.1"
  fi

  if [[ -n "$TESTBED_IP" ]]; then
    manage_hosts_entry "$INGRESS_HOST" "$TESTBED_IP" || true
    # Also ensure the Traefik dashboard hostname resolves (one-time, harmless if duplicate).
    manage_hosts_entry "traefik.dev.docker-testbed.local" "$TESTBED_IP" || true
  else
    echo "Could not resolve testbed IP from \$DEV_DOCKER_HOST; skipping /etc/hosts update."
    echo "Add manually: <testbed-ip>  ${INGRESS_HOST}"
  fi
fi

# ---------------------------------------------------------------------------
# Print structured access info.
# ---------------------------------------------------------------------------
echo ""
echo "Environment ready: ${BRANCH_SLUG}"
echo "  Project:    ${PROJECT_NAME}"
echo "  Host:       ${DEV_DOCKER_HOST:-local}"
docker compose -p "$PROJECT_NAME" ps

if (( TRAEFIK_MODE == 1 )); then
  cat <<EOF

HTTP endpoints (via Traefik):
  Web UI:     http://${INGRESS_HOST}/
  API docs:   http://${INGRESS_HOST}/docs
  Health:     http://${INGRESS_HOST}/health
  Dashboard:  http://traefik.dev.docker-testbed.local/dashboard/

Direct access:
  Master shell:  DOCKER_HOST=\$DEV_DOCKER_HOST docker compose -p ${PROJECT_NAME} exec -it master bash
  Master logs:   DOCKER_HOST=\$DEV_DOCKER_HOST docker compose -p ${PROJECT_NAME} logs -f master
  MQTT messages: DOCKER_HOST=\$DEV_DOCKER_HOST docker compose -p ${PROJECT_NAME} exec mosquitto mosquitto_sub -h localhost -t '#' -v

Teardown: .sre/env-down.sh ${BRANCH_SLUG}
EOF
else
  cat <<EOF

HTTP endpoints (fallback per-branch port — Traefik not running):
  Web UI:     http://localhost:${MASTER_PORT}/
  API docs:   http://localhost:${MASTER_PORT}/docs
  Health:     http://localhost:${MASTER_PORT}/health

To reach from your laptop, open an SSH tunnel:
  ssh -L ${MASTER_PORT}:localhost:${MASTER_PORT} ${DOCKER_HOST_REMOTE:-ubuntu@docker-testbed.local}

Direct access:
  Master shell:  DOCKER_HOST=\$DEV_DOCKER_HOST docker compose -p ${PROJECT_NAME} exec -it master bash
  Master logs:   DOCKER_HOST=\$DEV_DOCKER_HOST docker compose -p ${PROJECT_NAME} logs -f master
  MQTT messages: DOCKER_HOST=\$DEV_DOCKER_HOST docker compose -p ${PROJECT_NAME} exec mosquitto mosquitto_sub -h localhost -t '#' -v

Note: To switch to label-based routing without per-branch ports, run
  .sre/traefik-up.sh   (on the dev host)
  .sre/env-up.sh ${BRANCH_SLUG}   (re-up this env)

Teardown: .sre/env-down.sh ${BRANCH_SLUG}
EOF
fi
