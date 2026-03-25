---
name: commit
description: Use when a task is complete and the branch must be finalized with a clean commit and push using a conventional commit message.
---

# Commit

Use this skill to finalize completed work.

## Steps

1. Inspect `git status`.
2. Review `git diff` and `git diff --staged`.
3. Stage only the intended files.
4. Write a Conventional Commit message.
5. Commit.
6. Push the current branch.
7. Verify the worktree is clean afterward.

## Required report

- Full commit SHA.
- Branch name.
- Push result.
- Any remaining follow-up risk that was intentionally left out of the commit.

## Constraints

- Do not end the task before pushing unless the user explicitly says not to push.
