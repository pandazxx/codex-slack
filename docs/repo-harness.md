# Repository Harness

This document describes how the CI/CD harness is wired together, for onboarding new contributors and for `senior-sre` reference when modifying workflows.

## Compose File Layering

```
docker-compose.yml              (neutral base — shared service definitions, no build/ports/digest)
  + docker-compose.dev.yml      (dev overlay — explicit -f; build target dev, Traefik labels)
  + docker-compose.deploy.yml   (singleton overlay — explicit -f; host port, digest pin; used by just deploy)
  + docker-compose.ci.yml       (CI — explicit -f; builds test stage)
```

All overlays are applied with explicit `-f` flags by justfile recipes. No overlay is auto-merged. `docker-compose.dev.yml` was formerly named `docker-compose.override.yml`; the rename makes Compose's auto-merge opt-in impossible by default.

## Dockerfile Stage Map

| Stage | Target | Used in |
|---|---|---|
| `prod` | Production image | `build-push.yml`, `docker-compose.deploy.yml` |
| `dev` | Extends prod with debug tooling | `docker-compose.dev.yml` |
| `test` | Extends prod with test deps | `docker-compose.ci.yml`, `just test` |

## GitHub Actions Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | PR to master, push to master | Build test image, run pytest |
| `build-push.yml` | Push to master or version tag | Build prod image, push to registry (jobs: `build-master`, `build-agent-minimal`, `promote`) |

## Branch Protection

Rules defined in `.github/rulesets/master-protection.json`:
- Direct push to `master` blocked.
- PR required with 1 approving review.
- Code owner review required for SRE-domain files (per `.github/CODEOWNERS`).
- `Build and test` CI check must pass.

## Justfile Recipes

All operations are invoked through the `justfile` at the repo root. The justfile loads `.env` via `set dotenv-load`. Shell env vars override `.env` values.

| Recipe | Purpose |
|---|---|
| `just dev-up [branch]` | Build and start dev env for a branch on `DEV_DOCKER_HOST` |
| `just dev-down [branch]` | Tear down dev env |
| `just deploy <env> <tag>` | Resolve tag to digest and deploy singleton stack on `<env>_DOCKER_HOST` |
| `just undeploy <env>` | Tear down singleton stack |
| `just status` | List active compose projects on all configured hosts |
| `just logs <env> <service> [key]` | Stream logs |
| `just shell <env> <service> [key]` | Open interactive shell |
| `just test [pattern]` | Run pytest via test stage on `DEV_DOCKER_HOST` |
| `just post-merge-cleanup <branch> [tag]` | Refresh staging singleton + tear down dev env for branch |

The `.sre/*.sh` scripts for dev-shape recipes are one-release-cycle wrappers that `exec just <recipe> "$@"`.
