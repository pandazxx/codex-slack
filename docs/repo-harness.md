# Repository Harness

This document describes how the CI/CD harness is wired together, for onboarding new contributors and for `senior-sre` reference when modifying workflows.

## Compose File Layering

```
docker-compose.yml              (base — engineer-owned)
  + docker-compose.override.yml (dev — SRE-owned, auto-merged)
  + docker-compose.ci.yml       (CI — SRE-owned, explicit -f flag)
  + docker-compose.staging.yml  (staging — SRE-owned, explicit -f flag)
```

Dev uses auto-merge (Compose loads `override.yml` automatically). CI and staging always specify `-f` explicitly to avoid accidental override merging.

## Dockerfile Stage Map

| Stage | Target | Used in |
|---|---|---|
| `prod` | Production image | `build-push.yml`, staging envs |
| `dev` | Extends prod with debug tooling | `docker-compose.override.yml` |
| `test` | Extends prod with test deps | `docker-compose.ci.yml`, `run-tests.sh` |

Note: the current `Dockerfile` has no explicit stage names. An `SRE-ADVISORY` comment has been added requesting the engineer add `dev` and `test` stages. Until then, all compose targets fall back to the single unnamed stage.

## GitHub Actions Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | PR to master, push to master | Build test image, run pytest |
| `build-push.yml` | Push to master or version tag | Build prod image, push to registry |

## Branch Protection

Rules defined in `.github/rulesets/master-protection.json`:
- Direct push to `master` blocked.
- PR required with 1 approving review.
- Code owner review required for SRE-domain files (per `.github/CODEOWNERS`).
- `Build and test` CI check must pass.

## Scripts

| Script | Purpose |
|---|---|
| `.sre/env-up.sh <branch>` | Build dev image and bring up env on DEV_DOCKER_HOST |
| `.sre/env-down.sh <branch>` | Tear down dev env |
| `.sre/staging-up.sh <branch> <image> <digest>` | Deploy versioned image to staging |
| `.sre/staging-down.sh <branch>` | Tear down staging env |
| `.sre/env-status.sh` | List active envs on both hosts |
| `.sre/run-tests.sh [<pattern>]` | Run pytest in ephemeral test container |

All scripts read environment variables from the shell; they do not source `.env` files.
