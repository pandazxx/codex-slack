# SRE Reference

This document is the authoritative reference for infrastructure operations on this project. Agents and operators read this file; do not summarize it elsewhere.

## Required Environment Variables

| Variable | Required for | Example | Status |
|---|---|---|---|
| `DEV_DOCKER_HOST` | All dev env operations | `ssh://ubuntu@10.10.10.238` | Set |
| `DOCKER_GID` | Docker socket group on host | `988` | Set |
| `STAGING_DOCKER_HOST` | Staging deploys, UAT, post-merge cleanup | `ssh://ubuntu@<staging-ip>` | **Requires human to set** |
| `REGISTRY` | Building and pushing images | `ghcr.io/pandazxx` | **Requires human to set** |
| `REGISTRY_TOKEN` | Pushing images (CI secret) | (from secret manager) | **Requires human to set** |

No fallback to local Docker. `DEV_DOCKER_HOST` must always be set. For local Docker, set `DEV_DOCKER_HOST=unix:///var/run/docker.sock` explicitly.

### SSH requirements

SSH key must be loaded in `SSH_AUTH_SOCK` for remote `DOCKER_HOST=ssh://...` connections to work. Verify with `ssh -T ubuntu@<host>` before running any operations.

### DOCKER_GID

The `docker` group GID on the remote host is `988` (confirmed). This is passed via `DOCKER_GID` and used in `docker-compose.yml` `group_add:` for the master service so it can reach `/run/container.sock`.

## Services

| Service | Role | Routing | Access |
|---|---|---|---|
| `master` | FastAPI HTTP service (port 8080) | Traefik HTTP on `sre-traefik-public` | `http://master.<branch-slug>.<host-ip-dashed>.nip.io` |
| `mosquitto` | MQTT broker (port 1883) | Internal only | `docker compose exec mosquitto mosquitto_sub -t '#'` |

## URL Pattern

`<service>.<branch-slug>.<host-ip-dashed>.nip.io`

- `branch-slug`: branch name lowercased, `/` and `_` replaced with `-`
- `host-ip-dashed`: host IPv4 with dots replaced by dashes

Example: branch `feat/new-auth` on host `10.10.10.238` → `master.feat-new-auth.10-10-10-238.nip.io`

## Shared Host Infrastructure

One Traefik instance runs per Docker host as project `sre-host-infra`. It owns ports 80 and 443 and watches the `sre-traefik-public` network.

- Config: `.sre/host-infra/docker-compose.yml` and `.sre/host-infra/traefik.yml`
- Bootstrap command (idempotent):
  ```bash
  DOCKER_HOST=$DEV_DOCKER_HOST docker network create sre-traefik-public 2>/dev/null || true
  DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p sre-host-infra \
    -f .sre/host-infra/docker-compose.yml up -d
  ```
- Verify: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p sre-host-infra ps`

**Operators must not modify or restart `sre-host-infra`.** Changes to shared Traefik are `senior-sre` work only.

## Dev Environment Shape

- Multi-tenant: multiple branches run concurrently on `DEV_DOCKER_HOST`.
- Image: built from `Dockerfile` target `dev` at build time. No source bind-mounts.
- Dev cycle: edit source → `docker compose build master` → `docker compose up -d master`
- Resource limits: master 1 GB / 1 CPU, mosquitto 128 MB / 0.25 CPU.
- No published ports. All access via Traefik (HTTP) or `docker compose exec` (MQTT).

## Staging Environment Shape

- Multi-tenant: multiple branches can run on `STAGING_DOCKER_HOST`.
- Image: pulled by digest from `$REGISTRY`. No builds on staging host.
- Canonical staging: `main`-tracking env, refreshed after every merge by `post-merge-cleanup`.
- Requires `STAGING_DOCKER_HOST` and `REGISTRY` — see table above.

## Compose File Reference

| File | Used for |
|---|---|
| `docker-compose.yml` | Base definition (off-hand, engineer-owned) |
| `docker-compose.override.yml` | Dev — auto-merged by Compose; builds `dev` stage |
| `docker-compose.ci.yml` | CI — builds `test` stage, no Traefik labels |
| `docker-compose.staging.yml` | Staging — pulls image by digest |

## Operator Runbooks

All runbooks are in `.sre/operations/`. Operators read the runbook for the requested operation and follow it exactly.

| Runbook | Operation |
|---|---|
| `env-up.md` | Spin up dev env for a branch |
| `env-down.md` | Tear down dev env for a branch |
| `staging-up.md` | Spin up staging env at a version |
| `staging-down.md` | Tear down staging env |
| `logs.md` | Tail logs for a service |
| `shell.md` | Open a shell in a service |
| `status.md` | List active envs |
| `test.md` | Run tests |
| `post-merge-cleanup.md` | Refresh main staging + tear down merged-branch staging |

## CI/CD

- `ci.yml`: runs on every PR and push to master. Builds `test` stage, runs `pytest`.
- `build-push.yml`: runs on push to master and version tags. Builds `prod` stage, pushes to `$REGISTRY`.
- Branch protection: CI must pass before merge. Code owner review required for SRE-domain files.

## Items Requiring Human Action Before Production

1. Set `STAGING_DOCKER_HOST` in dotenv/direnv.
2. Set `REGISTRY` in dotenv/direnv and as a GitHub Actions variable (`vars.REGISTRY`).
3. Set `REGISTRY_TOKEN` as a GitHub Actions secret (`secrets.REGISTRY_TOKEN`).
4. Bootstrap `sre-host-infra` on `STAGING_DOCKER_HOST` (same command as dev bootstrap above, with `STAGING_DOCKER_HOST`).
5. Add `dev` and `test` Dockerfile stages (see `SRE-ADVISORY` comment in `Dockerfile`).
6. Verify Traefik digest in `.sre/host-infra/docker-compose.yml` before first bootstrap.
7. Update `CODEOWNERS` with the correct GitHub usernames for SRE reviewers.
