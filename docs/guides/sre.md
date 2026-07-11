# SRE Workflow & Container Operations

This document describes how to use the containerized dev/test/staging infrastructure for **codex-slack**. Other agents and humans delegate all infra tasks to the SRE subagent — do not run container or deploy commands directly.

## Required Environment Variables

All variables are set in `.env` at the repo root. The justfile loads them automatically. Variables already exported in the shell override `.env` values.

| Variable | Required for | Example |
|---|---|---|
| `DEV_DOCKER_HOST` | Dev env spin-up, testing, logs, shell | `ssh://ubuntu@10.10.10.238` |
| `STAGING_DOCKER_HOST` | Staging deploys, undeploys, post-merge cleanup | `ssh://ubuntu@staging.tail-scale.ts.net` |
| `REGISTRY` | Staging/prod image pulls | `ghcr.io/pandazxx` |
| `REGISTRY_TOKEN` | Pushing images to non-GHCR registries | (from secret manager) |

**No fallback to local Docker.** `DEV_DOCKER_HOST` must always be set. For local Docker, set `DEV_DOCKER_HOST=unix:///var/run/docker.sock` explicitly.

## Supported Operations

### Dev Environment Spin-Up

**Invoke:** Ask SRE to "spin up a dev env for branch `feat-auth`" or "I need a dev env for the current branch".

The SRE agent will:

1. Check if an environment already exists (idempotent).
2. Build the dev stage image from the current commit on `DEV_DOCKER_HOST`.
3. Start the branch stack with Traefik routing.
4. Wait for health checks to pass.
5. Return the Traefik hostname and direct-access commands.

**Accessing the dev env:**

```bash
# Web UI (Traefik hostname):
curl http://master.feat-auth.<host-ip-dashed>.nip.io

# Master logs:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p feat-auth logs -f master

# Shell into master:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p feat-auth exec master bash

# MQTT messages:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p feat-auth exec mosquitto mosquitto_sub -h localhost -t '#' -v
```

Dev builds from source at `DEV_DOCKER_HOST` — no source bind-mounts. Source changes require a rebuild (`just dev-up [branch]` handles this idempotently).

### Tear Down Dev Environment

**Invoke:** Ask SRE to "tear down the dev env for `feat-auth`" or "tear down the current branch".

### Fast Test Loop (Unit & In-Process)

**Invoke:** Ask SRE to "run tests" or "run tests matching `test_image_contract`".

The SRE agent will:

1. Build the test image (`test` stage of `Dockerfile`).
2. Run pytest in an ephemeral container on `DEV_DOCKER_HOST`.
3. Return pass/fail and logs.

### Staging Deploy

**Invoke:** Ask SRE to "deploy `v1.2.3` to staging" or "refresh canonical staging".

The SRE agent runs `just deploy staging <tag>`, which:
1. Resolves the tag to an immutable digest via `docker buildx imagetools inspect`.
2. Pulls the image by digest to `STAGING_DOCKER_HOST`.
3. Brings up the singleton stack (compose project `codex-slack`) in place, replacing the running container.
4. Polls `http://<host>:${MASTER_PORT:-8080}/health` until healthy.

Staging is a singleton — one stack per host, fixed project name `codex-slack`, no Traefik. RC UAT serializes on the singleton: only one tag occupies staging at a time.

**Rollback:** Ask SRE to "deploy `<previous-tag>` to staging". There is no automatic rollback.

### Post-Merge Cleanup

**Invoke:** Ask SRE to "post-merge cleanup for `feat-auth`".

Runs `just post-merge-cleanup feat-auth`, which:
1. Refreshes the staging singleton with the `master` tag (or a specified tag).
2. Tears down the dev env for the merged branch if one exists.

## File Ownership Matrix

| File(s) | Owner | Notes |
|---|---|---|
| `Dockerfile` | Engineer | Prod image; SRE proposes safe additions via `# SRE-ADVISORY:` comment only. |
| `justfile` | SRE | All ops recipes; SRE owns. |
| `docker-compose.yml` | Shared | Neutral base; SRE manages via decision records if changing. |
| `docker-compose.dev.yml` | SRE | Dev overlay; build target, Traefik labels. |
| `docker-compose.deploy.yml` | SRE | Singleton overlay for staging/prod. |
| `docker-compose.ci.yml` | SRE | CI test config. |
| `.sre/`, `.env.example` | SRE | SRE scripts and env template. |
| `.github/workflows/` | SRE | CI/CD pipeline; propose changes via PR. |
| `docs/sre*.md`, `docs/guides/sre.md` | SRE | SRE documentation. |

## Secrets & Credentials

**Never commit secrets.** Set them in `.env` at the repo root (gitignored). The justfile loads them automatically.

**CI secrets:** GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, etc.) are passed as env vars at build/test time — never committed.

## Troubleshooting

### "Docker daemon not responding"

Check `DEV_DOCKER_HOST` is reachable:

```bash
DOCKER_HOST="$DEV_DOCKER_HOST" docker ps
```

Ensure SSH keys are loaded (`ssh-add`).

### "Health check timeout"

Logs:

```bash
DOCKER_HOST="$DEV_DOCKER_HOST" docker compose -p $BRANCH_SLUG logs master
```

Common causes:
- Missing API keys.
- Port conflict (for singleton shape, `MASTER_PORT` conflicts).
- Network connectivity (mosquitto unreachable).

## Related Documentation

- **Authoritative SRE reference:** `docs/sre.md` — env vars, shapes, compose files, runbooks, CI/CD.
- **Branch protection:** `docs/repo-harness.md` — merge rules, CI requirements, justfile recipe map.
- **ADR-0016:** `docs/decisions/0016-singleton-justfile-deploys.md` — singleton deploy model and CD-daemon retirement.
