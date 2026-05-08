# Message Split Hint Detailed Design

**Status:** superseded — the split-hint protocol existed to chunk replies for Slack/Discord, both removed by [ADR-0006](../decisions/0006-drop-slack-discord-integration.md). The v3 web UI streams replies in full and does not require split hints. Retained for historical context.

**Issue:** [#39](https://github.com/pandazxx/codex-slack/issues/39)  
**ADR:** [`docs/decisions/0003-message-split-hint-protocol.md`](../decisions/0003-message-split-hint-protocol.md)

## Goal

Document the implemented split-hint protocol that lets agents choose semantic
message boundaries while preserving the current size-based fallback path.

The primary product goal is to avoid ugly frontend auto-splitting or truncation. The secondary goal is to improve readability of long answers.

## Scope

### In Scope

- parse an exact split marker line from agent responses
- apply the behavior to both Slack and Discord
- preserve the marker line in user-visible output when present
- fall back to current size-based splitting when hints are absent or insufficient
- document the static agent instruction contract in global Codex and Claude config

### Out of Scope

- dynamic per-request prompt injection
- numbering or part metadata
- frontend-specific continuation labels
- structured response envelopes or hidden control channels

## Protocol

The agent may separate intended sections with an exact standalone marker line:

```text
🔹🔹🔹
```

The master treats a line as a split hint only when the line content is exactly
`🔹🔹🔹`.

No looser variant is accepted in v1:

- no surrounding text
- no alternate counts such as `🔹🔹🔹🔹`
- no inline markers

If the marker reaches the frontend, it should remain visible as part of the content.

## Current Behavior Baseline

Today, long responses are split heuristically by frontend-specific size helpers:

- Discord uses `split_discord_message()` in `src/master/discord_app.py`
- command/status output uses `_chunk_text()` in `src/master/command_format.py`
- Discord still has `label_discord_chunks()`, which prepends `[1/2]` style labels

Issue `#39` adds semantic split hints ahead of those heuristics. It does not remove the size-based fallback path.

## Current Design

### Shared Parsing, Frontend-Owned Delivery

The shared split-hint parser lives in `src/master/response_split.py`, while
transport delivery decisions remain frontend-owned.

Current shared helper:

```python
split_on_hint_lines(text: str) -> list[str]
```

The parser:

- split only on exact `🔹🔹🔹` lines
- preserve section order
- preserve the marker line within the emitted chunk text

Each frontend then decides how to deliver those parsed sections, including:

- size-based fallback splitting when no hint lines are present
- frontend-specific hard limits
- frontend-specific file fallback behavior

### Why Preserve the Marker Line

The product decision is to keep the visible token if it leaks. The simplest implementation is therefore:

- use the marker line as the boundary between sections
- keep it attached to either the end of the previous section or the start of the next section

Current behavior:

- attach the marker line to the following section when splitting on boundaries

That keeps the separator visually associated with the new section and avoids silently removing content authored by the agent.

### Frontend Integration

Both Slack and Discord consume the same parsed hint sections before sending
responses, but each frontend remains responsible for its own transport
behavior.

#### Discord

The routed-reply path in `src/master/discord_app.py` uses:

- `build_discord_reply_plan(text)`
- the shared hint parser from `response_split.py`
- no `[1/2]` chunk labels in routed replies
- current size-based fallback splitting when no exact hint lines are present
- whole-response markdown file fallback when any hinted section exceeds the
  Discord fallback threshold

Current threshold:

- `2000` characters per hinted section

This keeps the implementation simple and avoids mixing semantic sections with additional partial splitting inside a section.

`label_discord_chunks()` is not part of the routed reply flow.

#### Slack

Slack uses the same shared hint parser, but the Slack frontend owns the final
delivery strategy.

The Slack routed reply path uses:

- `build_slack_reply_plan(text)`
- the shared hint parser from `response_split.py`
- current size-based delivery when no exact hint lines are present
- whole-response file fallback when any hinted section exceeds the Slack
  fallback threshold instead of partially splitting a hinted section

This boundary is intentionally frontend-owned so Slack and Discord can diverge later without changing the shared protocol.

### Fallback Behavior

Fallback behavior is frontend-owned:

- if no exact hint lines are present, use the frontend's current size-based splitting path
- if exact hint lines are present and every section is within the frontend threshold, send one message per section
- if exact hint lines are present and any section exceeds the frontend threshold, fall back to a single markdown file for the whole response

The current implementation does not size-split an individual hinted section
after parsing. Oversized hinted sections trigger whole-response file fallback
instead.

## Length Guidance

The `1700` target is not a programmatic enforcement rule.

Instead, it is a static instruction for the agent under:

- `config/codex-global/AGENTS.md`
- `config/claude-global/CLAUDE.md`

The implementation does not reject, warn, or reflow content purely because a
section exceeds `1700` characters. The master only needs to honor hints where
possible and then keep transport-safe behavior.

## Static Instruction Contract

Global instructions tell the agent:

- when producing a long reply, organize it into short sections
- place `🔹🔹🔹` on its own line between sections
- aim for about `1700` characters per section when practical
- expect master to split on that exact marker line only

This remains static repo-managed behavior, not dynamic prompt text injected by master.

## Error Handling

No new user-facing error is needed for malformed hints in v1.

Malformed or missing hints simply degrade to existing size-based splitting behavior.

Examples:

- no exact marker line found: split by size only
- marker line present but one section is too large: send the whole response as a markdown file
- repeated blank lines without marker: no special handling

## Tests

Test coverage:

- exact marker line produces multiple sections
- non-exact variants such as ` 🔹🔹🔹 ` do not trigger hint splitting
- oversized hinted section triggers whole-response file fallback
- no hint falls back to current splitter behavior
- Discord routed replies do not add `[1/2]` labels after the change
- Slack and Discord both preserve visible `🔹🔹🔹` lines in delivered content when sending split messages
- Slack and Discord can use different file fallback implementations while sharing the same parser

## UAT Expectations

Expected operator-visible behavior:

- a long agent response authored with `🔹🔹🔹` separators arrives as multiple messages at those boundaries
- the separator line remains visible if included by the agent
- when a hinted section is too long, the frontend falls back to sending the whole response as a markdown file
- when no hints are present, behavior remains compatible with today’s heuristic splitting

## Implemented Components

The current implementation is spread across:

1. `src/master/response_split.py`
   - `SPLIT_HINT_LINE`
   - `split_on_hint_lines()`
   - `split_by_size()`
   - `ReplyDeliveryPlan`
2. `src/master/discord_app.py`
   - `build_discord_reply_plan()`
3. `src/master/slack_app.py`
   - `build_slack_reply_plan()`
4. static global instructions:
   - `config/codex-global/AGENTS.md`
   - `config/claude-global/CLAUDE.md`
