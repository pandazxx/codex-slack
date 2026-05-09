# Operation: Tail logs

## Inputs
- `<branch>` — git branch name
- `<service>` — service name: `master` or `mosquitto` (default: `master`)
- `<env>` — `dev` or `staging` (default: `dev`)

## Required env vars
- `DEV_DOCKER_HOST` (if env=dev)
- `STAGING_DOCKER_HOST` (if env=staging)

## Pre-conditions
- Env is running.

## Steps
1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_' '-' | tr '[:upper:]' '[:lower:]')`
2. Set `DOCKER_HOST` based on `<env>`: dev → `$DEV_DOCKER_HOST`, staging → `$STAGING_DOCKER_HOST`
3. Run:
   ```
   DOCKER_HOST=<host> docker compose -p <BRANCH_SLUG> logs -f --tail=100 <service>
   ```

## On failure
- Non-zero exit or "no such project": confirm branch slug and env target, then escalate.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
