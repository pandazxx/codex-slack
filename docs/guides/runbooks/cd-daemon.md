# CD Daemon — Design and Operator Guide

Covers the continuous-deployment pipeline introduced in feat/3.2:
automatic image promotion, environment separation, and rollback on failure.

---

## Overview

The CD daemon is a lightweight Python process (`src/cd/`) that runs **alongside but
outside the master container**.  It polls GHCR for a new image digest, redeploys
the master container via docker/podman compose when the digest changes, and
automatically rolls back to the previous image if the container fails to stay
running after the deploy.

```
GitHub push ──► GitHub Actions (CI)
                  │  build + push master image
                  ▼
              GHCR registry
                  │  codex-slack-master:latest   (staging tracks this)
                  │  codex-slack-master:v1.2.3   (production tracks this)
                  │  codex-slack-master:sha-abc  (pinned rollback ref)
                  │
                  │  poll every CD_POLL_INTERVAL_SECONDS
                  ▼
          CD Daemon  (dedicated codex-slack-cd-daemon image)
            │
            ├── pull image, compare digest to state.json
            ├── docker compose up -d --force-recreate
            ├── wait CD_HEALTH_CHECK_DELAY_SECONDS
            ├── docker inspect → State.Status == "running"?
            │       YES ──► save new digest to state.json ✓
            │       NO  ──► rollback: pull previous digest, compose up with old image
            └── save consecutive_failures to state.json
```

---

## Daemon Image

The CD daemon runs from a dedicated minimal image (`Dockerfile.cd-daemon`) that
contains only Python, the Docker CLI, the Compose plugin, and `python-dotenv`.
It is published to GHCR by `publish-cd-daemon.yml` on every `master` commit that
touches `src/cd/`, `src/logging_utils.py`, or the Dockerfile itself.

| Image | Tag | Description |
|-------|-----|-------------|
| `ghcr.io/<org>/codex-slack-cd-daemon` | `latest` | Built on every qualifying master commit |
| `ghcr.io/<org>/codex-slack-cd-daemon` | `sha-<7>` | Immutable per-commit reference for pinning |

**Why separate from the master image?**  The daemon image rarely changes and
must remain stable regardless of what the master image contains.  A bad master
release cannot accidentally break the daemon binary.

Set `CD_DAEMON_IMAGE` in `.env` to pin the daemon to a specific `sha-` tag for
reproducible deployments.

---

## Master Image Tag Strategy

| Tag | Pushed on | Used by |
|-----|-----------|---------|
| `latest` | Every merge to `master` | Staging (default) |
| `v1.2.3` | Semver release tag (e.g. `git tag v1.2.3 && git push --tags`) | Production |
| `sha-abc1234` | Every push | Pinned rollback references |

The daemon uses the **repo-digest** (`image@sha256:…`) internally so it can
detect when the tag has moved even if the tag name stays the same (e.g. `latest`
pointing to a new commit).

---

## Environment Variables

All settings are read from environment variables.  Only `CD_IMAGE` is required —
everything else has a sensible default.  The master service variables
(`ANTHROPIC_API_KEY`, `GH_TOKEN`, etc.) are managed by the master itself and do
**not** need to appear in the daemon's `.env`.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CD_IMAGE` | **yes** | — | Base image name without tag, e.g. `ghcr.io/org/codex-slack-master` |
| `CD_IMAGE_TAG` | no | `latest` | Tag to track. Set to a semver for production. |
| `CD_CONTAINER_NAME` | no | `codex-slack-master` | Name of the master container on this host. |
| `CD_COMPOSE_FILE` | no | `docker-compose.yml` | Path to the compose file that defines the master service (as seen from inside the daemon container). |
| `CD_COMPOSE_SERVICE` | no | `master` | Service name inside the compose file. |
| `CD_COMPOSE_BINARY` | no | `docker compose` | Compose binary to invoke. Use `docker-compose` for Compose v1. |
| `CD_ENV_FILE` | no | — | Extra `.env` file forwarded to compose via `--env-file`. Rarely needed; omit unless you have variables that must be injected at deploy time. |
| `CD_STATE_FILE` | no | `data/cd/state.json` | JSON file where the daemon persists its deploy state. |
| `CD_POLL_INTERVAL_SECONDS` | no | `300` | How often (seconds) to poll the registry. |
| `CD_HEALTH_CHECK_DELAY_SECONDS` | no | `30` | Seconds to wait after container start before checking health. |
| `CD_ROLLBACK_ON_FAILURE` | no | `true` | Auto-roll back when health check fails. |
| `CD_DRY_RUN` | no | `false` | Log all actions without executing any subprocess commands. |
| `CD_NOTIFY_SLACK_WEBHOOK_URL` | no | — | Slack incoming-webhook URL. When set, the daemon POSTs `{"text": "..."}` on deploy/rollback events. |
| `CD_NOTIFY_DISCORD_WEBHOOK_URL` | no | — | Discord incoming-webhook URL. When set, the daemon POSTs `{"content": "..."}` on deploy/rollback events. |

---

## Notifications

The daemon can POST a plain-text message to Slack and/or Discord incoming webhooks
simultaneously at the following events:

| Event | Message |
|-------|---------|
| Deploy succeeded | ✅ deployed `<image>:<tag>` successfully |
| Deploy command failed | ❌ deploy failed for `<image>:<tag>` |
| Health check failed (rollback queued) | ⚠️ health check failed — rolling back… |
| Rollback completed | 🔄 rolled back `<container>` to `<previous_digest>` |
| Rollback also unhealthy | 🆘 rollback also unhealthy — manual intervention required |

Both webhooks are called in parallel threads with a 15-second join timeout so a slow
or unreachable webhook does not block the daemon's main loop.  All errors are logged
and swallowed; a broken webhook URL never causes a missed deploy.

When `CD_DRY_RUN=true` the notification is only logged, not POSTed.

### Slack setup

Create an incoming webhook in the Slack app settings
("Incoming Webhooks" → "Add New Webhook to Workspace").
The URL looks like `https://hooks.slack.com/services/T.../B.../...`.

```bash
# .env.staging (add to your existing variables)
CD_NOTIFY_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
```

### Discord setup

In your Discord server, go to **Channel Settings → Integrations → Webhooks → New Webhook**,
copy the URL.  The URL looks like `https://discord.com/api/webhooks/<id>/<token>`.

```bash
CD_NOTIFY_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
```

Both variables can be set at the same time; the daemon notifies all configured
destinations for every event.

---

## How Rollback Works

The daemon stores two digest references in `state.json`:

- `deployed_digest` — the image currently running (full `image@sha256:…` form).
- `previous_digest` — the image that was running before the last deploy.

**Rollback trigger:** After deploying a new image the daemon waits
`CD_HEALTH_CHECK_DELAY_SECONDS` seconds and then checks whether the container
is still in `running` state via `podman inspect`.  If it has transitioned to
`exited` or disappeared, this indicates the new master process crashed on startup
(e.g. a bad config, import error, or missing env var).

**Rollback flow:**

1. Pull the `previous_digest` ref from the registry (in case the local layer
   cache was evicted during the failed deploy).
2. Run compose with `MASTER_RUNTIME_IMAGE=<previous_digest> … up -d --force-recreate`.
3. Wait `CD_HEALTH_CHECK_DELAY_SECONDS` again and verify health.
4. If the rollback container is also unhealthy, log `cd.rollback_also_unhealthy`
   and increment `consecutive_failures`.  This signals that manual intervention
   is required — the daemon will not loop indefinitely.

**No previous digest:** On the very first deploy (no prior `state.json`) there is
nothing to roll back to.  The daemon records the failure and waits for the next
poll cycle.

---

## Deployment Setup

### Prerequisites

- Docker (or rootless Podman) on the host.
- `docker-compose.yml` and `docker-compose.cd-daemon.example.yml` checked out
  on the host in the same directory.
- Docker socket accessible at `CONTAINER_SOCKET_PATH` (default `/var/run/docker.sock`).
- GHCR credentials available to the daemon for pulling private images
  (log in with `docker login ghcr.io` or set `DOCKER_CONFIG` before starting).

### Minimal .env

Copy `.env.example` to `.env` and set the one required variable:

```bash
cp .env.example .env
# Edit .env — at minimum set:
#   CD_IMAGE=ghcr.io/<org>/codex-slack-master
```

Master-service variables (`ANTHROPIC_API_KEY`, `GH_TOKEN`, etc.) are managed
by the master container itself and do not belong in this file.

### Staging

Staging tracks `latest`, which is pushed on every merge to `master`.

```bash
# .env  (staging)
CD_IMAGE=ghcr.io/<org>/codex-slack-master
# CD_IMAGE_TAG=latest          # default — no need to set
# CD_POLL_INTERVAL_SECONDS=120 # optional: poll more frequently
```

```bash
docker compose -f docker-compose.cd-daemon.example.yml up -d
docker compose -f docker-compose.cd-daemon.example.yml logs -f
```

### Production

Production tracks a specific semver tag.  Update `CD_IMAGE_TAG` when you want
to promote a release.

```bash
# .env  (production)
CD_IMAGE=ghcr.io/<org>/codex-slack-master
CD_IMAGE_TAG=v1.2.3           # ← update this when promoting a release
# CD_HEALTH_CHECK_DELAY_SECONDS=45  # optional: give the master more time
```

To release a new version to production:

```bash
# On the release commit:
git tag v1.2.3
git push origin v1.2.3
# GitHub Actions publishes image:v1.2.3 to GHCR automatically.

# On the production host, bump the tag and restart the daemon:
sed -i 's/^CD_IMAGE_TAG=.*/CD_IMAGE_TAG=v1.2.3/' .env
docker compose -f docker-compose.cd-daemon.example.yml restart codex-slack-cd-daemon
```

The daemon picks up `v1.2.3` on its next poll, detects the new digest, and
deploys automatically.

### Dry-Run Validation

Before enabling on a live environment, run with `CD_DRY_RUN=true` to verify
the daemon can reach the registry and compose file without touching the running
master:

```bash
CD_DRY_RUN=true python -m src.cd.main
```

Expected log output on a healthy poll:

```
cd.pull_start image=ghcr.io/org/codex-slack-master:latest
cd.pull_done image=... digest=ghcr.io/org/codex-slack-master@sha256:...
cd.new_image image=... old_digest=- new_digest=ghcr.io/...@sha256:...
cd.deploy_dry_run image=ghcr.io/...@sha256:...
cd.health_wait container=codex-slack-master delay=30s
cd.health_result container=codex-slack-master status=running healthy=True
cd.deploy_success image=... digest=...
```

---

## State File

The daemon persists its state to `CD_STATE_FILE` (default `data/cd/state.json`).
This file survives daemon restarts so a restarted daemon knows which image is
already deployed and will not redeploy unnecessarily.

```json
{
  "consecutive_failures": 0,
  "deployed_at": "2026-03-19T12:00:00+00:00",
  "deployed_digest": "ghcr.io/org/codex-slack-master@sha256:abc123...",
  "previous_digest": "ghcr.io/org/codex-slack-master@sha256:def456..."
}
```

Mount the state file directory as a named volume or host bind-mount so it
persists across daemon container restarts.

---

## GitHub Actions Workflow

`publish-master.yml` runs on every push to `master` and on semver tags:

```
push to master  →  build  →  push image:latest + image:sha-<commit>
push v1.2.3 tag →  build  →  push image:v1.2.3 + image:sha-<commit>
```

The workflow uses GitHub's build cache (`cache-from: type=gha`) so layer
rebuilds on unchanged layers are fast.

To trigger a manual build without a code push:

```bash
gh workflow run publish-master.yml
```

---

## Troubleshooting

### Daemon does not detect a new image

- Confirm the image was actually pushed: `gh run list --workflow=publish-master.yml`.
- Check that `CD_IMAGE` and `CD_IMAGE_TAG` match what was pushed to GHCR exactly.
- Verify GHCR credentials inside the daemon container:
  ```bash
  docker exec codex-slack-cd-daemon docker pull $CD_IMAGE:$CD_IMAGE_TAG
  ```
- Reduce `CD_POLL_INTERVAL_SECONDS` temporarily.

### Deployment fires but master does not start

- Check `cd.health_check_failed` in daemon logs.
- Inspect the failed master container:
  ```bash
  docker logs codex-slack-master
  docker inspect codex-slack-master | jq '.[0].State'
  ```
- Common causes: incompatible image for the running compose config, Docker
  socket permission error, or a missing master-side setting.
- If `CD_ROLLBACK_ON_FAILURE=true`, the daemon will have already rolled back.
  Check `state.json` — `deployed_digest` should point to the previous good image.

### Rollback also fails (`cd.rollback_also_unhealthy`)

The `previous_digest` image is also failing to start.  This usually means a
bad shared config (secret mounts, master-side settings) rather than a bad image.

Manual recovery:

```bash
# Find a known-good sha- tag from GHCR
docker pull ghcr.io/org/codex-slack-master:sha-<good-commit>

# Deploy it manually
MASTER_RUNTIME_IMAGE=ghcr.io/org/codex-slack-master:sha-<good-commit> \
  docker compose -f docker-compose.yml \
  up -d --no-build --force-recreate master

# Reset daemon state to the known-good digest
cat > data/cd/state.json <<EOF
{
  "consecutive_failures": 0,
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)",
  "deployed_digest": "ghcr.io/org/codex-slack-master@sha256:<good-digest>",
  "previous_digest": null
}
EOF
```

### `consecutive_failures` keeps climbing

The daemon is failing on every poll.  Common causes:

- `CD_COMPOSE_FILE` path is wrong inside the daemon container (check volume mounts).
- Compose binary (`CD_COMPOSE_BINARY`) not installed in the daemon image.
- Podman socket not mounted or wrong path.

Enable debug logging:

```bash
docker exec codex-slack-cd-daemon \
  python -c "import logging; logging.basicConfig(level=logging.DEBUG); \
  from src.cd.config import load_cd_settings; print(load_cd_settings())"
```

---

## Security Notes

- The CD daemon is granted **read/write access to the Docker socket** so it can
  stop, remove, and recreate the master container.  Treat it with the same trust
  level as the master itself.
- GHCR pull credentials should be scoped to `read:packages` only.
- The daemon does not push images or modify source code — it only pulls and
  redeploys.
- `CD_DRY_RUN=true` is safe to run in any environment for validation.
