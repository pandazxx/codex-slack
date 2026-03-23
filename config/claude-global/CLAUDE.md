# Global Agent Instructions

## Environment

This agent runs inside a container. Users do not have direct access to the workspace. The primary interfaces are:

- *GitHub* — commits, pull requests, issues, and tags are the canonical output. Always push and open a PR for the user to review.
- *Reply* — the only real-time channel. Report progress, ask questions, and share links (PR, commit, issue URLs) here.

Never assume the user can inspect files locally.

## Git Workflow

- Never commit directly to `master` or `main`. Create a feature branch first.
- Name branches after the work: `feat/`, `fix/`, `refactor/`, `docs/`, etc.
- Use the `commit` skill to stage, write, and push. Use the `pr` skill to open a PR against `master`.
- Every code change must be committed and pushed before the task is considered done.

## Reply Formatting

- Start every reply with `<agent_name> says:` — where `<agent_name>` is `$AI_AGENT_NAME`, or `agent` if unset.
- Use `-` for bullet lists. Use bold for section labels instead of `#` headers.
- Use backticks for inline code and triple backticks for code blocks.
- No HTML, no horizontal rules, no deep nesting.

*If AGENT_FRONTEND=discord:* use `**bold**`. Use mermaid blocks for diagrams — they render as images.

*If AGENT_FRONTEND=slack (or unset):* use `*bold*`. No mermaid — use plain text or ASCII diagrams.

## Knowledge Persistence

Sessions end and context resets. The repository is the only durable record — write decisions, discoveries, and fixes to the repo as you go.

- Before starting significant work: read `docs/decisions/` and `docs/knowledge-base/` for prior context.
- After every non-trivial fix or discovery: update `docs/knowledge-base/lessons-learned.md` and commit it with the fix.
- For every significant architectural or design choice: produce an ADR in `docs/decisions/`.
- Never re-litigate settled decisions. If context is unclear when resuming, read the docs and `git log` first.

## Project Layout

Default layout for a well-structured engineering project. Adapt per-repo as needed; document deviations in `.claude/CLAUDE.md`.

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
├── decisions/            # Architecture Decision Records (ADRs)
│   └── NNNN-title.md     #   MADR v4 format; numbered sequentially
│
├── design/               # Design documents for features and subsystems
│   └── feature-name.md   #   Problem, goals, non-goals, solution, alternatives
│
├── knowledge-base/       # Accumulated operational knowledge
│   ├── lessons-learned.md #  Post-mortems and issue fixes (append-only log)
│   └── faq.md            #  Frequently asked questions and answers
│
├── releases/             # Release notes, one file per release
│   └── vX.Y.md           #  What changed, why, migration steps
│
├── guides/               # How-to guides and runbooks
│   ├── runbooks/         #  Step-by-step operational procedures (incident, deploy, rollback)
│   └── onboarding.md     #  Getting-started guide for new contributors
│
├── test-plans/           # Test case specifications and acceptance criteria
│   └── feature-name.md   #  Scope, test cases, pass/fail criteria, edge cases
│
├── references/           # Stable technical references
│   ├── api.md            #  API endpoints, request/response schemas
│   ├── config.md         #  All configuration keys, types, defaults, and descriptions
│   └── schemas/          #  Data schemas, ERDs, protocol specs
│
└── manuals/              # End-user and operator manuals
    ├── user-manual.md    #  Feature walkthroughs for end users
    └── ops-manual.md     #  Deployment, monitoring, backup, and recovery
```

*Document conventions:*

- *ADRs* (`decisions/`): MADR v4. Status: `proposed` → `accepted` → `deprecated`/`superseded`. Never delete — supersede.
- *Design docs* (`design/`): written before or alongside implementation. Sections: Context, Goals, Non-goals, Design, Alternatives considered, Open questions.
- *Lessons learned* (`knowledge-base/lessons-learned.md`): append-only. Each entry: date, summary, root cause, fix applied, prevention.
- *Runbooks* (`guides/runbooks/`): actionable, step-by-step. Written for someone responding under pressure. Include: trigger condition, impact, steps, rollback, escalation.
- *Test plans* (`test-plans/`): link to the feature design doc. Cover happy path, edge cases, failure modes, and non-functional requirements.
- *References* (`references/`): factual and stable. Prefer tables. Keep in sync with the implementation — stale references are worse than none.

## Skills

Invoke with the Skill tool. Defined in `~/.claude/commands/`.

- `commit` — stage, write a Conventional Commit message, and push; cleans workspace and reports SHA + URL.
- `pr` — push branch and open a PR against `master` with auto-generated title and checklist body.
- `tag` — create and push a semver tag; proposes next version if none given.
- `reply-formatter` — reformat a draft for the current `AGENT_FRONTEND`.

## Subagents

Invoke with the Agent tool. Defined in `~/.claude/agents/`.

- `architect` — plans solutions, evaluates tradeoffs, produces ADRs and design docs. Use for any significant design decision.
- `doc-writer` — writes and updates documentation; does not touch implementation files.
- `debugger` — root-cause analysis; returns diagnosis and recommended fix without modifying files.
- `test-runner` — runs the test suite and returns a structured pass/fail report.
- `housekeeper` — scans for dead code, unused imports, TODOs, and duplicates; returns a prioritised list without modifying files.
