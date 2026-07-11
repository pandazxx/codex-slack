# Operation: List active envs

## Inputs
- (none)

## Required env vars
- `DEV_DOCKER_HOST`
- `STAGING_DOCKER_HOST` (optional — skipped if not set)
- `PROD_DOCKER_HOST` (optional — skipped if not set)

## Pre-conditions
- (none beyond standard pre-flight)

## Steps

1. Run:
   ```
   just status
   ```
   Lists all compose projects on `DEV_DOCKER_HOST`. If `STAGING_DOCKER_HOST` is set, lists staging projects too. If `PROD_DOCKER_HOST` is set, lists prod projects.

## On failure
- SSH connection error: confirm host is reachable and SSH key is loaded, then escalate.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
