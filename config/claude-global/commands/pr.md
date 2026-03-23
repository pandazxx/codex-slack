---
description: Open a pull request against master with an auto-generated title and checklist body
argumentHint: "[title or description hints]"
---

Create a pull request from the current branch targeting `master` (or `main` if `master` does not exist).

1. Run `git status` — if there are uncommitted changes, commit them first (follow the commit procedure).
2. Run `git log master..HEAD --oneline` to see all commits in this branch.
3. Run `git diff master...HEAD --stat` for a high-level summary of what changed.
4. Push the branch if not already pushed: `git push -u origin HEAD`.
5. Draft a PR title — under 70 characters, imperative mood, no trailing period.
6. Draft a PR body using this template:
   ```
   ## Summary
   <2-4 bullet points: what changed and why>

   ## Test plan
   <markdown checklist of how to verify the change works>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```
7. Run: `gh pr create --base master --title "<title>" --body "<body>"`
8. Report the PR URL.

If the user provided a specific title, description hints, or base branch in the arguments, use that context when drafting.
