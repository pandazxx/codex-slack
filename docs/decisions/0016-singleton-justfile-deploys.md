---
title: "ADR-0016: Singleton tag-based staging/prod deploys via justfile; retire CD daemon and multi-version staging"
status: proposed
date: 2026-07-11
decision-makers: [architect, engineer, senior-sre]
consulted: [sre, doc-writer]
informed: [tester, reviewer]
---

## Context and Problem Statement

Issue #245 asks for a `just deploy <env> <tag>` entry point backed by a
single `.env` at the repo root. Delivering that cleanly forced two decisions
that were not part of the issue text but cannot be deferred: (a) what shape
should staging have, given that the current multi-version Traefik shape
exists only to serve pre-merge UAT of parallel release candidates, and (b)
does the CD daemon (`src/cd/`) coexist with an explicit `just deploy`, or
does one of them own staging/prod outright?

The status quo has three environment shapes (dev behind Traefik,
staging-UAT multi-version behind Traefik, and a nominal singleton
production that has never been provisioned) plus a pull-based CD daemon
that owns staging under ADR-0005 §2 and §4. Every one of those shapes has
its own `.env` conventions, its own overlay file, its own runbook, and its
own set of exported variables. The complexity is disproportionate to the
size of this project (single-host docker compose, one master container per
env) and it makes issue #245's "one entry point" goal impossible without
first collapsing shapes.

The target model is **three environment levels, two shapes**. Dev is the
multi-tenant, multi-stack, disposable environment where developers and code
agents each run one stack per ongoing branch — built from the current
commit, no tagging, routed by Traefik. Staging and prod are exactly the
same shape: a single long-lived stack per host, deployed only from frozen
CI-built image tags, master published on host port 8080. This ADR changes
nothing about the dev level — it collapses the two competing staging
shapes into the prod shape.

## Decision Drivers

- Reduce the number of staging topologies from two to one. Two topologies
  means two overlays, two runbook trees, two mental models, and a permanent
  "which staging do you mean?" tax on every conversation.
- Make deploys explicit and digest-transparent. An operator (human or the
  `sre` agent) should be able to point at exactly one command and know what
  ran, when, and against which digest — without polling lag or a
  daemon-owned reconciliation loop between "I tagged it" and "it's live".
- Preserve ADR-0005's CI build/tag/promotion guarantees (RC-based flow,
  `:rc` mutable → `:v*` bit-identical retag). Those are orthogonal to how
  deploys are executed and should not be renegotiated here.
- Minimise the operational surface that the `sre` agent has to know about.
  Fewer runbooks, fewer overlays, fewer environment variables.
- Keep the dev workflow (N branch envs per host behind Traefik) untouched —
  that shape does real work every day and is not the source of the pain.

## Considered Options

1. **Singleton staging + `just deploy` as the only staging/prod path; retire
   the CD daemon and the multi-version Traefik staging shape.**
2. **Keep the CD daemon on staging/prod; add `just deploy` as a manual
   override that both humans and the daemon can race against.**
3. **Keep the multi-version Traefik staging shape for pre-merge UAT; add a
   separate singleton `just deploy` path for post-merge staging refresh and
   for prod.**

## Decision Outcome

*Chosen option:* Option 1 — singleton `just deploy` owns staging and prod;
CD daemon and multi-version staging are removed — because it collapses
staging to one shape, gives issue #245 a single unambiguous deploy entry
point, and removes an entire subsystem (`src/cd/`) whose value proposition
does not survive the arrival of an explicit `just deploy`. The one honest
cost, serialized RC UAT, is judged smaller than the ongoing cost of
maintaining two staging topologies plus a polling daemon plus their
respective `.env` generators and runbooks.

### Consequences

- *Good:* the compose tree becomes one neutral base plus one additive
  overlay per shape: `docker-compose.yml` (shared intersection — no
  `build:`, no `ports:`, no digest pin) + `docker-compose.dev.yml`
  (renamed from `docker-compose.override.yml`, so nothing auto-merges
  implicitly) + `docker-compose.deploy.yml`. Overlays only add — no
  `!reset` subtraction, no Compose-version floor.
- *Good:* one staging shape, one overlay (`docker-compose.deploy.yml`), one
  deploy command, one place `.env` lives. `sre` agent's operation set
  shrinks (staging-up/staging-down runbooks retired, replaced by
  deploy/undeploy). Deploys become synchronous, explicit, and easy to
  audit — no polling window between tag push and deploy.
- *Good:* the CD-daemon codebase (`src/cd/`), its Dockerfile, its example
  compose file, its `.env` generator (`scripts/gen-env.sh`), its runbook
  (`docs/guides/runbooks/cd-daemon.md`), and its CI build job
  (`build-cd-daemon` in `.github/workflows/build-push.yml`) are all deleted.
  Roughly one subsystem's worth of surface area comes out of the tree.
- *Good:* prod is a natural extension — the same `just deploy prod <tag>`
  works the day `PROD_DOCKER_HOST` is populated. No second promotion path
  to design.
- *Bad:* pre-merge UAT for release candidates is serialized on the staging
  singleton. Only one `v*-rc*` tag can occupy staging at a time. Teams
  coordinate on who holds staging. Under the old flow, multiple RCs could
  be UAT'd in parallel behind Traefik.
- *Bad:* deploys are no longer automatic on tag push. Someone (human or
  agent) must run `just deploy staging <tag>`. This is the intended
  tradeoff — explicit over automatic — but it does mean the `post-merge`
  workflow now includes a deploy step that used to be implicit.
- *Bad:* the daemon's rollback-on-health-check-failure behaviour goes away.
  Rollback becomes `just deploy staging <previous-tag>` — one command,
  operator-initiated. Acceptable for a single-host project with an
  immutable-tag promotion trail.
- *Neutral:* ADR-0005's CI flow (`build-master`, `build-agent-minimal`, RC
  tag → `:rc` → `promote-release` retag → `:v*`) is unchanged. Only the
  `build-cd-daemon` job and the CD-daemon deploy model are removed.

### Confirmation

- Design doc `docs/design/justfile-dotenv-deploy.md` describes exactly this
  topology and is signed off before implementation begins.
- ADR-0005's header is updated to `status: accepted (superseded in part by
  ADR-0016)`. The superseded scope (§2 and §4) is called out in that ADR.
- After implementation: `.github/workflows/build-push.yml` no longer
  contains a `build-cd-daemon` job; `src/cd/`, `Dockerfile.cd-daemon`,
  `docker-compose.cd-daemon.example.yml`, `scripts/gen-env.sh`,
  `docker-compose.staging.yml`, `docs/guides/runbooks/cd-daemon.md`,
  `.sre/operations/staging-up.md`, and `.sre/operations/staging-down.md` no
  longer exist in the tree. `.sre/operations/deploy.md` and
  `.sre/operations/undeploy.md` do. `docker-compose.override.yml` is
  renamed to `docker-compose.dev.yml`, and `docker-compose.yml` contains
  no `build:`, `ports:`, or image digest pin.
- First canonical staging refresh via `just deploy staging master` after
  the implementation PR merges is observed green.

## Pros and Cons of the Options

### Option 1: Singleton `just deploy` owns staging and prod; retire CD daemon and multi-version staging

One shape for staging and prod (singleton, host-port-published, no Traefik).
`just deploy <env> <tag>` is the only entry point. Dev shape is unchanged.

- Pro: single overlay, single runbook per operation, single `.env` layout —
  matches issue #245's "one entry point" ask.
- Pro: `src/cd/` and its whole footprint (Dockerfile, compose example,
  `.env` generator, runbook, CI job) come out of the tree.
- Pro: prod is a code path that works the day `PROD_DOCKER_HOST` is set —
  no separate design.
- Pro: deploys are synchronous and digest-transparent; no polling lag.
- Con: pre-merge RC UAT is serialized on the staging singleton. Teams
  coordinate; throughput drops when multiple RCs are queued.
- Con: no automatic health-check rollback; rollback is a manual
  `just deploy staging <previous-tag>`.
- Con: the "post-merge staging refresh" step becomes explicit instead of
  daemon-driven — one extra command in the workflow.

### Option 2: Keep the CD daemon; add `just deploy` as a manual override

`just deploy` lives alongside the CD daemon on the same host. Both target
the singleton `codex-slack-master` container.

- Pro: preserves ADR-0005 §2 (pull-based CD on staging/prod) as-is.
- Pro: automatic deploys on `:rc` digest change stay.
- Con: two systems own the same container. If both are active on the same
  host they fight — the daemon rolls back what `just deploy` just pushed,
  or vice versa. Requires a "either/or per host" guard that adds its own
  operational rules.
- Con: two sources of `.env` truth (repo-root `.env` for `just`, generated
  `gen-env.sh` output for the daemon). Two runbook trees.
- Con: keeps the daemon's polling lag in the deploy loop even when a human
  explicitly deploys, defeating the "synchronous deploy" property that
  motivates `just deploy` in the first place.
- Con: the daemon's whole surface area (code, Dockerfile, CI job, runbook,
  compose example) stays.

### Option 3: Keep the multi-version Traefik staging shape for pre-merge UAT; add a singleton `just deploy` for post-merge / prod

Feature-branch RC UAT stays multi-version behind Traefik (unchanged
`docker-compose.staging.yml`). `just deploy` handles post-merge canonical
staging refresh and future production.

- Pro: preserves parallel RC UAT throughput.
- Pro: dev workflow unchanged, prod story unblocked.
- Con: two staging topologies to maintain — `docker-compose.staging.yml`
  (Traefik, multi-version) *and* `docker-compose.deploy.yml` (singleton).
- Con: `VERSION_SLUG` plumbing, Traefik-hostname routing, and per-tag
  compose project juggling all stay in the tooling.
- Con: the mental model "staging is one thing… except when it's several
  things" is permanent. Every doc has to explain both.
- Con: the CD-daemon question is orthogonal and would still need a separate
  decision.

## Scope of ADR-0005 that this supersedes

This ADR supersedes ADR-0005 *in part*. Specifically:

- **ADR-0005 §2 (CD daemon on staging and production; agent control on test
  bed).** The "CD daemon on staging and production" half is superseded. The
  "agent control on the test bed" half (dev / test bed remains
  agent-managed, no daemon) stays exactly as ADR-0005 stated — that piece
  is orthogonal.
- **ADR-0005 §4 (Three-environment promotion path).** The parts of the
  diagram that describe the CD daemon polling `:rc` and auto-deploying to
  staging, and the CD daemon polling `:v*` and auto-deploying to
  production, are superseded. Staging and production now receive
  `just deploy` calls initiated by an operator (human or `sre` agent). The
  RC-tag → `:rc` → merge-to-master → `:v*` promotion chain in
  `.github/workflows/build-push.yml` is unchanged.

What ADR-0005 says that this ADR does **not** touch:

- §1 (GitHub Actions only, no Jenkins).
- §3 (No merge to master before UAT sign-off; RC-based flow).
- §5 (RC-based promotion with bit-identical production image via retag).
- §6 (Image tagging strategy — `:sha-<hash>`, `:v*-rc*`, `:rc`, `:v*`; no
  `:latest`).

ADR-0005's status header is updated to reflect the partial supersession per
MADR convention. The relevant "Consequences" and "Implementation Notes"
bullets in ADR-0005 that assume the daemon is running remain readable as
historical context but should be interpreted through this ADR going
forward.
