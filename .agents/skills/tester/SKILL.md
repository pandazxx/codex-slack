---
name: tester
description: Use when adding or updating tests, running the full test suite, checking interface stability, or preparing a user acceptance checklist.
---

# Tester

Use this skill for automated and manual validation work.

## Responsibilities

- Add or update tests under `tests/` for every user-visible or contract-changing behavior.
- Prioritize interface boundaries: Podman, Slack, Discord, agent adapters, and command surfaces.
- Run the full relevant test suite before finalizing.
- Produce a concise UAT checklist when manual verification is useful.

## Report format

- Total passed and failed.
- Exact failing tests and the handoff needed to fix them.
- Residual risks if coverage is incomplete.

## Constraints

- Do not modify implementation files when the task is purely a testing pass.
- Do not mark the task complete while tests are knowingly red unless the user accepts that state.
