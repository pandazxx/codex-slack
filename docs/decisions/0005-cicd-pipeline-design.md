# 0005 CI/CD Pipeline Design

- Status: accepted
- Date: 2026-05-04

## Context

The project deploys two Docker images (master orchestrator and agent-minimal worker) to three
environments: test bed, staging, and production. Before this ADR the repository had two GitHub
Actions publish workflows (push-triggered, master/tag only) and a custom CD daemon
(`src/cd/`) that polls GHCR for digest changes and redeploys via docker compose. There was
no PR-time test gate and no written policy for how code flows between environments.

Four design questions needed resolution:

1. Should Jenkins be adopted alongside or instead of GitHub Actions?
2. Is the CD daemon (pull-based CD) the right deployment model for all environments?
3. What are the formal trigger points and promotion rules for the three environments?
4. How should the test bed be managed given that its primary operator is an LLM agent, not
   a human release engineer?

## Decision

### 1. GitHub Actions only — no Jenkins

Jenkins is not adopted. The existing GitHub Actions workflows already handle image building
and publishing. Jenkins would add an additional server to operate, a separate secret store,
plugin lifecycle management, and network access requirements between Jenkins and GHCR — with
no capability gain over what GitHub Actions provides for this project.

If the team later requires on-premises build agents that cannot reach GitHub, self-hosted
GitHub Actions runners satisfy that requirement without Jenkins.

The master orchestrator's `gh` CLI is the bridge between the LLM agent and GitHub Actions
workflows — no separate CI server is needed for agent-triggered builds.

### 2. CD daemon on staging and production; agent control on test bed

The CD daemon is retained for staging and production but is **not used on the test bed**.

**Why pull-based fits staging and production:**

- The deployment host may be behind NAT or a firewall. Pull-based requires only outbound
  connections from the host to GHCR; no inbound SSH port or GitHub runner IP allowlist is
  needed.
- The daemon provides rollback-on-failure: if a health check fails after deploy it
  re-pulls and redeploys the previous digest automatically.
- The daemon is already implemented, tested, and in use.

**Why the test bed is agent-managed instead:**

The test bed's purpose is pre-UAT development testing and troubleshooting by LLM agents.
Agents need to:
- Deploy arbitrary builds (feature branches, specific SHAs) on demand
- Iterate rapidly between broken and working states
- Control the exact deploy lifecycle without waiting for a polling cycle

A passive polling daemon is the wrong model for this. The agent already controls container
lifecycle via the Podman socket that the master orchestrator holds. Agent-direct deployment
is simpler, faster, and more aligned with how agents work.

**Known limitations and mitigations for the CD daemon (staging/production):**

- *Polling lag* — up to 5 min on staging, up to 10 min on production. Acceptable given that
  both environments have explicit human promotion gates; nobody is watching a clock.
- *Silent daemon failure* — if the daemon process dies, deployments stop without alerting.
  Compose files must set `restart: unless-stopped` on the daemon service.

**Rejected alternative — push-based SSH deploy from GHA:**

After image push a GHA job would SSH into the deployment host and run
`docker compose pull && docker compose up -d`. Requires an SSH key stored in GHA secrets
and an inbound firewall rule for GitHub runner IP ranges. Not adopted; firewall constraint
is real.

### 3. Three-environment promotion path

```
feature branch
      │
      │  agent triggers build-on-demand.yml (workflow_dispatch)
      │  waits on run ID via: gh run watch <run-id>
      ▼
  GHA builds both images from branch ref
  pushes :sha-<hash> only (dead-end tag, cannot enter staging)
      │
      ▼
  TEST BED  ← agent-managed, no daemon
  agent deploys, tests, iterates
      │
      │  PR merged to master
      ▼
  GHA publish-master.yml builds from master, pushes :sha-<hash>
      │
      │  agent or human triggers promote-staging.yml (workflow_dispatch)
      │  retags :sha-<hash> → :staging in GHCR (no rebuild)
      ▼
  STAGING  ← CD daemon, CD_IMAGE_TAG=staging
  user + agent UAT
      │
      │  human: git tag v1.2.3 && git push --tags
      ▼
  GHA publish-master.yml (tag trigger) builds, pushes :v1.2.3
      │
      ▼
  PRODUCTION  ← CD daemon, CD_IMAGE_TAG=v1.2.3
```

**Test bed** — persistent host, agent-managed. The agent triggers `build-on-demand.yml`
via `gh workflow run`, waits synchronously on the run ID, then deploys the resulting
`:sha-<hash>` image directly. Used for testing feature branches before they merge to master.
No CD daemon runs here.

**Staging** — persistent host, CD daemon, `CD_IMAGE_TAG=staging`. Receives builds that
agents have validated on the test bed. Promotion from test bed to staging is an explicit
action: running `promote-staging.yml` which retags an approved SHA as `:staging`. Used for
user and agent UAT, and issue reproduction in a stable-enough environment.

**Production** — persistent host, CD daemon, `CD_IMAGE_TAG=v1.2.3`. Promotion is a
two-step human action: (1) push a `v*` git tag; (2) update `CD_IMAGE_TAG` in the production
environment's config. Receives only human-approved release builds.

### 4. On-demand branch builds via workflow_dispatch

The agent triggers builds of arbitrary branches using `gh workflow run` against a new
`build-on-demand.yml` workflow. The agent waits on the run ID synchronously rather than
relying on webhook notifications.

This pattern keeps unverified branch code isolated from the promotion chain. A branch image
can only enter staging via an explicit `promote-staging.yml` run — there is no automatic
path from a branch build into staging or production.

### 5. Image tagging strategy (`:latest` removed)

| Tag | When pushed | Used by |
|---|---|---|
| `:sha-<hash>` | Every build (branch, master push, tag push) | Agent deploys to test bed explicitly |
| `:staging` | When `promote-staging.yml` runs | Staging CD daemon |
| `:v1.2.3` | When `v1.2.3` git tag is pushed | Production CD daemon |

`:latest` is not used. It was ambiguous — "latest what?" — and created risk of accidental
promotion. Every tag now has unambiguous semantics and a clear owner.

## Alternatives Considered

### Jenkins

Rejected. See decision 1 above. No capability gap justifies the operational overhead.

### CD daemon on test bed

Rejected. The test bed is designed for agent-driven iterative testing. A polling daemon
cannot deploy on demand, cannot choose which SHA to deploy, and cannot handle the rapid
broken/fixed cycle that pre-UAT development involves. Agent-direct deployment is the right
model for this environment.

### Watchtower (third-party pull-based daemon)

Considered as a replacement for `src/cd/`. Watchtower is a mature open-source container
that monitors registries and redeploys. It would eliminate the custom daemon code but
remove the built-in rollback-on-failure logic, the Slack/Discord notification integration,
and the per-environment configurability. Not adopted; the existing daemon is retained.

### Push-based SSH deploy from GitHub Actions

Rejected. See decision 2 above. Requires inbound SSH access from GitHub runner IPs.

### Webhook notification from build-on-demand to agent

Considered for `build-on-demand.yml`: post a Slack/Discord message when the build
completes so the agent knows without polling. Rejected in favour of `gh run watch <run-id>`:
synchronous polling on the run ID is simpler, doesn't require webhook URL configuration,
and is already available via the `gh` CLI the agent holds.

### Kubernetes with image-update automation (Flux/ArgoCD)

Not applicable. The project runs on a single host with docker compose. Kubernetes
infrastructure would be disproportionate overhead.

## Consequences

Positive:

- PRs are gated on tests; broken code cannot reach master.
- Test bed is fast and flexible — agent deploys any SHA in seconds, no polling lag.
- Staging only receives builds the agent has explicitly approved.
- Production only receives human-tagged releases.
- No new infrastructure to operate (no Jenkins server, no Kubernetes cluster).
- `:latest` removal eliminates the ambiguity of what "latest" means across environments.

Tradeoffs:

- Staging and production deploys have polling lag (up to 5 and 10 min respectively).
  This is acceptable because both have explicit promotion gates — speed is not the concern.
- Agent must wait on `gh run watch` during on-demand builds (~minutes). The agent is
  blocked during this time.
- Two new GHA workflows are required (`build-on-demand.yml`, `promote-staging.yml`).
  `publish-master.yml` needs a minor update to stop pushing `:latest`.

## Implementation Notes

- Add branch protection rule on `master`: require `CI — PR Tests / pytest` and
  `CI — PR Tests / Docker build check` status checks before merge.
- Staging and production compose stacks must set `restart: unless-stopped` on the daemon.
- Production `CD_IMAGE_TAG` must always be a semver string, never `:staging` or `:latest`.
- `build-on-demand.yml` must push `:sha-<hash>` only — no promotion tags.
- `promote-staging.yml` retags an existing image in GHCR — it does not rebuild.
- Implement `build-on-demand.yml` and `promote-staging.yml` after this ADR is signed off.
