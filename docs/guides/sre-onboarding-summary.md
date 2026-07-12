# SRE Onboarding Summary

**Date:** 2025-05-06  
**Project:** codex-slack  
**Status:** ✅ Containerized dev/test workflow established

## Overview

The project has been onboarded to a containerized SRE workflow. All infrastructure tasks are now delegated to the SRE subagent (or run via `.sre/` scripts). Developers no longer run `docker`/`docker compose` commands directly — instead, they ask SRE or use the scripts.

## What Was Set Up

### 1. Dev Environment

**Files:**

- `docker-compose.dev.yml` — Dev overlay: build target `dev`, Traefik labels, `sre-traefik-public` network.
- `justfile` — All ops recipes including `dev-up`, `dev-down`, `deploy`, `undeploy`, `test`, etc.
- `.sre/env-up.sh` — Thin wrapper: `exec just dev-up "$@"` (one-release-cycle deprecation window).
- `.sre/env-down.sh` — Thin wrapper: `exec just dev-down "$@"`.

**Usage:**

```bash
# Ask SRE to spin up
# "Spin up a dev env for feat-auth"

# Or manually:
.sre/env-up.sh [BRANCH_SLUG]
```

**Features:**

- Built from current commit at `DEV_DOCKER_HOST` (no source bind-mounts — source changes require `just dev-up`).
- Isolated from other branches (separate Compose projects, one per branch slug).
- Traefik routing via `master.<branch-slug>.<host-ip-dashed>.nip.io`.
- Healthchecks on all services.

**Access:**

```bash
# Web UI (Traefik hostname):
curl http://master.feat-auth.<host-ip-dashed>.nip.io

# Logs:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p feat-auth logs -f master

# Shell:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p feat-auth exec master bash
```

### 2. Test Environment (CI & Local Testing)

**Files:**

- `docker-compose.ci.yml` — Test config for CI.
- `Dockerfile.test` — Test image with pytest.
- `.sre/test.sh` — Run tests locally.

**Usage:**

```bash
# Run all tests locally:
.sre/test.sh

# Run specific test:
.sre/test.sh tests/test_version.py

# Match pattern:
.sre/test.sh -k test_image

# Verbose:
.sre/test.sh -vv
```

**Features:**

- Containerized pytest for parity with CI.
- Same image used locally and in GitHub Actions.
- Prevents "works locally, fails in CI" surprises.
- All dependencies pre-installed.

### 3. CI/CD Updates

**Files modified:**

- `.github/workflows/ci-pr.yml` — Tests now run in `Dockerfile.test` container.
- `Dockerfile` — Added HEALTHCHECK for container orchestration.

**Changes:**

- CI builds `Dockerfile.test` and runs tests in container (parity with dev).
- CI caches images via GitHub Actions cache.
- Test output is logged to GitHub Actions UI.

### 4. Justfile & SRE Scripts

`justfile` at repo root is the single entry point for all ops. Scripts in `.sre/` are one-release-cycle wrappers that `exec just <recipe> "$@"`.

| Entry point | Purpose |
|---|---|
| `just dev-up [branch]` | Spin up dev env (idempotent). |
| `just dev-down [branch]` | Tear down dev env. |
| `just deploy <env> <tag>` | Deploy singleton stack to staging or prod. |
| `just undeploy <env>` | Tear down singleton stack. |
| `just test [pattern]` | Run tests. |
| `.sre/setup-repo-protection.sh` | Apply branch protection rules to `main` (not wrapped — one-time admin task). |

**Permissions:** All scripts are executable.

### 5. Documentation

**SRE-facing:**

- `docs/sre.md` — Authoritative SRE reference. Env vars, shapes, compose files, recipe surface, runbooks.
- `docs/guides/sre.md` — SRE workflow guide. Covers dev env, staging deploy, secrets, troubleshooting.
- `docs/repo-harness.md` — Compose layering, justfile recipes, branch protection, CI workflows.
- `docs/decisions/0016-singleton-justfile-deploys.md` — singleton deploy model and CD-daemon retirement rationale.

**Developer-facing:**

- Updated `.claude/CLAUDE.md` with "SRE Workflow" section at the top. Tells developers to delegate infra tasks to SRE.

### 6. Project Layout

```
justfile                          # All ops recipes (dev-up, deploy, undeploy, test, …)

.sre/
├── env-up.sh                    # Wrapper: exec just dev-up "$@"
├── env-down.sh                  # Wrapper: exec just dev-down "$@"
├── test.sh                      # Wrapper: exec just test "$@"
└── setup-repo-protection.sh     # Configure branch protection (admin, not wrapped)

Dockerfile                        # Multi-stage: prod, dev, test

docker-compose*
├── docker-compose.yml           # Neutral base (no build/ports/digest)
├── docker-compose.dev.yml       # Dev overlay (renamed from override.yml)
├── docker-compose.deploy.yml    # Singleton overlay for staging/prod
└── docker-compose.ci.yml        # CI test config

docs/guides/
├── sre.md                       # SRE workflow guide
├── sre-onboarding-summary.md    # This file
└── onboarding.md                # Contributor onboarding (includes just install)

docs/sre.md                      # Authoritative SRE reference (env vars, shapes, runbooks)
docs/repo-harness.md             # Compose layering, justfile recipes, branch protection
docs/decisions/
└── 0016-singleton-justfile-deploys.md  # ADR for this change

.github/
├── workflows/
│   └── ci-pr.yml               # Updated: tests in container
└── pull_request_template.md    # Template for PRs

.claude/
└── CLAUDE.md                    # Updated: SRE Workflow section added
```

## Required Environment Variables

Before running SRE operations, verify these are set in your shell:

All variables are set in `.env` at the repo root; the justfile loads them automatically.

| Variable | Required for | Example |
|---|---|---|
| `DEV_DOCKER_HOST` | Dev env spin-up, tests, logs, shell | `ssh://ubuntu@dev.tail-scale.ts.net` |
| `STAGING_DOCKER_HOST` | Staging deploys, undeploys | `ssh://ubuntu@staging.tail-scale.ts.net` |
| `REGISTRY` | Staging/prod image pulls | `ghcr.io/pandazxx` |
| `REGISTRY_TOKEN` | Pushing images to non-GHCR registries | (from secret manager) |

**No local Docker fallback.** `DEV_DOCKER_HOST` must always be set. For local Docker, set `DEV_DOCKER_HOST=unix:///var/run/docker.sock` explicitly.

## Supported Operations

### Developers can now ask SRE to:

1. **"Spin up a dev env for branch `feat-auth`"**
   - SRE creates an isolated stack with bind-mounted source.
   - Returns HTTP endpoints, direct-access commands.

2. **"Run the tests"** or **"Run tests matching `test_image`"**
   - SRE builds and runs pytest in a container.
   - Returns pass/fail and logs.

3. **"Tear down the dev env for `feat-auth`"**
   - SRE removes containers and volumes.

4. **"Deploy `v1.2.3` to staging"**
   - SRE runs `just deploy staging v1.2.3`, resolves the tag to a digest, and deploys the singleton stack on `STAGING_DOCKER_HOST`. Rollback = `just deploy staging <previous-tag>`.

5. **"What's running on staging"**
   - SRE runs `just status` to list active compose projects on all configured hosts.

### Or run manually via justfile:

```bash
just dev-up feat-auth           # Spin up dev env
just dev-down feat-auth         # Tear down
just test                        # Run tests
just test test_image             # Run specific tests
just deploy staging v1.2.3      # Deploy to staging
```

## File Ownership

| File(s) | Owner | Notes |
|---|---|---|
| `Dockerfile` | Engineer | Prod image; SRE edits only safe additions (healthchecks, non-root user). |
| `Dockerfile.dev`, `Dockerfile.test` | SRE | Dev/test images; SRE owns outright. |
| `docker-compose.yml` | Shared | Base topology; engineer-proposed changes require SRE review. |
| `docker-compose.dev.yml`, `docker-compose.deploy.yml`, `docker-compose.ci.yml` | SRE | Dev/deploy/CI compose overlays; SRE owns. |
| `.sre/` | SRE | SRE implementation scripts. |
| `.github/workflows/` | SRE | CI/CD pipeline. |
| `docs/sre*.md`, `docs/guides/sre.md`, `docs/guides/repo-harness.md` | SRE | SRE documentation. |
| Application source, tests, migrations | Engineer | Off-limits to SRE unless domain-coupled issue. |

## Summary of Changes (2026-07-11 update)

- `docker-compose.override.yml` renamed to `docker-compose.dev.yml` — no more implicit auto-merge.
- `docker-compose.staging.yml` and `docker-compose.cd-daemon.example.yml` removed. `docker-compose.deploy.yml` is the new singleton overlay for staging and prod.
- CD daemon (`src/cd/`, `Dockerfile.cd-daemon`) retired. `just deploy <env> <tag>` is the only staging/prod deploy path.
- `justfile` added at repo root with all ops recipes.
- `.env.example` reorganised into two sections (Section A: justfile/deploy config; Section B: master runtime secrets).

## Next Steps (For Team)

1. **Verify the setup:**
   - Copy `.env.example` to `.env` and fill in `DEV_DOCKER_HOST`, `STAGING_DOCKER_HOST`, and `REGISTRY`.
   - Run `just test` to verify tests run.
   - Run `just dev-up` to spin up a dev env.

2. **Update team docs/onboarding:**
   - Point new developers to `.claude/CLAUDE.md` (SRE Workflow section).
   - Link to `docs/guides/sre.md` and `docs/guides/repo-harness.md`.

3. **Apply branch protection (one-time):**
   - Run `.sre/setup-repo-protection.sh` to enforce merge rules on `main`.
   - Requires `gh` CLI authenticated.

4. **Address "Address before going to prod" items:**
   - **Secrets in CI:** Review `.github/workflows/` to ensure no credentials are hardcoded. Use GitHub Actions secrets.
   - **Staging deploy:** Set `STAGING_DOCKER_HOST` and run `just deploy staging master` to verify end-to-end.
   - **Data backup:** Ensure production data volumes are regularly backed up (off-host).

5. **Iterate & improve:**
   - As the workflow settles, update decision records if tradeoffs change.
   - If new pain points emerge, raise them and SRE will optimize.

## Troubleshooting

### "Docker daemon not responding"

```bash
DOCKER_HOST="$DEV_DOCKER_HOST" docker ps
# If it fails, check DEV_DOCKER_HOST is reachable and SSH keys are loaded.
```

### "just: command not found"

Install `just`: macOS: `brew install just`; Linux: see `docs/guides/onboarding.md`.

### "Health check timeout on dev-up"

Check the logs:

```bash
DOCKER_HOST="$DEV_DOCKER_HOST" docker compose -p <branch-slug> logs master
```

Common causes: missing API keys, port conflict, network issue.

### "Test failures locally but not in CI"

This should be rare now (containerized parity). If it happens:

1. Check env vars (`.sre/test.sh` uses the same image as CI).
2. Check Docker version (build cache may differ).
3. Compare output between local and CI.

## Contacts & Escalation

- **SRE infrastructure questions:** Read `docs/guides/sre.md` or ask the SRE subagent.
- **Container build issues:** Check Dockerfile caching and GitHub Actions logs.
- **CI failures:** Check `.github/workflows/ci-pr.yml` and the CI run logs.
- **Design or architecture questions:** Refer to `docs/decisions/` or create a new ADR via the architect subagent.

## Related Documentation

- **Project instructions:** `.claude/CLAUDE.md` — agent workflows, git conventions, knowledge persistence.
- **SRE reference:** `docs/sre.md` — authoritative env vars, shapes, compose files, runbooks, CI/CD.
- **SRE workflow guide:** `docs/guides/sre.md` — how-to for supported operations.
- **Repository harness:** `docs/repo-harness.md` — branch protection, merge rules, justfile recipe map.
- **ADR-0016:** `docs/decisions/0016-singleton-justfile-deploys.md` — singleton deploy model, CD-daemon retirement.

---

**Onboarding complete. The project is ready for containerized development, testing, and staging workflows.**
