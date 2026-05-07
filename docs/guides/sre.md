# SRE Workflow & Container Operations

This document describes how to use the containerized dev/test/staging infrastructure for **codex-slack**. Other agents and humans delegate all infra tasks to the SRE subagent — do not run container or deploy commands directly.

## Required Environment Variables

Before any SRE operation, verify these variables are set in your shell environment (e.g., `~/.config/dev-env`, `.envrc`, or direnv):

| Variable | Required for | Example |
|---|---|---|
| `DEV_DOCKER_HOST` | Dev env spin-up, local testing | `ssh://ubuntu@docker-testbed.local` (optional; uses local Docker if unset) |
| `STAGING_DOCKER_HOST` | Staging deploys, UAT | `ssh://ubuntu@staging.tail-scale.ts.net` |
| `REGISTRY` | Building/pushing images | `ghcr.io/myorg` |
| `REGISTRY_TOKEN` | Pushing images | (from secret manager) |

**Remote vs. local development:**

- If `DEV_DOCKER_HOST` is set (e.g., `ssh://ubuntu@docker-testbed.local`), the SRE agent and `.sre/*.sh` scripts export `DOCKER_HOST=$DEV_DOCKER_HOST` before any `docker` or `docker compose` command. All containers run on the remote dev host.
- If `DEV_DOCKER_HOST` is unset, containers run on the local Docker daemon. This is fine for isolated feature development on a personal machine.

**Unit tests always run locally:** `.sre/test.sh` does NOT use `DEV_DOCKER_HOST`, because unit and in-process tests don't need remote infrastructure. Stack tests (integration, end-to-end) run against a dev environment spun up by SRE on the remote host.

## Supported Operations

### Dev Environment Spin-Up

**Invoke:** Ask SRE to "spin up a dev env for branch `feat-auth`" or "I need a dev env for the current branch".

The SRE agent will:

1. Check if an environment already exists (idempotent).
2. Build or pull images as needed.
3. Detect whether the shared Traefik ingress is up on the target Docker host.
4. Start the stack with `docker-compose.override.yml` (bind-mounted source code for live reload) when local; with `docker-compose.remote.yml` (no bind mounts, image-baked source) when `DEV_DOCKER_HOST` is set.
5. Add a `/etc/hosts` entry on the laptop for the branch hostname (Traefik mode only; requires `sudo` non-interactively or prints the line for the user to add manually).
6. Wait for health checks to pass.
7. Return access information (HTTP endpoints, direct-access commands).

**Accessing the dev env (Traefik mode — default):**

Each branch is reachable at a label-routed hostname:

```
http://master.<branch>.dev.docker-testbed.local/
http://master.<branch>.dev.docker-testbed.local/docs
http://master.<branch>.dev.docker-testbed.local/health
```

The Traefik dashboard is at `http://traefik.dev.docker-testbed.local/dashboard/`.

Multiple branches run in parallel without per-branch ports or per-env SSH tunnels. See `docs/sre-decisions/2026-05-07-traefik-multi-env-routing.md` for the design and rationale.

**One-time host setup** (once per dev Docker host, not per branch):

```bash
.sre/traefik-up.sh   # brings up the shared ingress on $DEV_DOCKER_HOST
```

Subsequent branch spin-ups just need `.sre/env-up.sh <branch>`.

**Fallback (per-branch port):**

If the Traefik ingress is not up on the target Docker host, `env-up.sh` falls back to publishing a deterministic per-branch port (hash of project name + 8080). The script prints the SSH-tunnel command. Use this only when Traefik is unavailable.

**Direct-access commands (both modes):**

```bash
# Master logs:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $USER-feat-auth logs -f master

# Shell into master:
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $USER-feat-auth exec -it master bash

# MQTT messages (mosquitto stays internal — no Traefik route, see decision record):
DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $USER-feat-auth exec mosquitto mosquitto_sub -h localhost -t '#' -v
```

**Source code changes are live (local Docker only):**

Edits to `src/`, `frontend/`, and `config/` are visible immediately when running on local Docker via `docker-compose.override.yml`. On a remote `DEV_DOCKER_HOST`, source is baked into the image at build time and `env-up.sh` rebuilds when invoked.

### Tear Down Dev Environment

**Invoke:** Ask SRE to "tear down the dev env for `feat-auth`" or "tear down the current branch".

The SRE agent will remove containers, volumes, and networks associated with the environment.

### Fast Test Loop (Unit & In-Process)

**Invoke:** Ask SRE to "run tests" or "run tests matching `test_image_contract`".

The SRE agent will:

1. Build the test image (`Dockerfile.test`).
2. Run pytest in a container against the test suite.
3. Return pass/fail and logs.

**Locally:**

```bash
# Run all tests:
.sre/test.sh

# Run a specific file:
.sre/test.sh tests/test_version.py

# Run tests matching a pattern:
.sre/test.sh -k test_image

# Verbose output:
.sre/test.sh -vv --tb=long
```

### Staging Environment (Feature-Branch & Canonical)

**Invoke:** Ask SRE to "spin up staging for `feat-billing`" or "refresh canonical staging from main".

The SRE agent will deploy to `STAGING_DOCKER_HOST` using digests (not tags) and run smoke tests. Two modes:

- **Canonical staging** (`main`-tracking, single shared environment) — always reflects what's about to ship to prod.
- **Feature-branch staging** (parallel, on-demand) — for UAT before merge.

Both modes run on the same host but use different Compose project names and hostnames.

### Smoke Tests

The SRE agent runs smoke tests after any staging deploy:

- Master API is reachable and healthy.
- Frontend is built and served.
- MQTT connectivity verified.

If smoke tests fail, the deploy is rolled back automatically.

## File Ownership Matrix

| File(s) | Owner | Notes |
|---|---|---|
| `Dockerfile` | Engineer | Prod image; SRE proposes safe additions (healthchecks, non-root user). |
| `Dockerfile.dev`, `Dockerfile.test` | SRE | Dev/test images; SRE owns outright. |
| `docker-compose.yml` | Shared | Base topology; SRE manages via decision records if changing. |
| `docker-compose.override.yml` | SRE | Dev overrides; bind-mounts, dev flags, Traefik labels. |
| `docker-compose.remote.yml` | SRE | Remote-host dev overrides; image-baked source, Traefik labels. |
| `docker-compose.traefik.yml` | SRE | Shared ingress stack (one per dev host). |
| `docker-compose.ci.yml` | SRE | CI test config; read-only volumes, pytest entrypoint. |
| `.sre/`, `scripts/sre/` | SRE | SRE implementation scripts; not user-facing. |
| `.github/workflows/` | SRE | CI/CD pipeline; propose changes via PR, not directly edited. |
| `docs/sre*.md`, `docs/guides/sre.md`, `docs/sre-decisions/*` | SRE | SRE documentation; user-facing guides. |

## Container Runtime Detection

The project supports both Docker and Podman. The Dockerfile installs `docker-ce-cli` and `podman-docker` shim. If `CONTAINER_SOCKET_PATH` is set (e.g., `/run/podman.sock`), the master image will use that; otherwise, it defaults to `/var/run/docker.sock`.

## Secrets & Credentials

**Never commit secrets.** Use `.env.local.*` files (gitignored) for local overrides:

```bash
# .env.local.feat-auth
ANTHROPIC_API_KEY=sk-...
GH_TOKEN=ghp_...
```

The `.sre/env-up.sh` script creates a `.env.local.<branch>` template at startup. Fill in API keys there if testing integrations.

**CI secrets:** GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, etc.) are passed as env vars to the container at build/test time — never committed.

## Building & Publishing Images

**Local image build (for testing):**

```bash
docker build -t codex-slack-master:local .
```

**Registry push (by CI):**

The `ci.yml` workflow:

1. Builds the image on merge to `main`.
2. Pushes to `$REGISTRY/codex-slack-master:sha-<commit>` and `$REGISTRY/codex-slack-master:latest`.
3. Records the digest in `MANIFEST` for reproducible deploys.

## Prod Deployment Artifacts

For prod deploys, the CI pipeline produces a **deployment artifact** (GitHub release) containing:

- `MANIFEST` — digests, migration IDs, build time, who triggered.
- `deploy.sh` — pulls images, runs migrations, restarts services.
- `rollback.sh` — reverts to previous digest.
- `verify.sh` — smoke tests post-deploy.

See `docs/deploy-prod.md` for the meta-runbook (human-facing, how to run the artifact).

## Troubleshooting

### "Docker daemon not responding"

Check `DEV_DOCKER_HOST` is reachable:

```bash
docker -H "$DEV_DOCKER_HOST" ps
```

If using remote Docker, ensure SSH keys are loaded (`ssh-add`).

### "Health check timeout"

Logs:

```bash
docker compose -p $PROJECT_NAME logs master
```

Common causes:
- Port conflict (8080 already in use).
- Missing API keys (if the app tries to validate credentials at startup).
- Network connectivity (mosquitto unreachable).

### "Changes not reflected"

Check volumes are mounted:

```bash
docker compose -p $PROJECT_NAME exec master mount | grep "src\|frontend"
```

If not mounted, the override file wasn't applied. Check `COMPOSE_FILE` env var:

```bash
echo $COMPOSE_FILE
# Should include docker-compose.override.yml
```

## Related Documentation

- **Project layout:** `.claude/CLAUDE.md` — project structure, workflow, agent responsibilities.
- **Prod deployment:** `docs/deploy-prod.md` — how to use the artifact.
- **Branch protection:** `docs/repo-harness.md` — merge rules, CI requirements.
- **SRE decisions:** `docs/sre-decisions/` — infrastructure tradeoffs and blocks.
