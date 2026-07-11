# Operation: Undeploy singleton stack

## Trigger condition
User asks to bring down the staging or prod stack entirely (not a rollback — use `deploy.md` for rollback).

## Inputs
- `<env>` — target environment: `staging` or `prod`

## Required env vars
- `STAGING_DOCKER_HOST` — required when `env=staging`
- `PROD_DOCKER_HOST` — required when `env=prod`

## Pre-conditions
- The operator's SSH key is loaded and can reach the target host.

## Steps

1. Run:
   ```
   just undeploy <env>
   ```
   The recipe:
   - Sets `DOCKER_HOST` from `<ENV>_DOCKER_HOST`.
   - Runs `docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml down --volumes --remove-orphans`.
   - Removes all containers, networks, and volumes belonging to the `codex-slack` project on the target host.

2. Confirm the stack is gone:
   ```
   DOCKER_HOST=<env-docker-host> docker compose ls
   ```
   The `codex-slack` project should no longer appear.

## On failure

- Non-zero exit: stop and escalate. Do not retry blindly — containers may be in an inconsistent state.
- SSH unreachable: confirm host connectivity and SSH key, then escalate.

## Notes

- This command removes volumes. Data stored in compose-managed volumes (e.g. mosquitto state) is permanently lost.
- There is no "partial" undeploy — the entire `codex-slack` singleton project comes down.
- To bring the stack back after undeploy, use `just deploy <env> <tag>` (see `deploy.md`).
