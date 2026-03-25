---
name: doc-writer
description: Use when syncing allowed non-code documentation such as README or contributor guidance with the current repository behavior, while respecting task-specific documentation constraints.
---

# Doc Writer

Use this skill to update repository documentation that is in scope for the current task.

## Responsibilities

- Read the current code and relevant repo instructions before editing prose.
- Prefer updating existing files over creating new ones.
- Keep structure, claims, and examples aligned with the actual repository state.

## Current repo constraint

- Do not modify anything under `docs/` unless the user explicitly allows it.

## Output

- Report each modified documentation file and the reason it changed.
