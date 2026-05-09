# Operation: Tear down dev env

## Inputs
- `<branch>` — git branch name from the user's request

## Required env vars
- `DEV_DOCKER_HOST` — e.g. `ssh://ubuntu@10.10.10.238`

## Pre-conditions
- (none beyond standard pre-flight)

## Steps
1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_' '-' | tr '[:upper:]' '[:lower:]')`
2. Confirm env exists: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose ls --filter name=$BRANCH_SLUG`
   - If empty, report "no env found for <branch>" and stop.
3. Run `.sre/env-down.sh <branch>` — brings down services and volumes.

## On failure
- Step 3 non-zero exit: stop and escalate.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
