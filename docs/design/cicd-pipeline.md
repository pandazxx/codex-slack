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
| **Test bed** | Agent development testing and pre-UAT troubleshooting | LLM agent (direct) |
| **Staging** | User and agent UAT; issue reproduction | CD daemon (tracks `:rc`) |
| **Production** | Stable working environment for real users | CD daemon (tracks `:v1.2.3`) |

**Core invariant:** code is never merged to `master` before UAT sign-off. The feature
branch is built, tested on staging, and UAT-approved before the PR is merged. The image
deployed to production is the exact same image (same bits) that ran on staging during UAT
— guaranteed by retagging rather than rebuilding.

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
feat/x branch (never merged until UAT sign-off)
      │
      │  1. Agent develops on feature branch
      │
      ▼  2. Agent: gh workflow run build-on-demand.yml --ref feat/x
         GHA builds both images from feat/x, pushes :sha-<hash>
         Agent waits on run ID, deploys to test bed, iterates
      │
      ▼  3. Agent creates PR: feat/x → master
         GHA ci-pr.yml: pytest + docker build gate
      │
      ▼  4. Agent or human: git tag v1.2.3-rc1 && git push --tags
         (tag is on the BRANCH, not master)
      │
      ▼  5. GHA build-rc.yml triggers on v*-rc* tag
         builds both images from v1.2.3-rc1 commit
         pushes :v1.2.3-rc1 (immutable) + :rc (mutable, latest RC)
      │
      ▼  6. STAGING  ← CD daemon, CD_IMAGE_TAG=rc
         detects new :rc digest, deploys automatically
         notifies: "RC v1.2.3-rc1 deployed to staging"
      │
      ▼  7. User performs UAT on staging

  ┌── issues found ──────────────────────────────────────────────────┐
  │   back to step 1                                                 │
  │   agent fixes on feat/x, commits                                │
  │   git tag v1.2.3-rc2 && git push --tags                        │
  │   repeat from step 5 (new :rc deployed to staging)              │
  └──────────────────────────────────────────────────────────────────┘

      │  8. UAT sign-off
      │
      ▼  9. PR approved, merged to master
         Linear history only (rebase merge, branch must be up to date)
         master receives the exact tested commits
      │
      ▼  10. Human: git tag v1.2.3 && git push --tags (on master)
          GHA promote-release.yml triggers on v* (non-rc) tags on master
          retags :rc → :v1.2.3 in GHCR  ← no rebuild
          same image bits that ran on staging during UAT
      │
      ▼  11. Operator updates production .env: CD_IMAGE_TAG=v1.2.3
          PRODUCTION  ← CD daemon, CD_IMAGE_TAG=v1.2.3
          detects new :v1.2.3 digest, deploys
```

---

## Trigger rules

| Trigger | Workflow | What it does |
|---|---|---|
| Commit pushed to any PR branch | `ci-pr.yml` | Run pytest + docker build (no push) — merge gate |
| Agent/human triggers manually | `build-on-demand.yml` | Build from any ref, push `:sha-<hash>` only |
| `v*-rc*` tag pushed (any branch) | `build-rc.yml` | Build RC images, push `:v1.2.3-rc1` + `:rc` |
| `v*` non-rc tag pushed on `master` | `promote-release.yml` | Retag `:rc` → `:v1.2.3` in GHCR (no rebuild) |

**What no longer triggers a build:** pushing to `master`. Master is a stable pointer;
images are built from feature branch RC tags, not from master merges.

---

## Environments

### Test bed (persistent, agent-managed)

- **Where:** persistent host — co-located with the master orchestrator is sufficient
- **Purpose:** agent development testing and pre-UAT troubleshooting. May run broken or
  experimental builds; never exposed to end users
- **Managed by:** LLM agent directly via the Podman socket the master orchestrator holds
- **What deploys:** any `:sha-<hash>` from a feature branch build (`build-on-demand.yml`)
- **No CD daemon** — the agent controls deploy lifecycle directly and handles rollback
  by redeploying a previous SHA explicitly

**Agent deploy workflow:**

```bash
# 1. Trigger build of the feature branch
gh workflow run build-on-demand.yml \
  --ref feat/new-auth \
  --field label=feat-new-auth

# 2. Wait synchronously on the run ID
gh run watch <run-id>

# 3. Deploy the SHA to test bed
# (agent uses compose + the sha-tagged image)

# 4. Test, iterate. When solid: create the PR and push an RC tag.
```

### Staging (persistent, CD daemon, `:rc`)

- **Where:** persistent host, separate from test bed and production
- **Purpose:** UAT by users and agents. Staging uses real (or staging-specific) Slack/
  Discord credentials so users can exercise real flows without risk to production
- **CD config:** `CD_IMAGE_TAG=rc`
- **Promotion trigger:** pushing a `v*-rc*` tag on the feature branch. GHA `build-rc.yml`
  builds the image and updates `:rc` in GHCR. The daemon detects the digest change and
  redeploys automatically, then notifies via Slack/Discord
- **Multiple RC iterations:** each new RC tag (rc1, rc2, …) moves `:rc` to the new image.
  Staging always reflects the latest RC. This is intentional — if rc2 is pushed while rc1
  UAT is ongoing, staging updates to rc2
- **Rollback:** re-push a previous RC tag (e.g., push a new `v1.2.3-rc3` pointing to the
  rc1 commit) to move `:rc` back

### Production (persistent, CD daemon, `:v1.2.3`)

- **Where:** persistent host, separate from staging
- **Purpose:** stable working environment for real users. Receives only human-approved,
  UAT-signed-off release builds
- **CD config:** `CD_IMAGE_TAG=v1.2.3` (pinned, updated per release)
- **Promotion trigger:** two manual steps by a human operator
  1. Push release tag on master: `git tag v1.2.3 && git push --tags`
  2. Update `CD_IMAGE_TAG` in production `.env` to `v1.2.3` and restart the daemon
- **Image guarantee:** `promote-release.yml` retags `:rc` → `:v1.2.3` without rebuilding.
  The production image is bit-identical to the image that ran on staging during UAT
- **Invariant:** `CD_IMAGE_TAG` must always be a semver string — never `:rc`, never
  `:latest`, never a raw SHA

---

## The CD daemon — design and trade-offs

`src/cd/` is a Python process running on the deployment host. Every N seconds it:

1. Pulls `<image>:<tag>` from GHCR and reads the repo-digest
2. Compares to the last deployed digest in a JSON state file
3. If changed: runs `docker compose up --force-recreate` with the new image
4. Waits `health_check_delay_seconds`, then checks the container is still running
5. If unhealthy and `CD_ROLLBACK_ON_FAILURE=true`: re-deploys the previous digest
6. Sends deploy/rollback/failure notifications to a Slack or Discord webhook

The daemon runs on **staging and production only**. The test bed is agent-managed with no
daemon.

### Why pull-based (no inbound connections needed)

The deployment host makes only outbound HTTPS requests to GHCR. No inbound SSH port and
no GitHub runner IP allowlist are required. Works behind NAT or strict firewalls.

### CD daemon config per environment

| Setting | Staging | Production |
|---|---|---|
| `CD_IMAGE_TAG` | `rc` | `v1.2.3` |
| `CD_POLL_INTERVAL_SECONDS` | `300` | `600` |
| `CD_ROLLBACK_ON_FAILURE` | `true` | `true` |
| `CD_HEALTH_CHECK_DELAY_SECONDS` | `30` | `60` |
| Notification channel | `#staging-deploys` | `#prod-alerts` |

### Limitations and mitigations

| Limitation | Mitigation |
|---|---|
| Polling lag (≤5 min staging, ≤10 min production) | Acceptable — both envs have explicit promotion gates |
| Silent daemon failure stops deployments | `restart: unless-stopped` on daemon compose service |
| No deploy history in GitHub UI | Slack/Discord webhook notifications are the audit trail |

---

## Image tagging strategy

| Tag | When pushed | Mutable | Tracked by |
|---|---|---|---|
| `:sha-<7-char-hash>` | Every `build-on-demand.yml` run | No | Agent (explicit deploy to test bed) |
| `:v1.2.3-rc1` | RC tag push on any branch | No | Audit trail only |
| `:rc` | RC tag push (always latest RC) | Yes | Staging CD daemon |
| `:v1.2.3` | Release `promote-release.yml` (retag, no rebuild) | No | Production CD daemon |

**No `:latest` tag.** Every tag has unambiguous semantics and a clear owner.

**Bit-identical guarantee:** `:v1.2.3` in GHCR is always the same image digest as the
`:rc` image that was deployed to staging and UAT-approved. `promote-release.yml` pulls `:rc`
by digest and pushes that digest under the release tag — it does not invoke `docker build`.

---

## Linear history enforcement on master

The goal: code on `master` is always code that has been UAT-approved. No unreviewed merge
commits.

**GitHub repository settings (Settings → General → Pull Requests):**
- Disable **Allow merge commits**
- Disable **Allow squash merging**
- Enable **Allow rebase merging** only

**Branch protection on `master` (Settings → Branches):**
- Require status checks: `pytest`, `Docker build check`
- **Require branches to be up to date before merging**
- Require linear history
- Do not allow bypassing (including admins)

With rebase-only merging and "up to date" enforcement, the feature branch must include all
of master's commits before it can merge. The PR commits land on master as a linear sequence
with no merge commit.

**Note on commit SHAs:** GitHub's rebase merge replays commits, producing new SHAs even
when code is identical. The image guarantee is not provided by commit SHA matching — it is
provided by the `:rc` → `:v1.2.3` retag in `promote-release.yml`, which operates on image
digests, not commit SHAs.

---

## GHA workflows summary

| Workflow | Trigger | Builds? | Pushes tags |
|---|---|---|---|
| `ci-pr.yml` | PR commit to `master` | Yes (no push) | None |
| `build-on-demand.yml` | `workflow_dispatch` (agent/human) | Yes | `:sha-<hash>` |
| `build-rc.yml` | `v*-rc*` tag push (any branch) | Yes | `:v1.2.3-rc1`, `:rc` |
| `promote-release.yml` | `v*` non-rc tag push on `master` | No (retag only) | `:v1.2.3` |

---

## Should we add Jenkins?

No. See ADR 0005. GitHub Actions covers all CI/CD needs. The master orchestrator's `gh`
CLI is the bridge between the LLM agent and GHA workflows — no separate CI server is
required.

---

## Rollback procedures

**Test bed:** agent redeploys a previous `:sha-<hash>` explicitly.

**Staging:** push a new RC tag pointing to the last known-good commit
(e.g., `v1.2.3-rc3` pointing to the rc1 commit). GHA rebuilds and updates `:rc`; daemon
redeploys. Daemon's built-in rollback also handles transient deploy failures automatically.

**Production:** push a new patch release tag pointing to the previous known-good commit
(e.g., `v1.2.4` → same commit as `v1.2.2`). `promote-release.yml` retags `:rc`
(which should be the last RC for that commit) → `:v1.2.4`. Operator updates
`CD_IMAGE_TAG=v1.2.4` and restarts the daemon. Daemon's built-in rollback handles
transient failures automatically.
