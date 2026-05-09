# Operation: Tear down staging env

## Inputs
- `<image-ref>` — full image reference used at spin-up time, e.g. `ghcr.io/pandazxx/codex-slack-master:v4.7-rc2`

## Required env vars
- `STAGING_DOCKER_HOST`

## Pre-conditions
- (none beyond standard pre-flight)

## Steps
1. Compute `VERSION_SLUG` from `<image-ref>`: strip everything up to and including `:`, then `tr './_' '-' | tr '[:upper:]' '[:lower:]'`.
   Example: `ghcr.io/pandazxx/codex-slack-master:v4.7-rc2` → `v4-7-rc2`
2. Confirm env exists: `DOCKER_HOST=$STAGING_DOCKER_HOST docker compose ls --filter name=$VERSION_SLUG`
   - If empty, report "no staging env found for <image-ref>" and stop.
3. Run `.sre/staging-down.sh <image-ref>` — brings down services and volumes for compose project `VERSION_SLUG`.

## On failure
- Non-zero exit: stop and escalate.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
