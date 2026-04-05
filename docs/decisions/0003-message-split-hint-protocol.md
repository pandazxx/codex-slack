# 0003 Message Split Hint Protocol

- Status: proposed
- Date: 2026-04-04
- Issue: [#39](https://github.com/pandazxx/codex-slack/issues/39)

## Context

Long agent replies currently rely primarily on frontend-side size splitting.

That creates two problems:

1. frontend auto-splitting can produce awkward chunk boundaries
2. large replies are less readable when the split points are chosen purely by character count

Issue `#39` proposes a protocol where:

- the agent chooses the intended split boundaries
- the master performs best-effort splitting based on those boundaries
- frontend delivery remains compatible with existing size-based fallback logic

The product guidance for this issue is:

- agent controls the intended split points
- master should split only on an exact marker line
- if the marker leaks to the user, it is acceptable to leave it visible
- no numbering or extra metadata should be attached to the protocol
- fallback size-based splitting must remain available
- the primary goal is to avoid ugly frontend auto-splitting/truncation
- the secondary goal is to improve readability

## Decision

Adopt an agent-authored split-hint protocol using an exact standalone marker line:

```text
🔹🔹🔹
```

### Matching rule

The master should treat a line as a split hint only when the line content is exactly:

```text
🔹🔹🔹
```

No surrounding text should be accepted as a split marker.

### Ownership boundary

- the agent owns the semantic split points
- the master owns best-effort transport splitting
- the frontend should send the resulting chunks exactly as produced, without adding part labels

### Fallback behavior

If no valid split hints are present, or if hinted sections still exceed frontend-safe size, the existing size-based splitting path remains available.

### Instruction source

The split-hint behavior should be instructed through static global config:

- `config/codex-global`
- `config/claude-global`

It should not be injected dynamically on each request in v1.

### Scope

Apply the protocol to both Slack and Discord.

### Soft section target

The agent should be guided by static instructions to keep sections around:

- `1700` characters

This is an instruction target for the agent, not a hard programmatic enforcement rule in the master.

## Example

```text
First short section.

🔹🔹🔹

Second short section.
```

## Alternatives Considered

### 1. Pure size-based splitting only

Rejected.

It does not give the agent any control over semantic boundaries and can create poor message breaks.

### 2. Invisible or whitespace-only split hints

Rejected.

They are harder to match robustly and harder to debug.

### 3. Structured numbered markers

Rejected for v1.

The user explicitly prefers a minimal protocol with only the marker line and no numbering metadata.

### 4. Dynamic per-request prompt injection

Rejected for v1.

The user wants the behavior to live in static instructions under global config rather than being injected on each request.

## Consequences

Positive:

- gives the agent semantic control over intended split boundaries
- reduces ugly transport-driven chunking
- keeps the wire protocol simple and debuggable
- preserves compatibility with current size-based fallback behavior

Tradeoffs:

- leaked marker lines are possible and intentionally tolerated
- malformed or missing hints still fall back to heuristic size splitting
- agent instruction quality becomes important for good results

## Implementation Guidance

Engineer and tester should treat this ADR as the v1 boundary:

- split only on exact `🔹🔹🔹` lines
- do not require numbering metadata
- do not inject split instructions dynamically
- keep existing size-based splitting as the fallback
- apply the behavior to both Slack and Discord
