# Operation: Spin up staging env

## Inputs
- `<branch>` — git branch name
- `<image-ref>` — full image reference without digest, e.g. `ghcr.io/pandazxx/codex-slack-master:v1.2.3`
- `<image-digest>` — sha256 digest from the CI build, e.g. `sha256:abc123...`

## Required env vars
- `STAGING_DOCKER_HOST`
- `REGISTRY`

## Pre-conditions
- `sre-host-infra` stack is running on `STAGING_DOCKER_HOST`.
- Image digest is available from the CI build-push workflow output.

## Steps
1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_' '-' | tr '[:upper:]' '[:lower:]')`
2. Run `.sre/staging-up.sh <branch> <image-ref> <image-digest>`
   - Pulls image by digest, brings up services, waits for healthcheck, prints URL.

## On failure
- Non-zero exit: stop and escalate. Do not tear down — preserve state for investigation.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
