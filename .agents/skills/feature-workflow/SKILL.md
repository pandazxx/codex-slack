---
name: feature-workflow
description: Use when a task is a significant feature or change that should follow the repository lifecycle: design, implementation, testing, review, documentation sync, commit, and push.
---

# Feature Workflow

Use this skill for non-trivial work that should mirror the repository's structured agent workflow.

## Sequence

1. Confirm the current branch is not `master` or `main`.
2. Clarify scope, constraints, and acceptance criteria before implementation.
3. Run design first.
   Use the `architect` skill for options, tradeoffs, and the recommended approach.
4. Implement the agreed change.
   Use the `engineer` skill for code changes.
5. Add or update tests.
   Use the `tester` skill for test coverage, full-suite execution, and UAT guidance where relevant.
6. Review the result.
   Use the `reviewer` skill for a read-only findings pass before finalizing.
7. Sync allowed documentation.
   Use the `doc-writer` skill only for files permitted by the current task constraints.
8. Finish with `commit` and `push`.
   Every non-trivial task in this repo must end with a commit and push unless the user explicitly waives that rule.

## Constraints

- Do not skip design for significant work just because implementation seems obvious.
- Do not leave the branch with uncommitted changes at task completion.
- Do not modify `docs/` when the active task forbids it.
