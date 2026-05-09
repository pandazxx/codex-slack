# Operation: Open a shell in a service

## Inputs
- `<branch>` — git branch name
- `<service>` — `master` or `mosquitto`
- `<env>` — `dev` or `staging` (default: `dev`)

## Required env vars
- `DEV_DOCKER_HOST` (if env=dev)
- `STAGING_DOCKER_HOST` (if env=staging)

## Pre-conditions
- Env is running.

## Steps
1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_' '-' | tr '[:upper:]' '[:lower:]')`
2. Set `DOCKER_HOST` based on `<env>`.
3. For `master` service:
   ```
   DOCKER_HOST=<host> docker compose -p <BRANCH_SLUG> exec master bash
   ```
   For `mosquitto` service:
   ```
   DOCKER_HOST=<host> docker compose -p <BRANCH_SLUG> exec mosquitto sh
   ```

## On failure
- "no such container": confirm the env is up, then escalate.

## Output
Interactive shell session. Pass the command to the user verbatim for them to run, or execute directly.
