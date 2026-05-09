# Operation: List active envs

## Inputs
- (none)

## Required env vars
- `DEV_DOCKER_HOST`
- `STAGING_DOCKER_HOST` (optional — skipped if not set)

## Pre-conditions
- (none beyond standard pre-flight)

## Steps
1. Run `.sre/env-status.sh`
   - Lists all compose projects on `DEV_DOCKER_HOST`.
   - If `STAGING_DOCKER_HOST` is set, lists staging projects too.

## On failure
- SSH connection error: confirm host is reachable and SSH key is loaded, then escalate.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
