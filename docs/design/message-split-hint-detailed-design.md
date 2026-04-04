# Message Split Hint Detailed Design

**Status:** proposed  
**Issue:** [#39](https://github.com/pandazxx/codex-slack/issues/39)  
**ADR:** `docs/decisions/0003-message-split-hint-protocol.md`

## Goal

Implement a minimal split-hint protocol that lets agents choose semantic message boundaries while preserving the current size-based fallback path.

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

The master treats a line as a split hint only when the line content is exactly `🔹🔹🔹`.

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

## Design

### Shared Splitter

Introduce a shared response-splitting helper in master that works in two phases:

1. split the full response on exact `🔹🔹🔹` lines
2. for each resulting section, apply the existing size-based splitter if the section is still too long for the target frontend

Recommended shape:

```python
split_response_for_frontend(text: str, *, limit: int) -> list[str]
```

Recommended internal helpers:

- `split_on_hint_lines(text: str) -> list[str]`
- `split_by_size(text: str, *, limit: int) -> list[str]`

The splitter should preserve section order and preserve the marker line within the emitted chunk text.

### Why Preserve the Marker Line

The product decision is to keep the visible token if it leaks. The simplest implementation is therefore:

- use the marker line as the boundary between sections
- keep it attached to either the end of the previous section or the start of the next section

Recommended v1 behavior:

- attach the marker line to the following section when splitting on boundaries

That keeps the separator visually associated with the new section and avoids silently removing content authored by the agent.

### Frontend Integration

Both Slack and Discord should consume the same split-hint-aware chunk list before sending responses.

#### Discord

Update the routed-reply path in `src/master/discord_app.py`:

- use the shared split-hint-aware splitter
- remove `[1/2]` chunk labels from routed replies
- continue using file fallback when the full response exceeds the existing file threshold

`label_discord_chunks()` should not be part of the routed reply flow after this change.

#### Slack

Slack should use the same shared splitter with the Slack send limit. The visible output should be exactly the emitted chunks with no extra part labels.

### Fallback Behavior

Fallback behavior remains unchanged in spirit:

- if no exact hint lines are present, split purely by size
- if a hinted section exceeds the frontend-safe size, split that section further by size
- if the response exceeds a separate file-upload threshold for a frontend, keep the existing file fallback behavior

## Length Guidance

The `1700` target is not a programmatic enforcement rule.

Instead, it is a static instruction for the agent under:

- `config/codex-global/instructions.md`
- `config/claude-global/CLAUDE.md`

The implementation should not reject, warn, or reflow content purely because a section exceeds `1700` characters. The master only needs to honor hints where possible and then keep transport-safe behavior.

## Static Instruction Contract

Global instructions should tell the agent:

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
- marker line present but one section is too large: split that section by size
- repeated blank lines without marker: no special handling

## Tests

Required test coverage:

- exact marker line produces multiple sections
- non-exact variants such as ` 🔹🔹🔹 ` do not trigger hint splitting
- oversized hinted section is further split by size
- no hint falls back to current splitter behavior
- Discord routed replies do not add `[1/2]` labels after the change
- Slack and Discord both preserve visible `🔹🔹🔹` lines in delivered content

## UAT Expectations

Expected operator-visible behavior:

- a long agent response authored with `🔹🔹🔹` separators arrives as multiple messages at those boundaries
- the separator line remains visible if included by the agent
- when a section is still too long, master may split it further
- when no hints are present, behavior remains compatible with today’s heuristic splitting

## Implementation Notes

Recommended sequence:

1. add shared split-hint parsing and size-fallback helpers in master
2. wire Discord routed replies to that helper and remove chunk labeling
3. wire Slack routed replies to the same helper
4. update static global instructions for Codex and Claude
5. add unit coverage for the splitter and frontend integration paths
