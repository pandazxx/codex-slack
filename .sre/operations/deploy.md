# Operation: Deploy (or upgrade) singleton stack

## Trigger condition
User asks to deploy a tagged image to staging or prod, e.g. "deploy v4.19-rc1 to staging".

## Inputs
- `<env>` — target environment: `staging` or `prod`
- `<tag>` — CI-built image tag, e.g. `master`, `v4.19-rc1`, `v4.19`

## Required env vars
- `REGISTRY` — image namespace, e.g. `ghcr.io/pandazxx`
- `STAGING_DOCKER_HOST` — required when `env=staging`, e.g. `ssh://ubuntu@10.10.10.227`
- `PROD_DOCKER_HOST` — required when `env=prod`, e.g. `ssh://ubuntu@prod.example.com`

## Pre-conditions
- The image `${REGISTRY}/codex-slack-master:<tag>` exists in the registry and is pullable from the target host.
- For `env=staging`: `STAGING_DOCKER_HOST` is set.
- For `env=prod`: `PROD_DOCKER_HOST` is set.
- The operator's SSH key is loaded and can reach the target host.

## Steps

1. Run:
   ```
   just deploy <env> <tag>
   ```
   The recipe:
   - Resolves `${REGISTRY}/codex-slack-master:<tag>` to an immutable `sha256:...` digest via `docker buildx imagetools inspect`.
   - Sets `DOCKER_HOST` from `<ENV>_DOCKER_HOST`.
   - Pulls the image by digest on the target host.
   - Runs `docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml up -d --remove-orphans` — the fixed project name `codex-slack` means an already-running stack is replaced in place (upgrade path).
   - Polls `http://<host>:${MASTER_PORT:-8080}/health` up to 90 s (18 retries × 5 s).
   - Prints the deployed image reference (tag + digest) and URL on success.

2. Verify the URL printed by the recipe responds 200:
   ```
   curl -sf http://<host>:${MASTER_PORT:-8080}/health
   ```

## On failure

- Digest resolution failure ("no manifest"): the tag does not exist or is not yet pushed. Confirm the CI build completed and the tag name is correct, then retry.
- Healthcheck timeout after deploy: the recipe already prints the last 50 lines of master logs. Pass those through and escalate — do not retry without investigation.
- Any other non-zero exit: stop and escalate. The previous singleton is still running (compose `up -d` is atomic at the container level for the services that did start).

## Rollback

Re-deploy the previous known-good tag:
```
just deploy <env> <previous-tag>
```
The fixed project name ensures the new deploy replaces the bad one in place.

## Notes

- There is exactly one singleton per host. Only one deploy can occupy `staging` at a time. Teams coordinate on who holds staging during concurrent RC UAT.
- `MASTER_PORT` defaults to `8080`. Override in `.env` if the host uses a different port.
- `DOCKER_GID` is probed from the target host automatically if not set in `.env`.
- The `prod` env works identically to `staging` once `PROD_DOCKER_HOST` is populated. No separate runbook needed.
