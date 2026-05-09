---
title: "ADR-0014: Codex agent adapter"
status: accepted
date: 2026-05-09
decision-makers: [engineer]
consulted: []
informed: [doc-writer, users]
---

## Context and Problem Statement

The platform supports Claude Code as its primary agent backend (ADR-0001, ADR-0005). Users running OpenAI Codex (`@openai/codex`) via their own API keys want equivalent support — the same MQTT streaming, topic session model, and web UI — without a separate dispatch path. See GitHub issue [#127](https://github.com/pandazxx/codex-slack/issues/127).

## Decision Drivers

- Reuse the existing MQTT chunk-streaming pipeline and frontend rendering with zero duplication.
- The `codex` binary (`@openai/codex` v0.128.0+) is a Rust-compiled native executable with a distinct CLI surface; it must be invoked correctly.
- Auth material (`~/.codex/auth.json`) must be configurable via the web settings UI without requiring SSH access to the agent container.

## Considered Options

1. Invoke `codex exec --json` with a temp-file output sink and stream stdout as MQTT chunks.
2. Use a REST API wrapper around the Codex binary.
3. Add a separate MQTT topic for Codex events.

## Decision Outcome

*Chosen option:* Option 1 — `codex exec --json` with temp-file sink — because it reuses the existing MQTT chunk pipeline unchanged and requires no new protocol surface.

### Consequences

- *Good:* frontend streaming, "details" transcript, and "raw" buttons all work without new code paths.
- *Good:* a single `CODEX_AUTH_JSON` system variable (written to `~/.codex/auth.json` at startup) covers authentication without exposing secrets in the settings UI plaintext.
- *Bad:* the `-o <tempfile>` flag is required for reliable final-output extraction; stdout alone can be empty when the model produces no `turn.completed.output_text`.

### Confirmation

Unit tests in `tests/agent/test_mqtt_loop.py` assert correct flag construction, event-type classification, and output extraction. The CI gate enforces green tests before merge.

## Pros and Cons of the Options

### Option 1: `codex exec --json` + temp-file sink

Stream `codex exec --json --dangerously-bypass-approvals-and-sandbox -s danger-full-access --ephemeral -o <tempfile> <prompt>` stdout as JSONL. Read `<tempfile>` for the canonical final output.

- Pro: zero new MQTT topics or frontend code paths
- Pro: `-o` file is the authoritative output even when `turn.completed.output_text` is absent
- Con: temp-file cleanup required; stderr must be captured separately

### Option 2: REST API wrapper

Wrap the binary behind an HTTP adapter and poll for results.

- Pro: cleaner separation of concerns
- Con: adds latency and complexity; streaming becomes a polling simulation

### Option 3: Separate MQTT topic for Codex events

Publish to a codex-specific topic namespace.

- Pro: allows codex-specific event schemas
- Con: duplicates all frontend rendering logic; breaks the unified chunk consumer

## Implementation Notes

**CLI flags (v0.128.0):**
```
codex exec --json \
  --dangerously-bypass-approvals-and-sandbox \
  -s danger-full-access \
  --ephemeral \
  -o <tempfile> \
  [-m <model>] \
  <prompt>
```

**JSONL event types emitted on stdout:**
- `thread.started`, `turn.started` — lifecycle, no output; classified `hidden`
- `turn.completed` — primary output in `output_text` or `last_message`; classified `codex_done`
- `turn.failed` — error in `error.message`; classified `folded`
- `turn.token_usage`, `turn.context_compacted` — telemetry; classified `hidden`
- `error` — fatal error; classified `folded`

**Auth configuration:**
`CODEX_AUTH_JSON` is a sensitive system variable (stored in the `config` table, injected as an env var into agent containers). The agent worker writes its value to `~/.codex/auth.json` with mode 600 during `stage_workspace_prepare()`.

**Persistent volumes:**
- `codex-claude-{workspace_id}` → `/home/appuser/.claude` (existing)
- `codex-codex-{workspace_id}` → `/home/appuser/.codex` (new; persists Codex config across restarts)
