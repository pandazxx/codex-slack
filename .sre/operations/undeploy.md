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
   - Runs `docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml down --remove-orphans`.
   - Removes the containers and networks belonging to the `codex-slack` project on the target host. Named volumes (including `master_data`, the application SQLite data) are preserved.

2. Confirm the stack is gone:
   ```
   DOCKER_HOST=<env-docker-host> docker compose ls
   ```
   The `codex-slack` project should no longer appear.

## On failure

- Non-zero exit: stop and escalate. Do not retry blindly — containers may be in an inconsistent state.
- SSH unreachable: confirm host connectivity and SSH key, then escalate.

## Notes

- Volumes are preserved: a later `just deploy <env> <tag>` reattaches `master_data` and the application data survives the undeploy/deploy cycle.
- To permanently delete the volumes as well, use `just destroy <env>`. It is interactive (requires typing the env name to confirm) and is intended for human operators only — the `sre` agent must never run it without an explicit, quoted user instruction naming the environment.
- There is no "partial" undeploy — the entire `codex-slack` singleton project comes down.
- To bring the stack back after undeploy, use `just deploy <env> <tag>` (see `deploy.md`).
