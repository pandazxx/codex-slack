# Operation: Tail logs

## Inputs
- `<env>` — `dev`, `staging`, or `prod`
- `<service>` — service name: `master` or `mosquitto`
- `<key>` — branch slug (dev only); omit for singleton envs (`staging`, `prod`)

## Required env vars
- `DEV_DOCKER_HOST` (if env=dev)
- `STAGING_DOCKER_HOST` (if env=staging)
- `PROD_DOCKER_HOST` (if env=prod)

## Pre-conditions
- Env is running.

## Steps

1. Run:
   ```
   just logs <env> <service> [<key>]
   ```
   For `env=dev`: `<key>` is the branch slug; defaults to the current git branch slug if omitted.
   For `env=staging` or `env=prod`: `<key>` is not needed (singleton project name `codex-slack` is used).

## On failure
- Non-zero exit or "no such project": confirm env is up and arguments are correct, then escalate.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
