# Project Instructions

## SRE Workflow

Two agents share infrastructure responsibilities. Never run `docker`, `docker compose`, or deploy commands directly — delegate to the right agent.

| Agent | Invoke when… |
|---|---|
| `senior-sre` | Onboarding, re-onboarding, infra design review, first-time env provisioning on a new host |
| `sre` | Routine ops on an already-onboarded project: spin up env, run tests, tail logs, open shell, deploy to staging, tear down, post-merge cleanup |

### Required Environment Variables

Full details in `docs/sre.md`. Summary:

| Variable | Required for | Example |
|---|---|---|
| `DEV_DOCKER_HOST` | Dev env operations | `ssh://ubuntu@dev.tail-scale.ts.net` |
| `STAGING_DOCKER_HOST` | Staging deploys, UAT | `ssh://ubuntu@staging.tail-scale.ts.net` |
| `REGISTRY` | Building/pushing images | `ghcr.io/myorg` |
| `REGISTRY_TOKEN` | Pushing images | (from secret manager) |

No fallback to local Docker — `DEV_DOCKER_HOST` must always be set explicitly.

### How to Invoke

**Routine ops (delegate to `sre`):**

- "Spin up a dev env for branch `feat-auth`"
- "Run the tests" / "Run tests matching `test_image_contract`"
- "Tail logs for `feat-auth`"
- "Open a shell in the `master` service on `feat-auth`"
- "Deploy `v1.2.3` to staging for `feat-auth`"
- "Tear down dev env for `feat-billing`"
- "Post-merge cleanup for `feat-auth`"

The `sre` operator reads per-operation runbooks from `.sre/operations/` and follows them exactly.

**Design and onboarding (delegate to `senior-sre`):**

- "Onboard SRE workflow to this project"
- "Review our Dockerfile structure"
- "Set up CI/CD for this project"
- "First-time staging env on a new host"

### Dev Environment

Dev envs run on `DEV_DOCKER_HOST`. The `dev` stage of `Dockerfile` is built and deployed there — no source bind-mounts. The dev cycle is build → push → restart via the `env-up` runbook. Access via `docker compose exec` on the remote host (see `.sre/operations/shell.md`).

### Test Execution

Ask `sre` to run tests: "Run the tests" or "Run tests matching `<pattern>`". The operator follows `.sre/operations/test.md`.

### Staging & UAT

- **Canonical staging** — tracks `main` on `STAGING_DOCKER_HOST`, refreshed by the `post-merge-cleanup` runbook after every merge.
- **Feature-branch staging** — parallel env for high-risk features; ask `sre` to spin it up.

Both are image-based (deployed by digest). See `.sre/operations/staging-up.md`.

## Git Workflow

- Never commit directly to `master` or `main`. Create a feature branch first.
- Name branches after the work: `feat/`, `fix/`, `refactor/`, `docs/`, etc.
- Use the `/commit` command to stage, write, and push. Use the `/pr` command to open a PR against `master`.
- Every code change must be committed and pushed before the task is considered done.

## Knowledge Persistence

Sessions end and context resets. The repository is the only durable record — write decisions, discoveries, and fixes to the repo as you go.

- Before starting significant work: read `docs/decisions/` and `docs/knowledge-base/` for prior context.
- After every non-trivial fix or discovery: update `docs/knowledge-base/lessons-learned.md` and commit it with the fix.
- For every significant architectural or design choice: produce an ADR in `docs/decisions/` using the `architect` subagent.
- Never re-litigate settled decisions. If context is unclear when resuming, read the docs and `git log` first.

## Project Layout (target structure — create incrementally)

```
.
├── src/                  # Application source code
├── tests/                # Test code (mirrors src/ structure)
├── scripts/              # Build, deploy, migration, and utility scripts
├── config/               # Environment and service configuration
├── docs/                 # All documentation (see Document Layout below)
├── .claude/              # Project-scope agent instructions and settings
└── .github/              # CI/CD workflows, issue templates, PR templates
```

## Document Layout

All documentation lives under `docs/`. Each subdirectory has a single, clear purpose.

```
docs/
├── decisions/             # Architecture Decision Records (ADRs)
│   └── NNNN-title.md      #   MADR v4 format; numbered sequentially
│
├── design/                # Design documents for features and subsystems
│   └── feature-name.md    #   Problem, goals, non-goals, solution, alternatives
│
├── knowledge-base/        # Accumulated operational knowledge
│   ├── lessons-learned.md #   Post-mortems and issue fixes (append-only log)
│   └── faq.md             #   Frequently asked questions and answers
│
├── releases/              # Release notes, one file per release
│   └── vX.Y.md            #   What changed, why, migration steps
│
├── guides/                # How-to guides and runbooks
│   ├── runbooks/          #   Step-by-step operational procedures (incident, deploy, rollback)
│   └── onboarding.md      #   Getting-started guide for new contributors
│
├── test-plans/            # Test case specifications and acceptance criteria
│   └── feature-name.md    #   Scope, test cases, pass/fail criteria, edge cases
│
├── references/            # Stable technical references
│   ├── api.md             #   API endpoints, request/response schemas
│   ├── config.md          #   All configuration keys, types, defaults, descriptions
│   └── schemas/           #   Data schemas, ERDs, protocol specs
│
└── manuals/               # End-user and operator manuals
    ├── user-manual.md     #   Feature walkthroughs for end users
    └── ops-manual.md      #   Deployment, monitoring, backup, and recovery
```

*Conventions per doc type:*

- *ADRs* — MADR v4. Status flows `proposed` → `accepted` → `deprecated`/`superseded`. Never delete; supersede.
- *Design docs* — Sections: Context, Goals, Non-goals, Design, Alternatives considered, Open questions. Write before or alongside implementation.
- *Lessons learned* — Append-only. Each entry: date, summary, root cause, fix applied, prevention.
- *Runbooks* — Actionable, step-by-step. Written for someone responding under pressure. Include: trigger condition, impact, steps, rollback, escalation.
- *Test plans* — Link to the feature design doc. Cover happy path, edge cases, failure modes, and non-functional requirements.
- *References* — Factual and stable. Prefer tables. Keep in sync with implementation — stale references are worse than none.


## Common Workflows
 
The agents that appear below — `explore`, `architect`, `engineer`, `tester`, `reviewer`, `doc-writer`, `sre`, `senior-sre` — are each defined in their own subagent files. Workflows below describe how they cooperate; each agent's own definition governs *how* it does its work.
 
Slash commands referenced: `/commit` (incremental commit on the current branch), `/pr` (open a pull request against the default branch). See `.claude/commands/` for the full list.
 
### Feature development
 
**Trigger:** user proposes a new feature or asks to implement a feature-shaped issue.
 
1. *Branch hygiene.* If the workspace is on `main`/`master`, fork a feature branch `feat/<short-desc>`. If the workspace is dirty or the branch has diverged from `main`, alert the user before proceeding.
2. *Scoping (optional).* If the scope is unclear, spawn `explore` to locate relevant files, trace call paths, or map interfaces. This runs before design.
3. *Design.* Spawn `architect`. Hold the discussion with the user until requirements are clear, tradeoffs are resolved, and an ADR and/or design doc is committed to `docs/decisions/` or `docs/design/`. **Do not write implementation code before the design is signed off.**
4. *Test plan and scaffolding (parallel with implementation start).* `tester` authors the test plan in `docs/test-plans/` and sets up scaffolding (fixtures, harnesses, test data) based on the design. Each UAT case is marked `automated` or `needs-human` at authoring time. Test *bodies* against application code wait until step 6 — public interfaces have to stabilize first.
5. *Implementation.* `engineer` implements the feature on the branch, committing incrementally with `/commit`. Public interfaces (function signatures, API shapes, schema) should stabilize early so tester can fill in test bodies in parallel.
6. *Test bodies fill in.* Once public interfaces have stabilized, `tester` writes the actual test bodies against the implementation. This may overlap with the tail end of step 5.
7. *Fast test loop.* `tester` runs unit and in-process tests (no dev env needed). Failures go to `engineer`; loop until green or the same test has failed across more than 5 fix attempts — escalate to user at that point.
8. *Dev env spin-up.* `tester` asks `sre` to spin up a dev env for this branch (idempotent — returns the existing env if already up). The env builds from source at `DEV_DOCKER_HOST`; source changes require a rebuild (the operator's `env-up` runbook handles this). `engineer` and `tester` both use the env for troubleshooting.
9. *Stack test loop.* `tester` runs end-to-end and integration tests against the dev env. `engineer` troubleshoots in-env. Loop until green or the same test has failed across more than 3 fix attempts — escalate to user.
10. *Review.* Spawn `reviewer`. `engineer` fixes review issues. After fixes:
    - Style/structure/naming changes only → re-run step 7.
    - Anything that affects runtime behavior → re-run steps 7 and 9.
11. *Documentation.* Spawn `doc-writer` to update README, guides, references, and knowledge base.
12. *Open PR.* Use `/pr` to open a pull request against `main`. Link any GitHub issues. Generate the UAT checklist from the test plan and post it as the PR description (or top comment).
13. *CI gate.* Wait for CI to pass (`gh run view`). Two failure modes:
    - *Normal failure* (test, lint, build) → `engineer` fixes and pushes; CI re-runs.
    - *`SRE-BLOCK` failure* → not a bug fix. `engineer` reads the block's decision record, then either resolves the underlying issue or explicitly removes the block in the diff with rationale. The block's removal is itself reviewable in the PR.
14. *Feature-branch staging spin-up.* `tester` asks `sre` to spin up a feature-branch staging env from the latest CI-built image. Staging is image-based (digests), not code-mounted — it mirrors what would actually deploy. This catches "works in dev, fails when built" issues before UAT.
15. *UAT execution.* `tester` runs all UAT cases against the feature-branch staging env (not the dev env). Posts a signoff table as a PR comment:
    - `✅ pass` — executed and verified automatically.
    - `⏭ needs-human` — requires human interaction (real Slack message, visual check, external credential); described clearly so the user knows exactly what to do.
    - `❌ fail` — executed and failed; handed off to `engineer`.
16. *Feedback loop.* Based on UAT results:
    - Trivial fix → `engineer` fixes → re-run step 9 → re-run step 15.
    - Fix exposes new test cases → loop to step 4 to add cases, then forward.
    - Design-level change (scope, contract, new tradeoff) → loop to step 3; `architect` updates the design and ADR before any further implementation.
17. *Human UAT signoff.* User reviews `⏭ needs-human` cases in the PR and replies with ✅ or ❌ per row. UAT is complete when all cases are signed off.
18. *Merge.* User reviews and merges. No squashing without explicit instruction — preserve commit history.
19. *Post-merge cleanup.* `sre` refreshes canonical staging from `main` and tears down the feature-branch staging env. The branch's dev env teardown is up to the developer (ask `sre` to "tear down dev env for `feat/<short-desc>`" when done).
---
 
### Bug / issue fix
 
**Trigger:** user reports a bug, links a GitHub issue, or describes unexpected behavior.
 
The bug-fix workflow is shorter than feature development because (a) the design surface is usually narrow, (b) the most important deliverable is a regression test that locks the bug shut. Steps below assume a real bug; if investigation reveals the reported behavior is correct-by-design, exit early and respond to the user.
 
1. *Branch hygiene.* If on `main`/`master`, fork `fix/<issue-id-or-short-desc>`. Same dirty-workspace rule as feature workflow.
2. *Reproduce first.* Spawn `explore` if needed to locate the relevant code. Reproduce the bug, ideally in a failing test. Two paths:
    - *Reproducible in a unit test* → write the failing test now. This becomes the regression test. Skip step 4.
    - *Requires the running stack to reproduce* → ask `sre` to spin up a dev env (idempotent). Reproduce in-env, capture the exact steps and observed vs. expected behavior in `docs/bug-reports/<issue-id>.md`.
3. *Root cause.* `engineer` investigates and identifies the cause. **Do not patch symptoms.** If the root cause crosses module boundaries, has architectural implications, or the fix is non-obvious, escalate to step 3a; otherwise proceed to step 4.
3a. *Design when warranted.* For non-trivial fixes — refactors, contract changes, anything affecting more than one module — spawn `architect`, write a short ADR (one paragraph: cause, fix, alternatives considered) in `docs/decisions/`, and get user signoff before proceeding. Most bug fixes skip this step. The judgment call is: would another engineer understand *why* the fix looks the way it does just from reading the diff? If yes, skip 3a. If no, do it.
 
4. *Failing regression test.* `tester` writes a regression test that reproduces the bug and currently fails. If a failing test already exists from step 2, this is satisfied. The test must fail *for the right reason* — not just because of a typo or environment difference.
5. *Fix.* `engineer` writes the minimum change that makes the regression test pass. Commits incrementally with `/commit`.
6. *Fast test loop.* `tester` runs the full unit/in-process test suite to confirm the fix doesn't regress anything. Failures → `engineer` fixes → loop. Same 5-attempt escalation as feature workflow.
7. *Stack test loop (if step 2 used the dev env).* If the bug was stack-dependent, run the relevant subset of stack tests against the dev env. Skip if the bug was reproducible in unit tests alone.
8. *Review.* Spawn `reviewer`. The reviewer's focus on a bug fix is different from a feature: *Is this the minimum fix? Does the regression test actually lock the bug? Are there adjacent cases that could fail similarly that aren't covered?* Engineer addresses issues. Re-run step 6 after any fix.
9. *Documentation (lighter than feature workflow).* Update `docs/bug-reports/<issue-id>.md` with the resolution. Update `CHANGELOG.md` if the project maintains one. Skip `doc-writer` unless the bug fix changes user-facing behavior or documented APIs — in which case, spawn it.
10. *Open PR.* Use `/pr`. Link the GitHub issue. PR description should include: brief root cause, the fix in one or two sentences, link to the regression test, and a note if the fix changes any documented behavior.
11. *CI gate.* Same as feature workflow — normal failures get fixed and pushed; `SRE-BLOCK` failures get the architectural-decision treatment.
12. *UAT (proportional to risk).* The default for bug fixes is *no full UAT cycle*. The regression test is the primary verification. Two exceptions:
    - *Fix touches a critical path* (auth, payments, data integrity) → spin up feature-branch staging via `sre`, run focused UAT on the affected paths only, post signoff in the PR.
    - *Fix changes user-visible behavior* → user sanity-checks in feature-branch staging before merge.
    - Otherwise the regression test plus standard CI is sufficient.
13. *Merge.* User reviews and merges. Preserve commit history.
14. *Post-merge cleanup.* If a feature-branch staging env was spun up in step 12, `sre` tears it down. Canonical staging refreshes from `main`. Dev env teardown is up to the developer.
15. *Close the issue.* Reference the merged PR in the GitHub issue and close it. If the bug surfaced gaps in test coverage or monitoring, file follow-up issues rather than expanding the scope of this fix.
---
 
### When to choose which workflow
 
Bug-fix workflow when: there's a defined incorrect behavior to make correct, the change is localized, and a regression test is the right contract for "done."
 
Feature workflow when: new capability is being added, behavior is being intentionally changed, or the change spans multiple modules with new interfaces.
 
When in doubt — when something starts as a "small fix" and the engineer realizes mid-work that it's actually a feature — stop, re-scope with the user, and switch workflows. Don't try to hammer feature-shaped work through the bug-fix path; the truncated UAT and lighter design phase exist because bugs have narrow blast radius, and they stop being safe shortcuts the moment that assumption breaks.
 
