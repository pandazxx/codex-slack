---
name: pr
description: Use when preparing or opening a pull request from the current branch, including summary, test evidence, and verification notes.
---

# PR

Use this skill after the branch has been committed and pushed.

## Steps

1. Confirm the worktree is clean.
2. Review commits on the branch against `master`.
3. Summarize what changed and why.
4. Include test evidence and any manual verification notes.
5. Open or draft the pull request against `master`.

## Constraints

- Do not open a PR from a dirty branch.
- Do not omit test evidence from the PR body.
