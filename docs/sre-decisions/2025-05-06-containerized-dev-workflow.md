# Containerized Dev & Test Workflow

**Status:** Accepted  
**Date:** 2025-05-06  

## Context

The codex-slack project needed a reproducible, isolated development and test environment. The team uses macOS for development and needs to support fast iteration with live code reloads. CI/CD also needed a way to ensure tests run identically locally and in the pipeline.

## Decision

Adopted a containerized workflow using Docker Compose:

1. **Dev environment** (`docker-compose.override.yml`):
   - Bind-mounts source code (`src/`, `frontend/`, `config/`) for live reload.
   - Runs uvicorn with `--reload` to watch Python files.
   - Frontend build runs in the container and watches `frontend/src/`.
   - No image rebuild needed when code changes.

2. **Test environment** (`Dockerfile.test`, `docker-compose.ci.yml`):
   - Containerized pytest runner with all dependencies.
   - Parity with local dev — developers can run `.sre/test.sh` locally and CI runs the same image.
   - Prevents "works on my machine but fails in CI" surprises.

3. **CI/CD updates**:
   - `ci-pr.yml` now builds and runs tests in a container instead of relying on host Python.
   - Dockerfile caching via GitHub Actions.

4. **SRE scripts** (`.sre/env-up.sh`, `.sre/env-down.sh`, `.sre/test.sh`):
   - Standard interface for spinning up/down environments.
   - Idempotent — calling twice on the same branch returns the existing env.
   - Used by both humans (developers) and agents (SRE subagent).

## Rationale

- **Reproducibility**: Dev environment behavior matches production. Eliminates dependency hell and environment drift.
- **Fast iteration**: Bind-mounted source + file watchers = instant feedback without rebuilds.
- **Local-CI parity**: Same test image and commands locally and in CI. Debugging failures is easier.
- **Isolation**: Multiple branches can run dev envs in parallel without interference (separate Compose projects).
- **Minimal friction**: No new tools (everyone has Docker); setup is `env-up.sh` or ask SRE.

## Alternatives Considered

1. **Host Python + Docker only for services** — Rejected. Leads to "works locally, fails in CI" issues due to environment differences.
2. **Kubernetes for dev** — Rejected. Overkill for a single developer. K8s for prod may come later.
3. **Podman only** — Rejected. Team uses Docker; Podman support via `CONTAINER_SOCKET_PATH` allows migration later.
4. **Virtual machines** — Rejected. Heavy, slow, less portable than containers.

## Consequences

- **Positive**: Fast feedback loop, reproducible tests, parallel branch envs, no system-wide Python pollution.
- **Negative**: Requires Docker to be installed and running (acceptable; all developers have it).
- **Risk**: If Docker daemon is down, dev env is unavailable. Mitigation: Quick restart, or use remote `DEV_DOCKER_HOST`.

## Implementation

- `.sre/env-up.sh` — spin up dev env (idempotent).
- `.sre/env-down.sh` — tear down dev env.
- `.sre/test.sh` — run tests locally.
- `Dockerfile.dev` — dev image with live-reload optimizations.
- `Dockerfile.test` — test image with pytest.
- `docker-compose.override.yml` — dev overrides (bind-mounts, local ports, reload flags).
- `docker-compose.ci.yml` — CI test config.
- Updated `ci-pr.yml` to build and run tests in container.

## Notes

- The main `Dockerfile` remains unchanged for prod image integrity.
- A `/health` endpoint is required for container health checks. Ensure `src/master/main:app` exposes it.
- SRE scripts assume Docker socket is available at `/var/run/docker.sock` or via `CONTAINER_SOCKET_PATH`.
- For remote dev (e.g., shared staging host), set `DEV_DOCKER_HOST=ssh://user@host` in shell environment.

## References

- `docs/guides/sre.md` — full SRE workflow.
- `.claude/CLAUDE.md` — project instructions; SRE workflow section.
- `.sre/*.sh` — implementation scripts.
