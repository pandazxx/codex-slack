# SRE Reference

This document is the authoritative reference for infrastructure operations on this project. Agents and operators read this file; do not summarize it elsewhere.

## Required Environment Variables

All variables live in a single `.env` file at the repo root. `justfile` loads it automatically via `set dotenv-load`. Variables already exported in the caller's shell override values in `.env`. See `.env.example` for the full annotated list.

| Variable | Required for | Example | Status |
|---|---|---|---|
| `DEV_DOCKER_HOST` | All dev env operations | `ssh://ubuntu@10.10.10.238` | Set |
| `DOCKER_GID` | Docker socket group on host (probed automatically if unset) | `988` | Set |
| `STAGING_DOCKER_HOST` | Staging deploys, undeploys, post-merge cleanup | `ssh://ubuntu@<staging-ip>` | **Requires human to set** |
| `REGISTRY` | Image namespace for staging/prod pulls | `ghcr.io/pandazxx` | **Requires human to set** |
| `REGISTRY_TOKEN` | Pushing images to non-GHCR registries | (from secret manager) | Optional — GHCR uses `GITHUB_TOKEN` |
| `MASTER_PORT` | Host port for singleton `just deploy` targets | `8080` | Optional (default `8080`) |
| `PROD_DOCKER_HOST` | Prod deploys (reserved — no prod host provisioned yet) | `ssh://ubuntu@prod.example.com` | Optional |

No fallback to local Docker. `DEV_DOCKER_HOST` must always be set. For local Docker, set `DEV_DOCKER_HOST=unix:///var/run/docker.sock` explicitly.

### SSH requirements

SSH key must be loaded in `SSH_AUTH_SOCK` for remote `DOCKER_HOST=ssh://...` connections to work. Verify with `ssh -T ubuntu@<host>` before running any operations.

### DOCKER_GID

The `docker` group GID on the remote host is `988` (confirmed). This is passed via `DOCKER_GID` and used in `docker-compose.yml` `group_add:` for the master service so it can reach `/run/container.sock`. If `DOCKER_GID` is unset, recipes probe it automatically by running `alpine stat -c '%g' /var/run/docker.sock` against the target host.

## Services

| Service | Role | Routing | Access |
|---|---|---|---|
| `master` | FastAPI HTTP service (port 8080) | Dev: Traefik HTTP on `sre-traefik-public`. Staging/prod: direct host port `${MASTER_PORT:-8080}`. | Dev: `http://master.<branch-slug>.<host-ip-dashed>.nip.io`. Staging/prod: `http://<host>:8080` |
| `mosquitto` | MQTT broker (port 1883) | Internal only | `docker compose exec mosquitto mosquitto_sub -t '#'` |

## URL Pattern (dev shape)

`<service>.<branch-slug>.<host-ip-dashed>.nip.io`

- `branch-slug`: branch name lowercased, `/`, `_`, and `.` replaced with `-`
- `host-ip-dashed`: host IPv4 with dots replaced by dashes

Example: branch `feat/new-auth` on host `10.10.10.238` → `master.feat-new-auth.10-10-10-238.nip.io`

## Shared Host Infrastructure

One Traefik instance runs per Docker host as project `sre-host-infra`. It owns ports 80 and 443 and watches the `sre-traefik-public` network. Traefik is used only by the dev shape.

- Config: `.sre/host-infra/docker-compose.yml` and `.sre/host-infra/traefik.yml`
- Bootstrap command (idempotent):
  ```bash
  DOCKER_HOST=$DEV_DOCKER_HOST docker network create sre-traefik-public 2>/dev/null || true
  DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p sre-host-infra \
    -f .sre/host-infra/docker-compose.yml up -d
  ```
- Verify: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p sre-host-infra ps`

**Operators must not modify or restart `sre-host-infra`.** Changes to shared Traefik are `senior-sre` work only.

## Environment Shapes

### Dev shape

- Multi-tenant: multiple branches run concurrently on `DEV_DOCKER_HOST`, one stack per branch.
- Compose project name: branch slug (e.g. `feat-auth`).
- Image: built from `Dockerfile` target `dev` at deploy time. No source bind-mounts.
- Ingress: Traefik on `sre-traefik-public`, routed by `master.<slug>.<ip-dashed>.nip.io`.
- No published host ports.
- Dev cycle: edit source → `just dev-up [branch]` (rebuilds and restarts in place).

### Staging / prod shape (singleton)

- One long-lived stack per host, fixed compose project name `codex-slack`.
- Image: frozen CI-built tag, resolved to a digest before deploy. No builds on the staging/prod host.
- No Traefik. Master published on `${MASTER_PORT:-8080}:8080`.
- Rollback: `just deploy <env> <previous-tag>`.
- RC UAT serializes on the staging singleton: only one `v*-rc*` tag occupies staging at a time.

## Compose File Reference

| File | Used for |
|---|---|
| `docker-compose.yml` | Base definition — shared intersection (no `build:`, no `ports:`, no digest pin) |
| `docker-compose.dev.yml` | Dev overlay — adds `build:` (target: `dev`), Traefik labels, `sre-traefik-public` network |
| `docker-compose.deploy.yml` | Singleton overlay — used by `just deploy` / `just undeploy`; publishes `${MASTER_PORT:-8080}:8080`, pins image by digest, no Traefik |
| `docker-compose.ci.yml` | CI — builds `test` stage, no Traefik labels |

The justfile always passes explicit `-f` pairs. No overlay is ever auto-merged.

## Recipe Reference

All operations go through `just`. Shell variables already exported override `.env` values.

| Recipe | Operation | Key args |
|---|---|---|
| `just dev-up [branch]` | Build and start dev env for a branch | `branch` (defaults to current git branch) |
| `just dev-down [branch]` | Tear down dev env for a branch | `branch` |
| `just deploy <env> <tag>` | Resolve tag to digest and deploy singleton stack | `env`: `staging` or `prod`; `tag`: CI image tag |
| `just undeploy <env>` | Tear down singleton stack | `env`: `staging` or `prod` |
| `just status` | List active compose projects on all configured hosts | — |
| `just logs <env> <service> [key]` | Stream logs | `key` = branch slug for dev; omit for singleton envs |
| `just shell <env> <service> [key]` | Open interactive shell | same `key` semantics as `logs` |
| `just test [pattern]` | Build test stage and run pytest on `DEV_DOCKER_HOST` | `pattern` (optional pytest `-k` filter) |
| `just post-merge-cleanup <branch> [tag]` | Refresh staging singleton + tear down dev env for branch | `tag` defaults to `master` |

## Operator Runbooks

All runbooks are in `.sre/operations/`. Operators read the runbook for the requested operation and follow it exactly.

| Runbook | Operation |
|---|---|
| `env-up.md` | Spin up dev env for a branch |
| `env-down.md` | Tear down dev env for a branch |
| `deploy.md` | Deploy (or upgrade) the singleton stack to staging or prod |
| `undeploy.md` | Tear down the singleton stack |
| `logs.md` | Tail logs for a service |
| `shell.md` | Open a shell in a service |
| `status.md` | List active envs |
| `test.md` | Run tests |
| `post-merge-cleanup.md` | Refresh staging singleton + tear down merged-branch dev env |

## CI/CD

- `ci.yml`: runs on every PR and push to master. Builds `test` stage, runs `pytest`.
- `build-push.yml`: runs on push to master and version tags. Builds `prod` stage, pushes to `ghcr.io/<repo-owner>/codex-slack-master`. Jobs: `build-master`, `build-agent-minimal`, `promote`.

Staging and prod receive deploys via `just deploy <env> <tag>` — no CD daemon. Post-merge staging refresh is triggered by the `post-merge-cleanup` runbook (`just deploy staging master`). Branch protection: CI must pass before merge. Code owner review required for SRE-domain files.

## Items Requiring Human Action Before Production

1. Set `STAGING_DOCKER_HOST` in `.env` at repo root.
2. Set `REGISTRY` in `.env` (e.g. `ghcr.io/pandazxx`). CI hardcodes `ghcr.io/<repo-owner>` — only local `just deploy` calls need this.
3. Set `REGISTRY_TOKEN` as a GitHub Actions secret only if `REGISTRY` is not GHCR. For GHCR, `build-push.yml` uses the workflow's auto-provided `GITHUB_TOKEN`.
4. Bootstrap `sre-host-infra` on `STAGING_DOCKER_HOST` (same command as dev bootstrap above, substituting `STAGING_DOCKER_HOST`).
5. Verify `Dockerfile` has `prod`, `dev`, and `test` stages — all three are present; confirm they have adequate tooling for your workload.
6. Verify Traefik digest in `.sre/host-infra/docker-compose.yml` before first bootstrap.
7. Update `CODEOWNERS` with the correct GitHub usernames for SRE reviewers.
