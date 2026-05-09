# 0005 v3.0 System Architecture

- Status: accepted
- Date: 2026-05-02

## Context

The current system routes user prompts through Slack or Discord. One agent maps
to exactly one channel and one repository. Master dispatches prompts into agent
containers via `podman exec` — a synchronous, one-directional call. There are
no persistent LLM sessions; every exec is stateless. Platform-specific quirks
(Slack file downloads, Discord snowflake IDs, slash-command registration) are a
significant ongoing maintenance surface.

The v3.0 requirements call for a major operating-model change:

- own chat frontend (no longer Slack/Discord-dependent)
- workspace/topic model (one repo, many parallel chat threads)
- bi-directional real-time agent communication (thinking feedback)
- persistent LLM sessions scoped per topic
- multi-subagent routing within a topic

These requirements are interrelated: dropping Slack/Discord necessitates an own
frontend; real-time thinking feedback necessitates bi-directional transport;
the workspace/topic model requires durable structured storage beyond the current
flat JSON registry.

## Decision

Adopt the v3.0 system architecture across six interrelated decisions:

### 1. Drop Slack/Discord frontends; implement own web frontend

Replace Slack and Discord adapter code with a self-hosted web UI served by the
master container. No migration path is provided — v3.0 is a fresh-start
deployment with a clean data model.

Frontend stack: Vue 3 + Vite. Vite produces a static bundle baked into the
master image at build time. FastAPI (already the Python web runtime) serves the
bundle and the REST API. No Node.js in the running container.

### 2. Adopt workspace/topic operating model

Replace the one-channel-per-agent mapping with a two-level hierarchy:

- **Workspace** — one per repository. Has a set of registered staff agent
  configurations and one agent container.
- **Topic** — one per chat thread within a workspace. Has its own git worktree,
  feature branch, and LLM session per agent.

### 3. Use MQTT (Mosquitto) for bi-directional master-agent communication

Replace `podman exec` prompt dispatch with an MQTT pub/sub model. Mosquitto
runs as a separate container in the compose stack.

MQTT topic structure:

```
codex-slack/workspace/{wid}/topic/{tid}/prompt    # master → agent
codex-slack/workspace/{wid}/topic/{tid}/response  # agent → master
codex-slack/workspace/{wid}/topic/{tid}/status    # agent → master (thinking)
```

Status payload: `{"state": "thinking"}` / `{"state": "idle"}`

Response payload: `{"last_response": "...", "transcript": "..."}`

Master bridges `response` and `status` MQTT messages to the browser over
WebSocket.

### 4. Use SQLite for workspace/topic/chat storage

Replace the flat `agents.json` registry with a SQLite database owned by the
master container on a durable volume. Schema covers five tables:

| Table | Key columns |
|---|---|
| `workspaces` | id, name, repo_url, container_name, created_at |
| `workspace_agents` | id, workspace_id, agent_name, adapter |
| `topics` | id, workspace_id, subject, branch_name, worktree_path, created_at |
| `sessions` | id, topic_id, agent_name, adapter, llm_session_id |
| `messages` | id, topic_id, sender, text, attachments_json, created_at |

SQLite is chosen for zero infra overhead, single-file backup, and compatibility
with the existing pattern of file-based durable state. PostgreSQL migration is
planned for a later release when multi-master or read-replica requirements arise.

### 5. Support persistent LLM sessions for both Claude Code and Codex

Sessions are topic-scoped. Creating a new topic creates a new session.
Switching topics resumes the corresponding session.

- **Claude Code:** `claude --print --verbose --output-format stream-json --dangerously-skip-permissions [--resume <session-id>]`. First turn in a topic returns a session ID (from the `result` event in stream-json output) stored in the `sessions` table. Subsequent turns pass `--resume`. If the session has expired the agent retries without `--resume`.
- **Codex:** `codex exec --json --dangerously-bypass-approvals-and-sandbox -s danger-full-access --ephemeral -o <tempfile> [-m <model>] <prompt>`. The `CODEX_HOME` directory mounted in the agent container persists config and auth across restarts. The `sessions` table stores `llm_session_id` (null for Codex — Codex does not expose a resumable session ID via CLI flags).

### 6. Run Mosquitto as a separate container

The MQTT broker runs as a dedicated Mosquitto container in the compose stack.
It is not embedded in the master process. This preserves the existing
separation-of-concerns principle (master = control plane, broker = message
bus) and makes broker restarts and configuration independent of master.

## Consequences

**Positive:**
- Eliminates Slack/Discord platform maintenance surface entirely
- Real-time thinking feedback is first-class (not a workaround)
- Workspace/topic model maps naturally to git worktrees and feature branches
- Persistent sessions enable multi-turn LLM context per topic
- SQLite gives structured queryable storage with no new service dependency
- MQTT decouples master and agent dispatch timing; agent can process
  asynchronously and stream progress

**Tradeoffs:**
- Vue 3 + Vite adds a frontend build step to the master image build
- Mosquitto adds a new container to operate and monitor
- SQLite is not suitable for concurrent writers at scale; migration to
  PostgreSQL is deferred but will require a schema migration later
- Dropping Slack/Discord means no mobile push notifications or existing
  workspace integrations out of the box
- MQTT QoS and retained-message semantics must be designed carefully to avoid
  duplicate or lost messages on reconnect

## Alternatives Rejected

1. **Keep Slack/Discord as primary, add web UI as secondary** — doubles the
   frontend surface area; v3 requirements call for replacing, not extending.

2. **WebSocket for master↔agent transport instead of MQTT** — would require
   master to act as WebSocket server for agents, coupling agent startup to
   master availability. MQTT's broker model gives agents the ability to publish
   without a live master connection.

3. **SSE (server-sent events) for agent→master** — unidirectional only;
   cannot carry master→agent prompts without a separate HTTP call per turn.

4. **Embed Mosquitto inside master container** — couples broker lifecycle to
   master process; a master redeploy would interrupt in-flight MQTT sessions.

5. **PostgreSQL from the start** — adds operational complexity (separate
   service, connection pooling) before scale requirements are proven.

6. **React / Next.js frontend** — heavier build pipeline (~130 KB bundle vs
   ~50 KB for Vue 3), SSR not needed for a private tool, slower time to MVP.

7. **Plain HTML/JS frontend** — fastest to start but becomes unmaintainable
   at moderate UI complexity (workspace sidebar, topic threads, spinner state,
   folded transcripts, @mention autocomplete).
