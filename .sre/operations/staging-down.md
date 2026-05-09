# Operation: Tear down staging env

## Inputs
- `<branch>` — git branch name

## Required env vars
- `STAGING_DOCKER_HOST`

## Pre-conditions
- (none beyond standard pre-flight)

## Steps
1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_' '-' | tr '[:upper:]' '[:lower:]')`
2. Confirm env exists: `DOCKER_HOST=$STAGING_DOCKER_HOST docker compose ls --filter name=$BRANCH_SLUG`
   - If empty, report "no staging env found for <branch>" and stop.
3. Run `.sre/staging-down.sh <branch>` — brings down services and volumes.

## On failure
- Non-zero exit: stop and escalate.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
