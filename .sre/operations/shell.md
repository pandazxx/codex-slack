# Operation: Open a shell in a service

## Inputs
- `<env>` — `dev`, `staging`, or `prod`
- `<service>` — `master` or `mosquitto`
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
   just shell <env> <service> [<key>]
   ```
   For `env=dev`: `<key>` is the branch slug; defaults to the current git branch slug if omitted.
   For `env=staging` or `env=prod`: `<key>` is not needed.
   The recipe opens an interactive bash session in the named service container.

## On failure
- "no such container": confirm the env is up, then escalate.

## Output
Interactive shell session. Pass the command to the user verbatim for them to run, or execute directly.
