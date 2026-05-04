# CI/CD Pipeline Design

**Status:** Accepted — see ADR 0005  
**Date:** 2026-05-04

---

## Context

This document describes the end-to-end CI/CD pipeline for the codex-slack project: what
triggers it, what it builds, how images are promoted across environments, and why the CD
daemon pattern was chosen over alternatives.

The project produces two Docker images:

| Image | Purpose |
|---|---|
| `ghcr.io/<org>/codex-slack-master` | Master orchestrator + single-session bot |
| `ghcr.io/<org>/codex-slack-agent-minimal` | Lean agent worker spawned by master |

Both images are deployed to three environments: test bed (ephemeral, CI), staging
(persistent, `latest`), and production (persistent, pinned semver).

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
┌──────────────────────────────────────────────────────────────────┐
│  Developer pushes commits to a PR branch                         │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  ci-pr.yml          │  ← GitHub Actions (GHA runner)
                    │  1. pytest          │
                    │  2. docker build    │
                    │     (no push)       │
                    └──────────┬──────────┘
                               │ all jobs green?
                    ┌──────────▼──────────┐
                    │  PR can be merged   │  ← branch protection gate
                    └──────────┬──────────┘
                               │ human merges to master
          ┌────────────────────▼────────────────────┐
          │  publish-master.yml                      │  ← GHA runner
          │  publish-agent-minimal.yml               │
          │  Build both images                       │
          │  Push :latest + :sha-<hash> to GHCR      │
          └────────────────────┬────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  STAGING            │  ← persistent host
                    │  CD daemon polls    │
                    │  GHCR every 5 min   │
                    │  Detects new digest │
                    │  docker compose up  │
                    └──────────┬──────────┘
                               │ human smoke-tests staging
                               │ decides it's ready
                    ┌──────────▼──────────┐
                    │  git tag v1.2.3     │  ← human action
                    │  git push --tags    │
                    └──────────┬──────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │  publish-master.yml (tag trigger)        │  ← GHA runner
          │  publish-agent-minimal.yml               │
          │  Build both images                       │
          │  Push :v1.2.3 + :sha-<hash> to GHCR      │
          └────────────────────┬────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  PRODUCTION         │  ← persistent host
                    │  CD daemon config:  │
                    │  CD_IMAGE_TAG=v1.2.3│
                    │  Detects new digest │
                    │  docker compose up  │
                    └─────────────────────┘
```

---

## Trigger rules

| Git event | Workflow triggered | Effect |
|---|---|---|
| Commit pushed to a PR branch | `ci-pr.yml` | Run pytest + docker build (no push) |
| Merge to `master` | `publish-master.yml`, `publish-agent-minimal.yml` | Build + push `:latest` + `:sha-<hash>` |
| Push `v*` tag | `publish-master.yml`, `publish-agent-minimal.yml` | Build + push `:v1.2.3` + `:sha-<hash>` |
| Manual trigger | Any of the above via workflow_dispatch | On-demand build/publish |

Path filters ensure workflows only run when relevant files change (src/, requirements.txt,
Dockerfiles, etc.) — unrelated file changes (docs, config) do not trigger image builds.

---

## Environments

### Test bed (ephemeral, CI)

- **Where:** inside the GitHub Actions runner VM — no persistent host
- **Lifetime:** exists only for the duration of one workflow job
- **Trigger:** every commit pushed to a PR branch
- **What runs:**
  - `pytest --tb=short -q` against the full test suite
  - `docker build` for both images (master and agent-minimal), no push
  - smoke test of the agent-minimal runtime contract (`codex`, `claude`, `gh`, `--help`)
- **Gate:** PR cannot be merged to master until these jobs pass (GitHub branch protection)

### Staging (persistent, tracks `:latest`)

- **Where:** a persistent host (VM or bare metal)
- **CD config:** `CD_IMAGE_TAG=latest`
- **Promotion trigger:** automatic — every merge to master produces a new `:latest` image;
  the CD daemon detects the digest change within one poll cycle (≤5 min) and redeploys
- **Purpose:** always reflects the latest merged state; used for human verification before
  a release is tagged

### Production (persistent, tracks semver)

- **Where:** a persistent host (VM or bare metal), separate from staging
- **CD config:** `CD_IMAGE_TAG=v1.2.3` (pinned to a specific release)
- **Promotion trigger:** human pushes a `v*` tag to git; the GHA publish workflow builds and
  pushes the semver-tagged image; the operator then updates `CD_IMAGE_TAG` in the
  production host's environment and restarts the daemon (or the daemon is already configured
  for the new tag)
- **Purpose:** stable, intentionally promoted releases only

---

## The CD daemon — design and trade-offs

### What it does

`src/cd/` is a Python process running on the deployment host. Every N seconds (default 300)
it:

1. Pulls `<image>:<tag>` from GHCR and reads the repo-digest
2. Compares the digest to the last deployed digest stored in a JSON state file
3. If changed: runs `docker compose up --force-recreate` with the new image
4. Waits `health_check_delay_seconds`, then checks the container is still running
5. If the health check fails and `CD_ROLLBACK_ON_FAILURE=true`: re-deploys the previous
   digest and re-checks health
6. Sends deploy/rollback/failure notifications to a Slack or Discord webhook

### Why pull-based is appropriate here

The pull pattern (daemon on the host, no inbound connections required) suits this project
because:

- **Firewall / NAT friendliness.** The deployment host makes only outbound HTTPS requests
  to GHCR. No inbound SSH port needs to be opened, and no GitHub runner IP allowlist is
  needed.
- **Rollback is built-in.** If a new image is unhealthy the daemon automatically re-deploys
  the previous known-good digest without human intervention.
- **Notification is built-in.** Deploy, rollback, and failure events are sent to the same
  Slack/Discord channels the team already monitors.

### Limitations and planned improvements

| Limitation | Current state | Planned mitigation |
|---|---|---|
| Polling lag | ≤5 min between image push and deploy | Add a webhook receiver endpoint to the daemon; GHA POSTs to it after push to trigger an immediate check |
| Silent daemon failure | If the daemon exits, deployments stop | Set `restart: unless-stopped` on the daemon compose service; monitor webhook silence as an alert signal |
| No deploy history in GitHub | Deploys are invisible to GitHub UI | Webhook notifications to Slack/Discord provide the audit trail |

### What if you removed the CD daemon?

Two alternatives, in order of simplicity:

**Option A — Push-based SSH deploy from GitHub Actions (simpler)**

Add a `deploy` job to each publish workflow. After the image is pushed, the job SSHes into
the deployment host and runs:

```bash
docker compose pull && docker compose up -d
```

Pros: instant deploy (no polling), deploy history visible in GitHub workflow runs, no extra
process to operate.  
Cons: requires storing an SSH private key in GitHub Secrets and opening an inbound SSH rule
for GitHub runner IPs. If the host is behind NAT or a strict firewall, this is not viable.

**Option B — Watchtower (drop-in open-source daemon)**

Replace `src/cd/` with the `containrrr/watchtower` container. It does the same poll-detect-
redeploy loop and is battle-tested. You lose the built-in rollback-on-failure logic and the
Slack/Discord notification integration, but you also stop maintaining custom daemon code.

For this project the current daemon is retained because the firewall constraint is real,
the rollback logic is valuable, and the daemon is already implemented.

---

## Should we add Jenkins?

No. Jenkins is not adopted.

GitHub Actions covers 100% of this project's CI/CD needs:
- Hosted runners are free for public repos and cheap for private ones
- Secrets, caches, and artifact storage are built in
- The existing publish workflows already use GHA and work correctly

Jenkins would require:
- A Jenkins server to provision, maintain, patch, and back up
- A separate secret store to manage
- Plugin version management (plugins regularly break across Jenkins upgrades)
- Network configuration so Jenkins can reach GHCR and deployment hosts

The only scenario where Jenkins makes sense is if builds require on-premises agents with
access to internal resources that GitHub-hosted runners cannot reach. In that case,
**self-hosted GitHub Actions runners** (not Jenkins) are the preferred solution — they run
your code on your hardware while keeping the same workflow YAML and GitHub secrets
integration.

---

## Branch protection setup (required)

To enforce the PR test gate, configure the following in GitHub → Settings → Branches → `master`:

- **Require status checks to pass before merging**: enabled
  - Required checks: `pytest`, `Docker build check`
- **Require branches to be up to date before merging**: enabled
- **Do not allow bypassing the above settings**: enabled (for all users including admins)

---

## Image tagging strategy

| Tag | When pushed | Who tracks it |
|---|---|---|
| `:latest` | Every merge to `master` | Staging CD daemon |
| `:sha-<7-char-hash>` | Every build (master push or tag push) | Nobody automatically — used for pinned rollback references |
| `:v1.2.3` | When `v1.2.3` tag is pushed | Production CD daemon |

Production should **never** be configured with `CD_IMAGE_TAG=latest`. Always pin to a
semver tag. This ensures production only changes when a human pushes a release tag.

---

## Rollback procedure

**Staging:** push a revert commit to master → CI passes → new `:latest` is built → daemon
deploys it automatically.

**Production:** push the previous working `v*` tag (or a new patch tag pointing to the last
known-good commit) → new image is built → update `CD_IMAGE_TAG` in production config and
restart daemon. Alternatively, if the daemon's rollback-on-failure triggered automatically,
no action is needed.
