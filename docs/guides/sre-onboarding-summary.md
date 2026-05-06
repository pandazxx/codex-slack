# SRE Onboarding Summary

**Date:** 2025-05-06  
**Project:** codex-slack  
**Status:** ✅ Containerized dev/test workflow established

## Overview

The project has been onboarded to a containerized SRE workflow. All infrastructure tasks are now delegated to the SRE subagent (or run via `.sre/` scripts). Developers no longer run `docker`/`docker compose` commands directly — instead, they ask SRE or use the scripts.

## What Was Set Up

### 1. Dev Environment (Local Development)

**Files:**

- `docker-compose.override.yml` — Dev overrides with bind-mounts.
- `Dockerfile.dev` — Dev image with live-reload optimizations.
- `.sre/env-up.sh` — Spin up isolated dev env (idempotent).
- `.sre/env-down.sh` — Tear down dev env.

**Usage:**

```bash
# Ask SRE to spin up
# "Spin up a dev env for feat-auth"

# Or manually:
.sre/env-up.sh [BRANCH_SLUG]
```

**Features:**

- Source code bind-mounted → changes reflect immediately (no rebuild).
- Python uvicorn reloads on file changes.
- Frontend build runs in container and watches for changes.
- Isolated from other branches (separate Compose projects).
- Healthchecks on all services.
- Exposed ports: 8080 (master API), etc.

**Access:**

```bash
# Web UI:
curl http://localhost:8080

# API docs:
curl http://localhost:8080/docs

# Logs:
docker compose -p $USER-feat-auth logs -f master

# Shell:
docker compose -p $USER-feat-auth exec -it master bash
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

### 4. SRE Scripts

All scripts in `.sre/` are called by the SRE subagent; humans use them for manual control:

| Script | Purpose |
|---|---|
| `env-up.sh` | Spin up dev env (idempotent). |
| `env-down.sh` | Tear down dev env. |
| `test.sh` | Run tests locally. |
| `setup-repo-protection.sh` | Apply branch protection rules to `main`. |

**Permissions:** All scripts are executable.

### 5. Documentation

**SRE-facing:**

- `docs/guides/sre.md` — Complete SRE workflow guide. Covers dev env, testing, staging, secret handling, troubleshooting.
- `docs/guides/repo-harness.md` — Branch protection rules, merge requirements, CODEOWNERS.
- `docs/guides/deploy-prod.md` — Production deployment runbook. How to use the artifact, verify, rollback.
- `docs/sre-decisions/2025-05-06-containerized-dev-workflow.md` — ADR explaining the decision, rationale, consequences.

**Developer-facing:**

- Updated `.claude/CLAUDE.md` with "SRE Workflow" section at the top. Tells developers to delegate infra tasks to SRE.

### 6. Project Layout

```
.sre/
├── env-up.sh                    # Spin up dev env
├── env-down.sh                  # Tear down dev env
├── test.sh                       # Run tests
└── setup-repo-protection.sh     # Configure branch protection

Dockerfile*
├── Dockerfile                    # Prod image (with HEALTHCHECK added)
├── Dockerfile.dev               # Dev image (live reload)
└── Dockerfile.test              # Test image (pytest)

docker-compose*
├── docker-compose.yml           # Base (unchanged)
├── docker-compose.override.yml  # Dev overrides (new)
└── docker-compose.ci.yml        # CI test config (new)

docs/guides/
├── sre.md                       # SRE workflow guide
├── repo-harness.md              # Branch protection
├── deploy-prod.md               # Prod deployment runbook
└── sre-onboarding-summary.md    # This file

docs/sre-decisions/
└── 2025-05-06-containerized-dev-workflow.md  # ADR

.github/
├── workflows/
│   └── ci-pr.yml               # Updated: tests in container
└── pull_request_template.md    # Template for PRs

.claude/
└── CLAUDE.md                    # Updated: SRE Workflow section added
```

## Required Environment Variables

Before running SRE operations, verify these are set in your shell:

| Variable | Required for | Example |
|---|---|---|
| `DEV_DOCKER_HOST` | Remote dev env (optional; uses local Docker if unset) | `ssh://ubuntu@dev.tail-scale.ts.net` |
| `STAGING_DOCKER_HOST` | Staging deploys | `ssh://ubuntu@staging.tail-scale.ts.net` |
| `REGISTRY` | Building/pushing images | `ghcr.io/myorg` |
| `REGISTRY_TOKEN` | Pushing images | (from secret manager) |

**Local development:** `DEV_DOCKER_HOST` is optional; the SRE agent uses the local Docker daemon if unset.

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
   - SRE deploys to `STAGING_DOCKER_HOST` by image digest.
   - Runs smoke tests; auto-rollback if failed.

5. **"What's running on staging"**
   - SRE lists canonical and feature-branch staging envs.

### Or run manually:

```bash
.sre/env-up.sh feat-auth        # Spin up dev env
.sre/env-down.sh feat-auth      # Tear down
.sre/test.sh                     # Run tests
.sre/test.sh -k test_image       # Run specific tests
```

## File Ownership

| File(s) | Owner | Notes |
|---|---|---|
| `Dockerfile` | Engineer | Prod image; SRE edits only safe additions (healthchecks, non-root user). |
| `Dockerfile.dev`, `Dockerfile.test` | SRE | Dev/test images; SRE owns outright. |
| `docker-compose.yml` | Shared | Base topology; engineer-proposed changes require SRE review. |
| `docker-compose.override.yml`, `docker-compose.ci.yml` | SRE | Dev/test compose configs; SRE owns. |
| `.sre/` | SRE | SRE implementation scripts. |
| `.github/workflows/` | SRE | CI/CD pipeline. |
| `docs/sre*.md`, `docs/guides/sre.md`, `docs/guides/repo-harness.md` | SRE | SRE documentation. |
| Application source, tests, migrations | Engineer | Off-limits to SRE unless domain-coupled issue. |

## No Breaking Changes

- Prod `Dockerfile` is nearly unchanged (only added HEALTHCHECK).
- Base `docker-compose.yml` is unchanged.
- No impact on existing deploys or CI (ci-pr.yml now uses containers, which is safer).
- Existing `docker-compose.yml`, example files (`.example.yml`) are untouched.

## Next Steps (For Team)

1. **Verify the setup:**
   - Clone or pull the branch with these changes.
   - Run `.sre/test.sh` to verify tests run.
   - Run `.sre/env-up.sh` to spin up a dev env (requires Docker).

2. **Update team docs/onboarding:**
   - Point new developers to `.claude/CLAUDE.md` (SRE Workflow section).
   - Link to `docs/guides/sre.md` and `docs/guides/repo-harness.md`.

3. **Apply branch protection (one-time):**
   - Run `.sre/setup-repo-protection.sh` to enforce merge rules on `main`.
   - Requires `gh` CLI authenticated.

4. **Address "Address before going to prod" items:**
   - **Secrets in CI:** Review `.github/workflows/` to ensure no credentials are hardcoded. Use GitHub Actions secrets and pass as env vars at runtime.
   - **Prod artifact & rollback:** When deploying to prod, implement the artifact pattern described in `docs/guides/deploy-prod.md` (build-prod-artifact.yml workflow with `deploy.sh`, `rollback.sh`, `verify.sh`).
   - **Staging UAT:** Establish a formal UAT process before prod releases. Use feature-branch staging envs for high-risk features.
   - **Data backup:** Ensure production data volumes are regularly backed up (off-host).

5. **Iterate & improve:**
   - As the workflow settles, update decision records if tradeoffs change.
   - If new pain points emerge (e.g., "tests are too slow"), raise them and SRE will optimize.

## Troubleshooting

### "Docker daemon not responding"

```bash
docker ps
# If it fails, start Docker or check DEV_DOCKER_HOST is reachable.
```

### "env-up.sh: command not found"

```bash
chmod +x .sre/env-up.sh
.sre/env-up.sh
```

### "Health check timeout on env-up"

Check the logs:

```bash
docker compose -p $USER-<branch> logs master
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
- **SRE workflow:** `docs/guides/sre.md` — full operational guide.
- **Repository harness:** `docs/guides/repo-harness.md` — branch protection, merge rules.
- **Production deployment:** `docs/guides/deploy-prod.md` — how to deploy to prod.
- **Decision record:** `docs/sre-decisions/2025-05-06-containerized-dev-workflow.md` — rationale and consequences.

---

**Onboarding complete. The project is ready for containerized development, testing, and staging workflows.**
