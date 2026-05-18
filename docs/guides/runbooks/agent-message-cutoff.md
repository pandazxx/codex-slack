# Agent Message Cut-Off — Investigation Runbook

## Symptom

A message in the UI ends with the orange **"interrupted"** badge instead of a normal agent response. Master log shows:

```
master.stale_stream_interrupted message_id=<R> topic_id=<T> container_running=True chunk_age_s=<N>
```

…and the message row in `messages` has `text='(message interrupted)'` with `interrupt_reason` set.

## Impact

User's prompt produced no usable answer. Any work the codex/claude subprocess started (file edits, repo state, partial chunks) is preserved in the chunks table / worktree but the *response* is gone. The agent container itself usually keeps running.

## Decision tree — start here

Open the affected topic in the UI. Note the **`interrupt_reason`** displayed on the badge:

| Badge text | Reason key | Most likely cause | Go to |
|---|---|---|---|
| "interrupted: agent restarted" | `agent-shutdown` | Clean SIGTERM (someone clicked Restart Agent, or container was `docker stop`'d) | §1 |
| "interrupted: container gone" | `container-gone` | Container removed mid-stream — usually master's `stop_agent` (UI Restart Agent button) | §1 |
| "interrupted: agent killed" | `agent-killed` | SIGKILL'd container, Docker's restart policy bounced it, new agent replayed (v4.15-rc6+) | §2 |
| "interrupted: no response" | `ping-timeout` | Agent failed the ping but container still up — subprocess crashed in Python OR the agent is on a build prior to rc6 | §3 |
| "interrupted" (no specific text) | NULL | Old DB row from before PR #222 — no diagnostic info available | §4 |

## §1 — agent-shutdown / container-gone

Almost always intentional (someone restarted the agent or the container was rescheduled). Cross-check:

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker logs codex-slack-master 2>&1 \
  | grep -E "agent_runner\.(stopped|spawned)|workspace\.restarted|master\.idle_stop" \
  | tail -20
```

If you find `agent_runner.stopped`/`workspace.restarted` near the cut-off timestamp, it was a deliberate restart. Document the action that triggered it (UI click, deploy, etc.) and close. No code fix needed.

## §2 — agent-killed *(this is the case PR #222 / v4.15-rc6 added)*

The container was killed by something outside Python (SIGKILL bypasses our handlers). Docker's restart policy brought it back, the new agent replayed `_active_procs` from disk, and published the `agent-killed` interrupt event.

### Step 1: confirm the kill

`docker inspect` lies — it reports `exitcode=0` for restarted containers. Use the dockerd journal for ground truth:

```bash
ssh ubuntu@<prod-host> \
  'sudo journalctl --since "<HH:MM>:00" --until "<HH:MM>:59" -u docker' \
  | grep -E "restarting container.*exitCode"
```

Expected match:
```
restarting container ... exitCode=137 exitedAt="..." manualRestart=false restartPolicy="{unless-stopped 0}"
```

- `exitCode=137` ⇒ SIGKILL
- `exitCode=143` ⇒ SIGTERM that completed
- `manualRestart=false` ⇒ Docker's policy, not a human `docker restart`

### Step 2: rule out kernel OOM

```bash
ssh ubuntu@<prod-host> 'sudo journalctl -k --since "<HH:MM>:00" --until "<HH:MM>:59"' \
  | grep -iE "oom|out of memory|killed process"

DOCKER_HOST=ssh://ubuntu@<prod-host> docker inspect <container> --format '{{.State.OOMKilled}}'
```

If `OOMKilled=true` or the kernel log shows an OOM event: the container exceeded available memory. Look at recent memory usage in the agent (large transcript? Many subprocess instances?) — fix is to set explicit `Memory` limits in `agent_runner.spawn_agent` or scale the host.

### Step 3: rule out master initiating it

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker logs codex-slack-master --since 1h 2>&1 \
  | grep -E "agent_runner\.(stopped|spawned)|master\.idle_stop|pause_agent" \
  | grep <container>
```

Any hit here means master code stopped the agent. The most common case is `master.idle_stop` (idle timeout reached). Cross-reference with `MASTER_AGENT_IDLE_TIMEOUT_SECONDS`.

### Step 4: rule out CD daemon

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker logs codex-slack-cd-daemon --since 1h 2>&1 \
  | grep -E "cd\.deploy_start|cd\.force_recreate|cd\.new_image" | tail -10
```

If a deploy fired right before the kill: that's the source. CD daemon only restarts the master container — but a master restart can ripple into agent state.

### Step 5: rule out host-wide event

If multiple containers were killed in the same minute, it's a host event (kernel panic, dockerd restart). If only this container, it's container-specific.

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker ps -a \
  --format '{{.Names}} RestartCount={{.RestartCount}} created={{.CreatedAt}}'
```

Compare `RestartCount` and `created` against the incident time.

### Step 6: examine the codex transcript

The chunks before the cut-off are preserved in `messages.transcript` for the interrupted message. They tell you what the model was *doing* at the moment of death:

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker exec codex-slack-master python -c "
import sqlite3, json
conn = sqlite3.connect('/opt/codex-slack/data/master/master_data.db')
row = conn.execute(\"SELECT transcript FROM messages WHERE id=?\", ('<message-id>',)).fetchone()
events = json.loads(row[0])
for e in events[-10:]:
    item = e.get('item', {}) if isinstance(e.get('item'), dict) else {}
    print(e.get('type'), item.get('command', '')[:120])
"
```

The last `item.started` (without matching `item.completed`) is the command the model was running when it died. If it correlates with shell commands that allocate memory, write to disk, or fork heavily, that's a strong signal.

### Step 7: if all the above are clean

You have an unexplained SIGKILL. Possibilities to investigate next:

- **Tini SIGTERM→SIGKILL escalation.** Default 10s grace. Look upstream for whoever sent the original SIGTERM — could be a host-level systemd unit, container manager extension, or security tool.
- **Disk pressure** (overlay2 ENOSPC, write rejected). `ssh ubuntu@<host> df -h /` near the incident time; pull `journalctl` for disk-related kernel messages.
- **Codex CLI doing something fatal.** The CLI runs with `--dangerously-bypass-approvals-and-sandbox` and can run any shell command the model picks. In principle the model could issue `kill -9 1` against tini. Inspect the transcript for any such command.

If still unidentified, file the incident with the full timeline + ruled-out items. The new logs at least give you a marker — `agent.startup_inherited_active count=N message_ids=[...]` — so you can correlate future incidents.

## §3 — ping-timeout

Agent container is still up, but answered `alive=False` to master's ping. Two sub-cases:

### §3a — Python exception path (preferred outcome with v4.15-rc5+)

Look for either of these in the agent container log around the cut-off time:

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker logs <agent-container> --since 1h 2>&1 \
  | grep -E "agent\.(llm|codex)_subprocess_(aborted|nonzero_exit)"
```

If matched, the line carries everything you need:
```
agent.codex_subprocess_aborted topic_id=… message_id=… pid=…
   returncode=… chunks=… exc_type=BrokenPipeError exc=… stderr_tail=…
```

- `exc_type` + `exc` → which exception type aborted the stream (look in `_stream_codex_once`/`_stream_claude_once` for the matching code path)
- `stderr_tail` → last bytes of the codex/claude CLI's stderr, often the actual error text
- `returncode` → if non-None, the subprocess had exited cleanly with a failure code

### §3b — agent on a pre-rc5 build, no detailed logs

If you see `agent.pong message_id=… alive=False` with no preceding `agent.llm_done` / `agent.codex_done` / `agent.*_subprocess_aborted`:

The agent is missing the rc5/rc6 logging. Confirm:

```bash
DOCKER_HOST=ssh://ubuntu@<prod-host> docker exec <agent-container> sh -c 'env | grep APP_VERSION'
```

If `APP_VERSION` is earlier than `v4.15-rc5`: trigger a respawn so the agent picks up the latest image. After that, the next incident will give you the detailed logs.

```bash
# Restart the workspace's agent (master will recreate the container)
curl -sS -X POST 'https://codex-slack-prod.pandazxx.com/api/workspaces/<workspace-id>/restart-agent'
```

## §4 — bare "interrupted" with NULL reason

The row predates PR #222 / v4.14. No diagnostic information was captured. There's nothing to investigate; close as historical.

## Quick reference — important paths

| What | Where |
|---|---|
| Master DB | `/opt/codex-slack/data/master/master_data.db` inside `codex-slack-master` |
| Agent persisted `_active_procs` snapshot (v4.15-rc6+) | `/tmp/master-agent/active_procs.json` inside the agent container |
| Master logs | `docker logs codex-slack-master` |
| Agent logs | `docker logs codex-agent-<workspace-id>` |
| CD daemon logs | `docker logs codex-slack-cd-daemon` |
| Host journal | `journalctl -u docker` and `journalctl -k` |

## Reference case

The runbook was written after the **2026-05-17 cbc02192 incident** in topic `7c4f67e9` (workspace `e6d8e527`). See `docs/knowledge-base/lessons-learned.md` for the post-mortem. Symptoms were exactly §2: `exitCode=137`, `manualRestart=false`, no master logs of a stop/spawn, no kernel OOM. Codex was running `git worktree add origin/pr/219` at the moment of death. Source of the SIGKILL was not positively identified. PR #222 (v4.15-rc5 + rc6) was the response: rc5 adds subprocess-abort logging, rc6 adds the persistent `_active_procs` snapshot + startup replay so that future occurrences surface as `agent-killed` instead of silent `ping-timeout`.
