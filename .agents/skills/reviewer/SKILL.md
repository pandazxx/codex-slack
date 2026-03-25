---
name: reviewer
description: Use when performing a read-only review of branch changes, focusing on correctness, regression risk, interface breakage, and missing tests.
---

# Reviewer

Use this skill for a code review pass after implementation and testing.

## Process

1. Review `git diff` for the branch.
2. Read changed files in full, not only the diff hunks.
3. Evaluate correctness, regression risk, extensibility, and test coverage.

## Report format

- Findings first, ordered by severity.
- Each finding should include file, line, issue, and concrete fix direction.
- End with a short verdict: approve, approve with minor fixes, or request changes.

## Constraints

- Read-only review only.
- Do not mix review findings with implementation.
