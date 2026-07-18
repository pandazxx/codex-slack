# Topic Graph Fixtures

Synthetic JSONL fixtures for `frontend/src/lib/transcriptGraph.js` unit tests.
Each file is a valid topic conversation log per `docs/references/schemas/topic-transcript-events.md`.
All content is invented — no real prompts, paths, tokens, or message text from actual logs.

## Fixture inventory

| File | Scenario | Key structural features exercised |
|---|---|---|
| `simple-text.jsonl` | Text-only topic, two turns | User dispatch with `model: "claude-opus-4-7"` override; `is_new_session: true` on first turn, `false` on second; agent transcripts with only `thinking` + `text` blocks; `system/init` on first turn only; `rate_limit_event`; `result/success` with `modelUsage`. |
| `tools-and-subagent.jsonl` | Agent uses Glob + Agent tools | Main-thread `tool_use`/`tool_result` pair (Glob); `Agent` `tool_use` spawning an Explore subagent; `system/task_started` (`task_type=local_agent`, `subagent_type=Explore`); subagent-scoped `assistant`/`user` events with `parent_tool_use_id` set and `claude-haiku-4-5-20251001` model; `system/task_progress`; `system/task_notification` with `usage`; Agent `tool_result` rollup on main thread. |
| `background-and-compaction.jsonl` | Background Bash task + context compaction | MCP tool call (`mcp__notes__list_workspace_notes`); `system/task_started` with `task_type=local_bash`; `system/task_updated` with `patch: {is_backgrounded: true}` then `patch: {status: "failed", end_time: <ms>}`; `tool_result` with `is_error: true`; `system/status` `compacting` then `compact_result: "success"`; `system/compact_boundary` with full `compact_metadata`. |
| `interrupted-and-edge.jsonl` | Interrupted message + consecutive user records + session restart | Agent record with `transcript: null` and `text: "(message interrupted)"`; two consecutive `user` records (interrupt "stop" followed by "continue"); the subsequent agent message contains **two** `system/init` events (session restart — same `session_id`); `Write` tool call. |
| `nested-subagent.jsonl` | HYPOTHETICAL: two-level nested subagent | A coordinator `Agent` tool_use (level 0) whose own transcript contains a second `Agent` tool_use (level 1) with its own `parent_tool_use_id` chain; `task_started` / `task_notification` at both levels; the inner specialist subagent uses only `Glob` and returns results up through the chain. **Not observed in real data — structurally possible per the schema's recursive `parent_tool_use_id` rule. Marked hypothetical.** |
| `malformed.jsonl` | Defensive / error handling | `tool_result` referencing an unknown `tool_use_id` (orphan result — triggers `Diagnostic{code:"orphan_tool_result"}`); `tool_use` with no matching `tool_result` (orphan send — `toolu_orphan_001` never gets a result); `assistant` content block with unknown `type` (`unknown_content_block_type` — should not crash); top-level event with unknown `type` (`future_event` — triggers `Diagnostic{code:"unknown_event_type"}`). Parser must emit diagnostics and continue; all other nodes in the message must still be produced. |

## UUID / ID conventions

- `session_id`: `ssssssss-0000-0000-0000-000000000NNN` where NNN is the fixture number.
- `message_id`: `xxxxxxxx-NNNN-0000-0000-MMMMMMMMMMM1/2/3` (fixture / turn index).
- `tool_use id`: `toolu_<descriptive>_<seq>` (e.g. `toolu_agent_001`, `toolu_glob_001`).
- `uuid` (event envelope): `<role>-uuid-<seq>` (e.g. `asst-uuid-0001`, `init-uuid-0003`).
- All timestamps are monotonically increasing within each file.
- `base_sha`: 39 zeros + fixture number (e.g. `0000000000000000000000000000000000000003`).
