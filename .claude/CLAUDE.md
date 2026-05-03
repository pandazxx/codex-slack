# Project Instructions

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

## Common Workflow

### Feature development

1. *User opens a feature branch* — `git checkout -b feat/<name>`. This is the trigger to begin work.

1a. *Scoping (optional)* — if the scope is unclear, spawn the `explore` agent to locate relevant files, trace call paths, or map interfaces before design begins.

2. *Design phase* — spawn the `architect` agent. Hold the discussion with the user until the design is complete: requirements are clear, tradeoffs are resolved, and an ADR and/or design doc are committed to `docs/decisions/` or `docs/design/`. Do not write implementation code before design is signed off.

3. *Build and test authoring (parallel)* — once design is signed off, two tracks run concurrently:
   - `engineer` agent implements the feature on the branch, committing incrementally with the `/commit` command.
   - `tester` agent authors test cases and test code in `tests/` and a test plan in `docs/test-plans/`, based on the design doc.

4. *Test execution loop* — `tester` agent runs the full test suite. If any tests fail, `engineer` fixes them. Repeat until all tests are green.

5. *User manual testing* — `tester` agent commits all test work and instructs the user on what to verify manually. User performs manual testing and provides feedback.

6. *Feedback loop* — based on user feedback:
   - Trivial change (wording, minor behaviour) → back to step 3.
   - Non-trivial change (scope, design, new tradeoff) → back to step 2; `architect` re-enters to update the design and ADR before any further implementation.
   - Repeat steps 2–6 until the user is satisfied.

7. *Review* — spawn the `reviewer` agent (or request a human reviewer via PR). `engineer` fixes all review issues. `tester` runs the test suite again to confirm nothing regressed.

8. *Documentation* — spawn the `doc-writer` agent to update README, guides, references, and knowledge-base to reflect the completed feature.

9. *Release candidate* — use the `/tag` command to create an RC tag (e.g. `v1.2.0-rc1`). Use the `/pr` command to open a PR against `master`.

10. *Merge* — user reviews the PR and merges. No squashing without explicit instruction — preserve the commit history.
