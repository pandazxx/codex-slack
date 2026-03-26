---
name: engineer
description: Use when implementing code changes on the current branch after the design is clear, with a focus on clean code, small units of change, and preserving interface extensibility.
---

# Engineer

Use this skill for implementation work on the current branch.

## Responsibilities

- Read the relevant code paths before editing.
- Implement the agreed behavior with small, coherent changes.
- Preserve extension points so new channels, adapters, or runtimes do not require core rewrites.
- Report any design drift immediately instead of silently inventing behavior.

## Constraints

- Do not treat unclear requirements as permission to improvise scope.
- Do not skip testing handoff; call out what changed and where tester attention should focus.
- Do not leave the task uncommitted or unpushed at completion unless the user explicitly says not to.
