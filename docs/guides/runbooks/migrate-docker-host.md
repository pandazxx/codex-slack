# Runbook: Migrate an instance (and its volumes) to another Docker host

**Trigger:** you are moving a running codex-slack singleton (staging or prod, compose project `codex-slack`) from one Docker host to another, e.g. hardware replacement, new VM, or a new cloud region.

**Impact:** downtime from the moment master stops on the source until the target passes its healthcheck. Plan on roughly the time it takes to copy every volume over the network. Agents stop replying during the window. Messages sent during the window are lost, so freeze usage first.

**Who runs it:** a human operator or `senior-sre`. The `sre` operator has no runbook in `.sre/operations/` for this.

> **Never** run `docker compose down -v`, `docker volume rm`, or `docker volume prune` on the source host until the rollback window (step 9) has ended. The source volumes are your rollback.

---

## 0. What has to move

All state lives in Docker named volumes. Containers are disposable: master re-creates every agent container on startup (`_respawn_agents` in `src/master/main.py`) and re-attaches it to the existing volumes by name.

| Volume | Mounted at | Contents |
|---|---|---|
| `codex-slack_postgres_data` | `/var/lib/postgresql/data` (postgres) | All DB data: workspaces, topics, messages, sessions, agent configs |
| `codex-slack_master_data` | `/opt/codex-slack/data/master` (master) | Attachments, DB snapshots, `master_data.db` for SQLite installs |
| `codex-claude-<workspace_id>` | `/home/appuser/.claude` (agent) | Claude Code session state, one per workspace |
| `codex-codex-<workspace_id>` | `/home/appuser/.codex` (agent) | Codex CLI state, one per workspace |
| `agent-workspace-<workspace_name>` | `/workspace` (agent) | Cloned repo, including any **uncommitted agent work** |

For a dev stack, the compose volumes are prefixed with the branch slug (`<slug>_master_data`) instead of `codex-slack_`. Set `PROJECT=<slug>` below.

Host-side files are **not** in volumes. Copy or recreate them on the target separately:

- `.env` on the operator machine (`POSTGRES_PASSWORD`, `COMPOSE_PROFILES`, tokens). **`POSTGRES_PASSWORD` must stay the same**, because the password lives inside the copied data directory.
- Whatever `MASTER_CODEX_AUTH_JSON_PATH`, `MASTER_SSH_KNOWN_HOSTS_PATH`, and `MASTER_SSH_AUTH_SOCK_PATH` point to on the host.

## 1. Pre-flight checks

Run these from the operator machine (the one with the repo checkout and `.env`). Set:

```bash
export SRC=ssh://ubuntu@<old-host>      # current host
export DST=ssh://ubuntu@<new-host>      # new host
export PROJECT=codex-slack              # or the dev branch slug
```

1. The target has Docker and Compose >= 2.20:
   `DOCKER_HOST=$DST docker version && DOCKER_HOST=$DST docker compose version`
2. **Both hosts use the same CPU architecture**, because the Postgres data directory is binary and tied to the architecture:
   `DOCKER_HOST=$SRC docker info -f '{{.Architecture}}'` and the same command for `$DST`. If the values differ (for example x86_64 vs aarch64), do **not** copy `postgres_data`. Use the logical path in step 3b instead.
3. The target can pull the master image from `$REGISTRY` (`docker login` on the target if the registry is private).
4. The target has enough free disk space. Get the size of each volume on the source:
   ```bash
   for v in $(DOCKER_HOST=$SRC docker volume ls -q | grep -E "^(${PROJECT}_|codex-claude-|codex-codex-|agent-workspace-)"); do
     printf '%s\t' "$v"; DOCKER_HOST=$SRC docker run --rm -v "$v":/d:ro alpine du -sh /d | cut -f1
   done
   ```
5. Note the image tag currently deployed. The target must run the **same** tag, so the schema revision matches the data:
   `DOCKER_HOST=$SRC docker inspect ${PROJECT}-master-1 -f '{{.Config.Image}}'`

## 2. Freeze and take a logical backup (safety net)

Tell users the instance is going down. Then take a `pg_dump`. It is independent of the volume copy and works across architectures and Postgres versions:

```bash
DOCKER_HOST=$SRC docker exec ${PROJECT}-postgres-1 \
  pg_dump -U master -d master --no-owner > "master-pg-$(date +%Y%m%d%H%M).sql"
```

## 3. Stop the source stack (keep the volumes)

Stop master first so nothing writes to the DB, then the agents, then everything else:

```bash
DOCKER_HOST=$SRC docker stop ${PROJECT}-master-1
DOCKER_HOST=$SRC docker ps -q --filter name=codex-agent- | xargs -r env DOCKER_HOST=$SRC docker stop
DOCKER_HOST=$SRC docker stop ${PROJECT}-postgres-1 ${PROJECT}-mosquitto-1
```

Use `docker stop`, not `docker compose down -v`. Stopping without removing gives the fastest rollback: `docker start` brings the stack back.

## 3a. Copy the volumes (same architecture)

This streams each volume as a tar archive from the source daemon to the target daemon through the operator machine. No temp files are written. GNU tar with `--numeric-owner` keeps UIDs intact (postgres runs as UID 70, agents as `appuser`).

Compose-managed volumes are created on the target with compose labels, so `docker compose up` adopts them without warnings:

```bash
copy_volume() {  # copy_volume <name> [compose-volume-key]
  local v="$1" key="${2:-}"
  if [ -n "$key" ]; then
    DOCKER_HOST=$DST docker volume create \
      --label com.docker.compose.project="$PROJECT" \
      --label com.docker.compose.volume="$key" "$v" >/dev/null
  else
    DOCKER_HOST=$DST docker volume create "$v" >/dev/null
  fi
  echo "==> $v"
  DOCKER_HOST=$SRC docker run --rm -v "$v":/from:ro debian:bookworm-slim \
      tar -C /from --numeric-owner -cpf - . \
    | DOCKER_HOST=$DST docker run --rm -i -v "$v":/to debian:bookworm-slim \
      tar -C /to --numeric-owner -xpf -
}

copy_volume "${PROJECT}_master_data"   master_data
copy_volume "${PROJECT}_postgres_data" postgres_data   # skip if architectures differ

for v in $(DOCKER_HOST=$SRC docker volume ls -q | grep -E '^(codex-claude-|codex-codex-|agent-workspace-)'); do
  copy_volume "$v"
done
```

For a slow link, add compression: pipe through `gzip -1` on the source side (`... -cpf - . | gzip -1`) and extract with `-xzpf -` on the target side.

**Without `DOCKER_HOST` over SSH:** run the same pair on the hosts themselves, joined by `ssh`:
`docker run --rm -v V:/from:ro debian:bookworm-slim tar -C /from --numeric-owner -cpf - . | ssh new-host "docker volume create V && docker run --rm -i -v V:/to debian:bookworm-slim tar -C /to --numeric-owner -xpf -"`

## 3b. Different architecture or Postgres major: logical restore

Skip `postgres_data` in step 3a, and copy all the other volumes as shown there. Bring up only Postgres on the target (empty volume, same `POSTGRES_PASSWORD`), then restore the dump from step 2:

```bash
DOCKER_HOST=$DST COMPOSE_PROFILES=db-bundled \
  MASTER_RUNTIME_IMAGE=placeholder IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  docker compose -p $PROJECT -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml \
  up -d postgres
DOCKER_HOST=$DST docker exec -i ${PROJECT}-postgres-1 psql -U master -d master -v ON_ERROR_STOP=1 < master-pg-<timestamp>.sql
```

## 4. Verify the copy

Compare file counts and byte totals for every copied volume:

```bash
for v in $(DOCKER_HOST=$SRC docker volume ls -q | grep -E "^(${PROJECT}_|codex-claude-|codex-codex-|agent-workspace-)"); do
  a=$(DOCKER_HOST=$SRC docker run --rm -v "$v":/d:ro alpine sh -c 'find /d | wc -l; du -s /d | cut -f1' | tr '\n' ' ')
  b=$(DOCKER_HOST=$DST docker run --rm -v "$v":/d:ro alpine sh -c 'find /d | wc -l; du -s /d | cut -f1' | tr '\n' ' ')
  [ "$a" = "$b" ] && echo "OK   $v" || echo "DIFF $v  src=[$a] dst=[$b]"
done
```

Small `du` differences can come from filesystem block sizes. A different **file count** is a real problem: re-copy that volume.

## 5. Recreate host-side files on the target

- Copy the files behind `MASTER_CODEX_AUTH_JSON_PATH` / `MASTER_SSH_KNOWN_HOSTS_PATH` to the same paths on the new host, or change the paths in `.env`.
- Make sure the SSH agent socket at `MASTER_SSH_AUTH_SOCK_PATH` exists on the new host (or unset it).
- Leave `DOCKER_GID` unset so `just deploy` detects it on the new host. The docker group GID often differs between hosts.

## 6. Deploy on the target

Point the deploy target at the new host and deploy the **same tag** you noted in step 1.5:

```bash
# in .env (or the shell): STAGING_DOCKER_HOST=$DST   (or PROD_DOCKER_HOST for prod)
just deploy staging <same-tag>
```

The fixed project name `-p codex-slack` makes Compose attach to the copied `codex-slack_*` volumes. If master logs `DB schema is behind code`, you deployed a newer tag. Either redeploy the old tag, or follow `.sre/operations/db-snapshot-migrate.md`.

## 7. Verify the new instance

1. `curl -sf http://<new-host>:8080/health` returns `{"status":"ok",...}`, with the expected version.
2. `curl -s http://<new-host>:8080/schema` shows the schema is current.
3. `DOCKER_HOST=$DST docker logs ${PROJECT}-master-1 | grep master.respawned` lists one line per active workspace, and `DOCKER_HOST=$DST docker ps --filter name=codex-agent-` shows them running.
4. In the UI, the workspaces, topic history, and attachments are present. Send a message in one topic and confirm the agent replies and keeps its earlier session context.

## 8. Cut over clients

Update DNS, reverse proxy, bookmarks, and anything else that points at the old host. Update `STAGING_DOCKER_HOST` / `PROD_DOCKER_HOST` everywhere it is set (CI secrets, `.env`, `docs/sre.md` if it names the host).

## 9. Rollback, and cleaning up the source

**Rollback** (any time before the source is cleaned up): stop the target stack with `DOCKER_HOST=$DST docker compose -p $PROJECT ... stop`. Then on the source, run `docker start ${PROJECT}-postgres-1 ${PROJECT}-mosquitto-1 ${PROJECT}-master-1`. Master respawns the agents. Point clients back. Anything written on the target after the cut-over is **not** carried back.

**Cleanup:** only after the new host has run cleanly for an agreed period (at least a few days), and with a fresh `pg_dump` from the **target** in hand, remove the old stack and its volumes:

```bash
DOCKER_HOST=$SRC docker compose -p $PROJECT -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml down
# then, deliberately and one by one, `docker volume rm` the volumes listed in step 0
```

## Escalation

If the file counts disagree after a re-copy, Postgres refuses to start on the target ("database files are incompatible"), or master exits on schema checks you cannot explain: roll back (step 9), keep both hosts' volumes untouched, and escalate to `senior-sre` with the logs from `DOCKER_HOST=$DST docker logs ${PROJECT}-postgres-1` and `${PROJECT}-master-1`.
