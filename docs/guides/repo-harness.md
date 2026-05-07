# Repository Harness & Branch Protection

This document describes the merge rules and repository guardrails enforced on the default branch.

## Overview

The repository enforces branch protection on the default branch (`main`) to maintain code quality and traceability:

- **No direct pushes** — all changes must go through a pull request.
- **Linear history** — merges use squash or rebase (no merge commits) for a clean log.
- **Required review** — at least 1 approving review before merge.
- **Required status checks** — CI must pass (tests, build, lint).
- **Stale dismissal** — new commits dismiss old approvals; fresh review needed.
- **Admin override only** — admins can push directly in emergencies (audit trail preserved).

## Setting Up Protection

Run this once per repository to enable protection:

```bash
.sre/setup-repo-protection.sh
```

Requires `gh` CLI authenticated (`gh auth login`).

The script is idempotent — run it again to update rules if they change.

## Merge Rules

### No Direct Pushes

All changes to `main` must come via a pull request. The rule is enforced by GitHub API.

**Attempt to push directly:**

```bash
git push origin main
# ERROR: failed to push some refs to 'github.com/...'
# hint: Updates were rejected because the tip of your current branch is behind its remote counterpart
```

**Instead:**

1. Create a feature branch: `git checkout -b feat/my-feature`
2. Make commits, push to origin.
3. Open a PR via GitHub UI or `/pr` command.
4. Get approval, merge via the UI.

### Linear History (Squash/Rebase Merge)

PRs are merged with squash (one commit on main) or rebase (replays commits on main in order). No merge commits allowed.

**Why:** Linear history is easier to bisect, understand blame, and revert if needed. Squash keeps the log clean for release notes.

**Default setting:** Squash and fast-forward merge. (Configurable in GitHub UI if the project needs rebases instead.)

### Required Review

At least 1 approving review is required before merging. The reviewer must be someone other than the PR author.

**To approve a PR:**

```bash
gh pr review <PR_NUMBER> --approve
```

**To dismiss stale reviews (automatic on new commits):**

If the author pushes new commits, previous approvals are dismissed — a fresh review is needed.

### Required Status Checks

All CI checks must pass:

- `ci` — build, test, lint in Docker (see `.github/workflows/ci.yml`)

**View check status:**

```bash
gh run view <RUN_ID>
```

**If a check fails:**

1. Read the failure logs.
2. Fix the issue locally (code, tests, Dockerfile, etc.).
3. Commit and push — CI re-runs automatically.
4. Once green, you can request re-review and merge.

### Dismissing Stale Reviews

When the author pushes new commits, any existing approvals are marked stale. A fresh review is required.

**Why:** Ensures reviewers explicitly sign off on changes, not just the original PR.

## SRE-BLOCK Enforcement

SRE may mark PRs with a "SRE-BLOCK" status check if infrastructure concerns are found (e.g., `latest` tags in prod images, hardcoded secrets, missing healthchecks). These are not normal CI failures.

**To resolve a SRE-BLOCK:**

1. Read the decision record cited in the block message (in `docs/sre-decisions/`).
2. Either:
   - Fix the underlying issue (preferred).
   - Explicitly remove the block in your diff with a comment explaining why it's safe to proceed (reviewable decision).

The block is intended to be deliberate friction, not a gating rule that can be silently bypassed.

## Overriding Protection (Admins Only)

Admins can force-push to `main` in emergencies (e.g., revert a bad merge that broke prod). This requires write access to the repository settings.

**To force-push (emergency only):**

```bash
git push --force-with-lease origin main
```

**This should be rare.** Every force push is logged in the GitHub audit log. After recovery, schedule a postmortem to prevent recurrence.

## CODEOWNERS

File ownership can be enforced via `.github/CODEOWNERS`. If the file exists, reviewers from the owning team must approve changes to their files.

**Current status:** Not set up (optional). Contact SRE if the team wants to enforce ownership.

## Pull Request Template

A PR template lives at `.github/pull_request_template.md` and is shown to authors when opening a PR. It includes:

- Checklist for SRE concerns (container build, no `latest` tags, migrations reversible).
- Link to this repo harness doc.
- Reminders about test coverage and commit message clarity.

## Troubleshooting

### "Branch is out of date"

Your branch is behind `main`. Update it:

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease origin feat/my-feature
```

### "Status checks failed"

CI workflow failed. Check the logs:

```bash
gh run view <RUN_ID> --log
```

Fix the issue (tests, Dockerfile, linting) and push — CI re-runs.

### "Waiting for status checks to pass"

A CI run is in progress. Wait for it to finish:

```bash
gh run watch <RUN_ID>
```

### "Resolve merge conflicts"

GitHub will show a "Resolve conflicts" button if `main` has changed since your branch was created. Use it in the UI or rebase locally:

```bash
git fetch origin
git rebase origin/main
# Fix conflicts in your editor
git add .
git rebase --continue
git push --force-with-lease origin feat/my-feature
```

## Updating Protection Rules

To change the rules (e.g., require 2 reviews instead of 1):

1. Edit `.sre/setup-repo-protection.sh` to reflect new rules.
2. Commit and push the change.
3. Re-run `.sre/setup-repo-protection.sh`.

Or manage rules directly in GitHub UI:

1. Go to Settings → Branches.
2. Click "Edit" on the protection rule.
3. Update and save.

The setup script should be considered the source of truth; keep it in sync with the actual rules.

## Related Documentation

- **SRE workflow:** `docs/guides/sre.md` — container operations, dev env, test execution.
- **Project instructions:** `.claude/CLAUDE.md` — agent workflows, git branching conventions.
- **CI pipeline:** `.github/workflows/ci.yml` — what checks are run.
