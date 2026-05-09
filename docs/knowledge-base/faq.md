# FAQ

Frequently asked questions about operating codex-slack v3.

---

**Q: What does codex-slack v3 do?**

codex-slack v3 is a self-hosted web application that lets you chat with LLM coding agents (Claude Code or Codex) against a Git repository. You create workspaces (one per repo), create topics (chat threads), and send messages. The system routes prompts to agent containers via MQTT and streams responses back to the browser over WebSocket. There is no Slack or Discord dependency.

---

**Q: What services does the stack include?**

Two: `master` (FastAPI app on port 8080) and `mosquitto` (MQTT broker, internal only). Agent containers are spawned dynamically by master when you create a workspace — they are not defined in `docker-compose.yml`.

---

**Q: Where is my data stored?**

All workspace, topic, message, session, and agent configuration data is in a SQLite database at `/opt/codex-slack/data/master/master_data.db` inside the master container. This path is mounted from the `master_data` Docker volume. Each agent container has two persistent named volumes: `codex-claude-{workspace_id}` mounted at `/home/appuser/.claude` (Claude session state) and `codex-codex-{workspace_id}` mounted at `/home/appuser/.codex` (Codex config and auth).

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

Inside the master container at `/opt/codex-slack/data/master/master_data.db`. This file lives on the `master_data` Docker volume. To inspect it:

```bash
docker cp codex-slack-master:/opt/codex-slack/data/master/master_data.db ./master_data.db
sqlite3 ./master_data.db ".tables"
```

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

**Q: Can I run the CD daemon for automated deployments?**

Yes. See [`docs/guides/runbooks/cd-daemon.md`](../guides/runbooks/cd-daemon.md) for the full setup guide. The CD daemon polls GHCR for a new image digest, redeploys master via compose, and rolls back automatically if health check fails.
