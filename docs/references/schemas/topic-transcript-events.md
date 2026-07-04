# Topic Transcript Event Schema

Reference for the structure of topic conversation logs: the message records stored per topic and the Claude Code stream events embedded in each agent message's `transcript`. Derived from analysis of four real topic logs (116 records, ~2,300 events) on 2026-07-04. This is the input contract for the topic graph view parser (see `docs/design/topic-graph-view.md`).

All examples below are synthetic — no real conversation content.

## Top-level record

One JSON object per message, ordered chronologically.

| Field | Type | Notes |
|---|---|---|
| `type` | `"user"` \| `"agent"` | Sender. Consecutive `user` records occur (e.g. an interrupt followed by "continue"). |
| `timestamp` | ISO 8601 string | |
| `agent_name` | string | Present on `agent` records (e.g. `"claude"`). |
| `text` | string | Final rendered message text. |
| `transcript` | object \| array \| null | `user` → dispatch metadata object. `agent` → array of stream events, **or `null` when the message was interrupted** (text is `"(message interrupted)"`). |

## User record `transcript` (dispatch metadata)

| Field | Type | Notes |
|---|---|---|
| `message_id` | UUID string | |
| `agent_name` | string | Target agent. |
| `adapter` | string | Observed: `"claude-code"`. |
| `subagent` | string \| null | Observed null. |
| `worktree` | string | Absolute worktree path. |
| `branch` | string | e.g. `topic/<slug>-<sha7>`. |
| `repo_ref` | string | Base ref, e.g. `master`. |
| `base_sha` | 40-char SHA | |
| `session_id` | UUID string | Stable across a topic's session. |
| `is_new_session` | bool | `false` on continuation turns (the common case after the first message). |
| `session_scope` | string | Observed: `"topic"`. |
| `model` | string \| null | Per-topic model override (e.g. `"claude-opus-4-7"`); usually null. |
| `system_prompt` | string \| null | Present on some topics. |
| `text` | string | Duplicate of top-level `text`. |
| `attachments` | array | Usually empty. |

## Agent record `transcript` events

Each event has `type`, plus `subtype` for `system`/`result`. Common envelope fields on most events: `uuid`, `session_id`, and `parent_tool_use_id`.

**`parent_tool_use_id` is the subagent linkage:** `null` for main-thread events; otherwise the `id` of the `Agent` `tool_use` block that spawned the subagent the event belongs to. Subagent-scoped `user` events additionally carry `subagent_type` and `task_description`. Nested subagents (Task-inside-Task) were not observed but are structurally possible — parsers must recurse on the same rule.

| Event | Cardinality | Purpose / key fields |
|---|---|---|
| `system/init` | ~1 per message | Session start: `cwd`, `session_id`, `tools[]`, `model`. Absent when the message continued after an interrupt-free boundary is not observed; may appear twice in one message (session restart). |
| `rate_limit_event` | 1 per message | Rate-limit telemetry. |
| `assistant` | many | One API turn chunk. `message.model`, `message.usage`, `message.content[]` blocks of type `thinking`, `text`, `tool_use` (`{id, name, input}`). |
| `user` | many | Tool results: `message.content[]` blocks of type `tool_result` (`{tool_use_id, content, is_error?}`). `is_error: true` marks failed tool calls (~3% of results observed). Rarely plain text content (subagent prompts echoed). |
| `system/task_started` | 0–n | Background task launched. `task_id`, `tool_use_id`, `description`, `task_type`: `"local_agent"` (subagent — also has `subagent_type`, `prompt`) or `"local_bash"` (backgrounded shell — no prompt). |
| `system/task_progress` | 0–n | Heartbeat for a running task: `task_id`, progress info. |
| `system/task_notification` | 0–n | Task finished: `task_id`, `tool_use_id`, `status` (`completed`/…), `summary`, `usage` (tokens, tool_uses, duration_ms). |
| `system/task_updated` | 0–n | Patch to task state: `task_id`, `patch` (e.g. `{is_backgrounded: true}`, `{status: "failed", end_time}`). |
| `system/status` | 0–n | Transient status, observed for context compaction: `{status: "compacting"}` then `{status: null, compact_result: "success"}`. |
| `system/compact_boundary` | 0–1 | Context compaction marker: `compact_metadata` (`trigger`, `pre_tokens`, `post_tokens`, `duration_ms`). |
| `result/success` | ~1 per message | Message rollup: `duration_ms`, `num_turns`, `total_cost_usd`, `usage`, `modelUsage` (per-model token breakdown), `is_error`, `permission_denials`. Absent on interrupted messages. |

### Pairing and ordering invariants (observed)

- Every `tool_use.id` in a completed message has exactly one matching `tool_result.tool_use_id` (0 orphans in 623 pairs). Interrupted messages have `transcript: null` entirely, so partial pairing was not observed — parsers must still tolerate orphans defensively.
- `tool_use` names observed: `Read`, `Glob`, `Grep`, `Bash`, `Write`, `Edit`, `Agent`, `Skill`, plus MCP tools (`mcp__<server>__<tool>` naming, e.g. `mcp__notes__list_workspace_notes`).
- Models observed: `claude-sonnet-4-6` (main thread), `claude-haiku-4-5-20251001` (subagents), `claude-opus-4-7` (per-topic override).
- Transcript sizes: 5–134 events per agent message in typical topics; a heavy ops topic reached 678 assistant events across 28 messages. Subagent-scoped events can dominate (645 of ~1,600 events in one log).
- Every `local_agent`/`local_bash` task lifecycle references its spawning `tool_use_id`, so task events attach to the same graph node as the tool call.

### Synthetic minimal example

```jsonl
{"type": "user", "timestamp": "2026-01-01T00:00:00Z", "text": "do the thing", "transcript": {"message_id": "00000000-0000-0000-0000-000000000001", "agent_name": "claude", "adapter": "claude-code", "subagent": null, "worktree": "/workspace/worktrees/x", "branch": "topic/x", "repo_ref": "master", "base_sha": "0000000000000000000000000000000000000000", "session_id": "00000000-0000-0000-0000-0000000000aa", "is_new_session": true, "session_scope": "topic", "model": null, "system_prompt": null, "text": "do the thing", "attachments": []}}
{"type": "agent", "timestamp": "2026-01-01T00:01:00Z", "agent_name": "claude", "text": "done", "transcript": [{"type": "system", "subtype": "init", "cwd": "/workspace/worktrees/x", "session_id": "00000000-0000-0000-0000-0000000000aa", "tools": ["Bash"], "model": "claude-sonnet-4-6"}, {"type": "assistant", "parent_tool_use_id": null, "message": {"model": "claude-sonnet-4-6", "role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {"command": "true"}}], "usage": {"input_tokens": 1, "output_tokens": 1}}}, {"type": "user", "parent_tool_use_id": null, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": ""}]}}, {"type": "result", "subtype": "success", "is_error": false, "duration_ms": 1000, "num_turns": 1, "total_cost_usd": 0.01, "usage": {"input_tokens": 1, "output_tokens": 1}}]}
```

## Keeping this reference current

This document reflects observed data as of 2026-07-04. When new event types or fields appear (new adapters, nested subagents, error results), update the tables here and add a matching synthetic fixture under `tests/fixtures/topic-graph/`.
