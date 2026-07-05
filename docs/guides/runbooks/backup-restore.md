# Runbook: Backup and Restore

**Trigger:** Scheduled backup, host migration, disaster recovery, or rebuilding on a new Docker host.

**Impact:** No downtime required for backup. Restore requires bringing down the old host (or simply not starting it) and bringing up the new one.

---

## What is durable vs ephemeral

| Component | State | Backup needed? |
|-----------|-------|---------------|
| `master_data` Docker volume (SQLite) | Durable | Yes — all workspaces, topics, messages, staff configs |
| `codex-claude-{workspace_id}` volumes | Durable | Yes — Claude Code LLM session state per workspace |
| `codex-codex-{workspace_id}` volumes | Durable | Yes (optional) — Codex agent config and auth per workspace |
| `.env` file | Durable | Yes — API keys and secrets |
| Workspace git clones + worktrees | Ephemeral | No — live in agent container writable layer; re-cloned from GitHub on respawn |
| `mosquitto` container | Ephemeral | No — `persistence false`, no durable state |
| Agent containers (`codex-agent-*`) | Ephemeral | No — master respawns them on demand |
| Docker images | Registry-sourced | No — pulled from registry at deploy time |
| Compose files | In git | No — clone the repo on the new host |

---

## Backup

Run all steps on the **source host** (or from a machine with `DOCKER_HOST` pointing to it).

### Step 1 — list workspace IDs

Use a temporary sidecar container with sqlite3 to query the database volume:

```bash
docker run --rm \
  -v master_data:/data:ro \
  alpine sqlite3 /data/master_data.db "SELECT id FROM workspaces;"
```

Save the output — you need this list for step 3.

Alternatively, if you prefer not to query, you can skip this step and back up all Claude volumes that exist on the host:

```bash
# List all claude-session volumes currently on the host
docker volume ls -q | grep "^codex-claude-"
```

### Step 2 — back up the SQLite database

Safe to run while master is running (SQLite WAL mode makes the file copy-safe):

```bash
mkdir -p ./codex-backup

docker cp codex-slack-master:/opt/codex-slack/data/master/master_data.db \
  ./codex-backup/master_data-$(date +%Y%m%d-%H%M%S).db
```

### Step 3 — back up Claude session volumes

LLM conversation context is preserved in `codex-claude-{workspace_id}` volumes. Back these up:

**Option A** — if you have workspace IDs from step 1:

```bash
WORKSPACE_IDS="ws-id-1 ws-id-2 ws-id-3"   # fill in from step 1

for WID in $WORKSPACE_IDS; do
  docker run --rm \
    -v codex-claude-${WID}:/data:ro \
    -v $(pwd)/codex-backup:/backup \
    alpine tar czf /backup/claude-sessions-${WID}.tar.gz /data
  echo "backed up claude sessions for workspace ${WID}"
done
```

**Option B** — automatically back up all claude-session volumes on the host:

```bash
docker volume ls -q | grep "^codex-claude-" | while read VOL; do
  WID=${VOL#codex-claude-}
  docker run --rm \
    -v ${VOL}:/data:ro \
    -v $(pwd)/codex-backup:/backup \
    alpine tar czf /backup/claude-sessions-${WID}.tar.gz /data
  echo "backed up claude sessions for workspace ${WID}"
done
```

### Step 3b — (optional) back up Codex config volumes

Agent configuration for Codex is stored in `codex-codex-{workspace_id}` volumes. This is optional — if not backed up, Codex agents will be re-initialized on first use:

```bash
docker volume ls -q | grep "^codex-codex-" | while read VOL; do
  WID=${VOL#codex-codex-}
  docker run --rm \
    -v ${VOL}:/data:ro \
    -v $(pwd)/codex-backup:/backup \
    alpine tar czf /backup/codex-config-${WID}.tar.gz /data
  echo "backed up codex config for workspace ${WID}"
done
```

### Step 4 — back up the env file

```bash
cp .env ./codex-backup/dot-env.bak
```

### Step 5 — transfer the backup archive

```bash
# Example using rsync — adjust destination as needed
rsync -av ./codex-backup/ ubuntu@<new-host>:~/codex-backup/
```

---

## Restore on a new Docker host

Run all steps from a machine that has SSH access to the new host, with `DOCKER_HOST` set:

```bash
export DOCKER_HOST=ssh://ubuntu@<new-host-ip>
```

### Step 1 — install Docker and bootstrap Traefik

Install Docker + Compose on the new host, then bootstrap shared Traefik infrastructure (idempotent):

```bash
git clone https://github.com/<org>/codex-slack.git
cd codex-slack

docker network create sre-traefik-public 2>/dev/null || true
docker compose -p sre-host-infra \
  -f .sre/host-infra/docker-compose.yml up -d
```

Verify Traefik is running:

```bash
docker compose -p sre-host-infra ps
```

### Step 2 — restore the env file

```bash
cp ./codex-backup/dot-env.bak .env
```

If the new host's Docker group GID differs from the old host, update `DOCKER_GID` in `.env`:

```bash
# Find the docker group GID on the new host
ssh ubuntu@<new-host-ip> "getent group docker | cut -d: -f3"
# Then set DOCKER_GID=<that-value> in .env
```

### Step 3 — restore the SQLite database

```bash
BACKUP_FILE="./codex-backup/master_data-<timestamp>.db"   # use the file from step 2 of backup

docker volume create master_data

docker run --rm \
  -v master_data:/restore \
  -v $(pwd)/codex-backup:/backup:ro \
  alpine sh -c "cp /backup/$(basename ${BACKUP_FILE}) /restore/master_data.db && \
                chmod 644 /restore/master_data.db"
```

### Step 4 — restore Claude session volumes

Automatically restore all Claude backup archives found in `./codex-backup/`:

```bash
for ARCHIVE in ./codex-backup/claude-sessions-*.tar.gz; do
  if [ -f "$ARCHIVE" ]; then
    WID=$(basename "$ARCHIVE" | sed 's/claude-sessions-//; s/.tar.gz//')
    docker volume create codex-claude-${WID}
    docker run --rm \
      -v codex-claude-${WID}:/restore \
      -v $(pwd)/codex-backup:/backup:ro \
      alpine tar xzf /backup/claude-sessions-${WID}.tar.gz -C /restore --strip-components=1
    echo "restored claude sessions for workspace ${WID}"
  fi
done
```

### Step 4b — (optional) restore Codex config volumes

If you backed up Codex config volumes in step 3b, restore them:

```bash
for ARCHIVE in ./codex-backup/codex-config-*.tar.gz; do
  if [ -f "$ARCHIVE" ]; then
    WID=$(basename "$ARCHIVE" | sed 's/codex-config-//; s/.tar.gz//')
    docker volume create codex-codex-${WID}
    docker run --rm \
      -v codex-codex-${WID}:/restore \
      -v $(pwd)/codex-backup:/backup:ro \
      alpine tar xzf /backup/codex-config-${WID}.tar.gz -C /restore --strip-components=1
    echo "restored codex config for workspace ${WID}"
  fi
done
```

### Step 5 — pull images and start

```bash
docker compose pull
docker compose up -d
```

### Step 6 — verify

```bash
curl http://master.<branch-slug>.<new-host-ip-dashed>.nip.io/health
# expected: {"status":"ok","version":"..."}
```

Check that all services are up:

```bash
docker compose ps
```

---

## What to expect after restore

- **UI shows all data immediately.** Workspaces, topics, and message history load from the restored SQLite DB.
- **Agent containers are not running.** This is normal — master spawns them on first use when a user sends a message to a workspace. There is no manual step required.
- **Claude sessions resume.** LLM conversation context is preserved from the restored volumes; agents pick up where they left off.
- **Git worktrees are re-created on first use.** Agent containers don't persist git clones or worktrees (they live in the container's ephemeral layer). When an agent container starts, it re-clones the repo from GitHub and re-creates the topic worktrees based on metadata in SQLite. All branches and commits are safe on GitHub — nothing is lost. Worktree re-creation is automatic and transparent; no manual steps are needed.

---

## Escalation

If the restore fails at step 3 (SQLite) or step 4 (volumes), the most common causes are:

- Permissions on the restored file: confirm `master_data.db` is readable (`chmod 644`).
- Wrong `--strip-components` depth for a volume archive: inspect the tar with `tar tzf <file> | head` and adjust.
- Volume already exists with stale data: `docker volume rm <name>` and re-run the restore step.

If master fails to start, check logs: `docker logs codex-slack-master`.
