# FAQ

Frequently asked questions about operating codex-slack v3.

---

**Q: What does codex-slack v3 do?**

codex-slack v3 is a self-hosted web application that lets you chat with LLM coding agents (Claude Code or Codex) against a Git repository. You create workspaces (one per repo), create topics (chat threads), and send messages. The system routes prompts to agent containers via MQTT and streams responses back to the browser over WebSocket. There is no Slack or Discord dependency.

---

**Q: What services does the stack include?**

Three in the default configuration: `master` (FastAPI app on port 8080), `mosquitto` (MQTT broker, internal only), and `postgres` (bundled `postgres:17-alpine`, internal only, started when `COMPOSE_PROFILES=db-bundled`). Agent containers are spawned dynamically by master when you create a workspace — they are not defined in `docker-compose.yml`. When using a managed (external) database, `postgres` is not started and only `master` and `mosquitto` run.

---

**Q: Where is my data stored?**

Since v5.0, all workspace, topic, message, session, and agent configuration data is in Postgres by default. The bundled `postgres:17-alpine` service stores its data in the `postgres_data` Docker volume. Attachments are in the `master_data` volume under `attachments/`. Each agent container has two persistent named volumes: `codex-claude-{workspace_id}` mounted at `/home/appuser/.claude` (Claude session state) and `codex-codex-{workspace_id}` mounted at `/home/appuser/.codex` (Codex config and auth).

For SQLite installs (opt-in via `DATABASE_URL=sqlite+pysqlite:///...`), the database file is at `/opt/codex-slack/data/master/master_data.db` inside the `master_data` volume.

---

**Q: How do I choose between the `claude` and `codex` agents?**

Both are created by default for each workspace. Use `@claude` for Claude Code (resumable sessions, stream-json output, supports `subagent` flag). Use `@codex` for Codex (`codex exec --json --dangerously-bypass-approvals-and-sandbox -s danger-full-access --ephemeral`). You can add custom named agents via the Agents section in the workspace UI.

---

**Q: What does archiving a workspace or topic do?**

Archiving sets an `archived_at` timestamp in the database — no data is deleted. For workspaces, it also cascades to all active topics and stops the agent container. Archived workspaces and topics are viewable as read-only in the UI at `/archived` and `/workspaces/:id/archived-topics`.

---

**Q: Why does the agent respond with `(no output)` or an empty reply?**

Most likely the `claude` CLI is returning output but the `result` event is absent. This happens if `--verbose` is missing from the CLI invocation. The agent always runs `claude --print --verbose --output-format stream-json --dangerously-skip-permissions`. Check the agent container logs:

```bash
docker logs codex-agent-<workspace_id>
```

If `stream-json` events are present but `type: result` is missing, the `--verbose` flag was not included.

---

**Q: What happens when a Claude session expires?**

The agent detects the string `No conversation found with session ID` in the claude output and automatically retries the same prompt without `--resume`. The new session ID is stored in the `sessions` table. No user action is required.

---

**Q: Where is the SQLite database?**

SQLite is no longer the default since v5.0 — Postgres is. If you opted in to SQLite by setting `DATABASE_URL=sqlite+pysqlite:///...`, the file is at the path in that URL (typically `/opt/codex-slack/data/master/master_data.db` inside the `master_data` volume):

```bash
docker cp codex-slack-master:/opt/codex-slack/data/master/master_data.db ./master_data.db
sqlite3 ./master_data.db ".tables"
```

---

**Q: Master refuses to start after upgrading to v5.0 — what do I do?**

You are seeing the SQLite-upgrade safety net. Master found a non-empty `master_data.db` in `MASTER_DATA_DIR` while `DATABASE_URL` is unset. The default is now Postgres, and master refuses to silently ignore existing SQLite data.

You have two options:

- **Keep SQLite:** Add `DATABASE_URL=sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db` to `.env` and restart. No data migration needed.
- **Migrate to Postgres:** Follow [`docs/guides/runbooks/migrate-sqlite-to-postgres.md`](../guides/runbooks/migrate-sqlite-to-postgres.md). The short version: set `COMPOSE_PROFILES=db-bundled` and `POSTGRES_PASSWORD=<hex>`, start Postgres, run `scripts/db.sh snapshot-and-migrate`, then set `DATABASE_URL` to the Postgres URL.

---

**Q: Master exits with "DB schema is behind code" — what do I do?**

A code upgrade introduced a new Alembic migration that has not been applied to the database yet. Master refuses to start to prevent running against a mismatched schema.

Apply the pending migrations before restarting:

```bash
# From inside the master container (or a one-off container with --no-deps):
bash scripts/db.sh snapshot-and-migrate   # snapshot first (recommended)
# or, if you don't need the snapshot:
bash scripts/db.sh migrate
```

For a running staging stack, use the SRE runbook: `.sre/operations/db-snapshot-migrate.md`.

---

**Q: Snapshot import says "database is in use" — what does that mean?**

The exact message is: `ERROR: target database is in use (lock timeout after 30s) — stop master before importing`.

This occurs when `snapshot import` tries to acquire an exclusive table lock on Postgres (needed for `--yes` truncate-and-reload) but another connection — typically a running master process — is holding the lock. Stop master before running the import:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml stop master
# Then re-run the import
```

Pass `--lock-timeout 0` to disable the timeout (not recommended — the import will hang indefinitely if master is still connected).

---

**Q: How do I inspect an agent's status?**

```bash
# Agent container logs
docker logs codex-agent-<workspace_id>

# Status file written by the agent worker startup stages
docker exec codex-agent-<workspace_id> cat /tmp/master-agent/status.json
```

---

**Q: The agent container is missing. How do I recover?**

Master respawns agent containers for all non-archived workspaces on startup. If a workspace's container is gone, restart master:

```bash
docker compose restart master
```

Master will detect the missing container and respawn it. If the workspace is archived, the container will not be respawned.

---

**Q: How do I deploy a new image to staging or production?**

Use `just deploy <env> <tag>`. The recipe resolves the tag to a digest, pulls the image to the target host, and replaces the running singleton stack in place. Rollback = `just deploy <env> <previous-tag>`. See `docs/sre.md` for the full recipe reference and `.sre/operations/deploy.md` for the operator runbook. The CD daemon was retired in ADR-0016; `just deploy` is the only staging/prod path.
