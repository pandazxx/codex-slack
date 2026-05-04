# CI/CD Pipeline Design

**Status:** Accepted — see ADR 0005  
**Date:** 2026-05-04

---

## Context

This document describes the end-to-end CI/CD pipeline for the codex-slack project: what
triggers it, what it builds, how images are promoted across environments, and the rationale
for each design choice.

The project produces two Docker images:

| Image | Purpose |
|---|---|
| `ghcr.io/<org>/codex-slack-master` | Master orchestrator + single-session bot |
| `ghcr.io/<org>/codex-slack-agent-minimal` | Lean agent worker spawned by master |

Both images are deployed to three persistent environments with distinct purposes and
different deployment models:

| Environment | Purpose | Managed by |
|---|---|---|
| **Test bed** | Agent development testing and pre-UAT troubleshooting | LLM agent |
| **Staging** | User and agent UAT; issue reproduction | CD daemon |
| **Production** | Stable working environment for real users | CD daemon |

---

## What is a GitHub Actions runner?

A GitHub Actions **runner** is the machine that executes a workflow job. GitHub provides
hosted runners — ephemeral virtual machines (Ubuntu, Windows, macOS) that GitHub spins up
fresh for each job and discards when the job finishes. `runs-on: ubuntu-latest` in a
workflow file requests one of these.

Hosted runners have:
- ~7 GB RAM, 2 vCPUs, ~14 GB SSD (for Ubuntu)
- Docker, Python, Node.js, and most common tools pre-installed
- No persistence between runs — every job starts from a clean slate
- Network access to the internet (GHCR, PyPI, etc.)

You can also run **self-hosted runners** on your own machines (useful if builds need access
to internal networks, GPUs, or large caches). For this project, GitHub-hosted runners are
sufficient.

---

## Pipeline overview

```
feature branch
      │
      │  agent triggers: gh workflow run build-on-demand.yml \
      │                    --ref feat/x --field label=feat-x
      ▼
  GHA: build-on-demand.yml
  builds both images from feat/x
  pushes :sha-<hash> to GHCR
  (agent waits on run ID, no notification needed)
      │
      ▼
  TEST BED  ← persistent host, agent-managed, no daemon
  agent deploys :sha-<hash>, tests, iterates
  agent: "this build is solid, merge the PR"
      │
      │  PR merged to master
      ▼
  GHA: publish-master.yml  (existing, triggered by master push)
  builds from master HEAD
  pushes :sha-<hash> to GHCR
      │
      │  agent or human triggers:
      │  gh workflow run promote-staging.yml \
      │    --field sha=<hash>
      ▼
  GHA: promote-staging.yml
  retags :sha-<hash> → :staging in GHCR
  (no build — same image bits, new tag)
      │
      ▼
  STAGING  ← persistent host, CD daemon, CD_IMAGE_TAG=staging
  daemon detects new :staging digest, deploys
  user + agent perform UAT
      │
      │  human: git tag v1.2.3 && git push --tags
      ▼
  GHA: publish-master.yml  (tag trigger)
  builds from tagged commit
  pushes :v1.2.3 + :sha-<hash> to GHCR
      │
      ▼
  PRODUCTION  ← persistent host, CD daemon, CD_IMAGE_TAG=v1.2.3
  daemon detects new :v1.2.3 digest, deploys
```

---

## Trigger rules

| Trigger | Workflow | What it does |
|---|---|---|
| Commit pushed to a PR branch | `ci-pr.yml` | Run pytest + docker build validation (no push) |
| Agent/human triggers manually | `build-on-demand.yml` | Build both images from any ref, push `:sha-<hash>` |
| Merge to `master` | `publish-master.yml`, `publish-agent-minimal.yml` | Build from master, push `:sha-<hash>` |
| Agent/human triggers manually | `promote-staging.yml` | Retag a `:sha-<hash>` → `:staging` in GHCR |
| Push `v*` tag | `publish-master.yml`, `publish-agent-minimal.yml` | Build from tag, push `:v1.2.3` + `:sha-<hash>` |

Path filters on `ci-pr.yml` and the publish workflows ensure they only fire when relevant
files change (`src/`, `requirements.txt`, `Dockerfile*`). Docs and config changes do not
trigger image builds.

---

## Environments

### Test bed (persistent, agent-managed)

- **Where:** persistent host — the same host running the master orchestrator is sufficient
- **Purpose:** agent development testing and pre-UAT troubleshooting before any build
  touches staging or production
- **Managed by:** LLM agent directly — the agent issues deploy commands via the Podman
  socket it already controls; no CD daemon runs here
- **What the agent deploys:** any `:sha-<hash>` image — from a feature branch build
  (triggered via `build-on-demand.yml`) or from a master merge build
- **Intentionally unstable:** the test bed may run broken or experimental builds; it is
  never shown to end users

**Agent workflow for test bed deployment:**

```
# 1. Trigger a build of the feature branch
gh workflow run build-on-demand.yml \
  --ref feat/new-auth \
  --field label=feat-new-auth

# 2. Wait for the build to finish (synchronous, uses run ID)
gh run watch <run-id>

# 3. Deploy the built image to test bed
docker compose -f docker-compose.testbed.yml up -d --force-recreate \
  codex-slack-master  # with MASTER_RUNTIME_IMAGE=ghcr.io/.../master:sha-<hash>

# 4. Test, iterate. If broken, the agent reports and fixes on the branch.
# 5. When solid: "this build is ready, merge the PR"
```

No CD daemon is needed because the agent controls the deploy lifecycle directly. The agent
also handles rollback explicitly — it simply redeploys the previous SHA if a new one is
broken.

### Staging (persistent, CD daemon, `:staging`)

- **Where:** persistent host, separate from test bed/production
- **Purpose:** user and agent UAT in a stable-enough environment. Staging uses the same
  Slack/Discord workspace or a dedicated staging workspace so users can reproduce issues and
  validate flows without risk to production
- **CD config:** `CD_IMAGE_TAG=staging`
- **Promotion trigger:** explicit human or agent action — run `promote-staging.yml`
  `workflow_dispatch`, which retags the approved `:sha-<hash>` as `:staging` in GHCR; the
  daemon detects the new digest and deploys automatically
- **Rollback:** daemon rolls back automatically on health-check failure; manual rollback is
  done by re-promoting a known-good SHA

### Production (persistent, CD daemon, `:v1.2.3`)

- **Where:** persistent host, separate from staging
- **Purpose:** stable working environment for real users; only receives human-approved
  release builds
- **CD config:** `CD_IMAGE_TAG=v1.2.3` (a specific semver tag, updated per release)
- **Promotion trigger:** human pushes a `v*` git tag; GHA builds and pushes `:v1.2.3`;
  operator updates `CD_IMAGE_TAG` in the production `.env` and restarts the daemon (or the
  daemon is pre-configured for the new tag)
- **Invariant:** production `CD_IMAGE_TAG` must always be a semver string; never `:staging`,
  never `:latest`, never a SHA directly

---

## The CD daemon — design and trade-offs

`src/cd/` is a Python process running on the deployment host. Every N seconds it:

1. Pulls `<image>:<tag>` from GHCR and reads the repo-digest
2. Compares to the last deployed digest in a JSON state file
3. If changed: runs `docker compose up --force-recreate` with the new image
4. Waits `health_check_delay_seconds`, then checks the container is still running
5. If unhealthy and `CD_ROLLBACK_ON_FAILURE=true`: re-deploys the previous digest
6. Sends deploy/rollback/failure notifications to a Slack or Discord webhook

The daemon is used on **staging and production only**. The test bed does not run a daemon —
the agent manages the test bed directly.

### Why pull-based (no inbound connections needed)

- The deployment host makes only outbound HTTPS requests to GHCR
- No inbound SSH port, no GitHub runner IP allowlist required
- Works behind NAT or strict firewalls

### CD daemon config differences per environment

| Setting | Staging | Production |
|---|---|---|
| `CD_IMAGE_TAG` | `staging` | `v1.2.3` |
| `CD_POLL_INTERVAL_SECONDS` | `300` | `600` |
| `CD_ROLLBACK_ON_FAILURE` | `true` | `true` |
| `CD_HEALTH_CHECK_DELAY_SECONDS` | `30` | `60` |
| Notification channel | `#staging-deploys` | `#prod-alerts` |

Production uses a longer poll interval because production deploys are deliberate — nobody
is watching a clock. Staging uses 5 minutes as a reasonable balance between feedback speed
and GHCR polling load.

### Limitations and mitigations

| Limitation | Current state | Mitigation |
|---|---|---|
| Polling lag | ≤5 min (staging), ≤10 min (production) | Acceptable given explicit promotion gates |
| Silent daemon failure | If daemon exits, deployments stall | `restart: unless-stopped` on daemon compose service; monitor webhook silence |
| No deploy history in GitHub UI | Deploys invisible to GitHub | Slack/Discord webhook notifications are the audit trail |

---

## Image tagging strategy

| Tag | When pushed | Tracks who |
|---|---|---|
| `:sha-<7-char-hash>` | Every build (branch, master, or tag trigger) | Nobody automatically — deployed explicitly by agent or promote workflow |
| `:staging` | When `promote-staging.yml` is run | Staging CD daemon |
| `:v1.2.3` | When `v1.2.3` git tag is pushed | Production CD daemon |

**No `:latest` tag.** Removing `:latest` eliminates the ambiguity of "what is latest" and
forces every promotion to be an explicit, traceable action. Each environment tracks a tag
with clear semantics:

- `:sha-<hash>` — immutable, identifies exact code, used by agent on test bed
- `:staging` — mutable, always points to the last promoted build, used by staging daemon
- `:v1.2.3` — immutable semver, used by production daemon

---

## Agent-triggered on-demand builds

The agent triggers builds of arbitrary branches using `gh workflow run` against the
`build-on-demand.yml` workflow. This workflow:

- Accepts a `ref` (branch, tag, or SHA) and an optional `label`
- Builds both images (`master` and `agent-minimal`) from that ref
- Pushes only `:sha-<hash>` — never `:staging`, never a semver
- The agent waits on the run ID synchronously with `gh run watch <run-id>`

This pattern keeps unverified branch code entirely isolated from the promotion chain.
A branch image can only enter staging if a human or agent explicitly runs `promote-staging.yml`
with a SHA — and that SHA must come from a build that passed CI.

---

## Should we add Jenkins?

No. See ADR 0005. GitHub Actions covers all CI/CD needs. The master orchestrator's `gh` CLI
is the bridge between the agent and GHA — no separate CI server is required.

---

## Branch protection setup (required)

Configure in GitHub → Settings → Branches → `master`:

- **Require status checks to pass before merging**: enabled
  - Required checks: `pytest`, `Docker build check`
- **Require branches to be up to date before merging**: enabled
- **Do not allow bypassing the above settings**: enabled (including admins)

---

## Rollback procedures

**Test bed:** agent redeploys the previous `:sha-<hash>` explicitly.

**Staging:** re-run `promote-staging.yml` with the last known-good SHA to push a new
`:staging` digest; daemon redeploys automatically. If the daemon's built-in rollback
triggered, no action needed.

**Production:** push a new `v*` tag pointing to the last known-good commit (e.g. `v1.2.4`
pointing to same commit as `v1.2.2`); update `CD_IMAGE_TAG` in production env and restart
daemon. Daemon's built-in rollback handles transient failures automatically.
