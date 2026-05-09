# Operation: Spin up staging env

## Inputs
- `<branch>` — git branch name (used internally for logging; does not appear in the URL)
- `<image-ref>` — full image reference without digest, e.g. `ghcr.io/pandazxx/codex-slack-master:v4.7-rc2`
- `<image-digest>` — sha256 digest from the CI build, e.g. `sha256:abc123...`

## Required env vars
- `STAGING_DOCKER_HOST`
- `REGISTRY`

## Pre-conditions
- `sre-host-infra` stack is running on `STAGING_DOCKER_HOST`.
- Image digest is available from the CI build-push workflow output.

## Steps
1. Compute `VERSION_SLUG` from the image tag: strip everything up to and including `:` from `<image-ref>`, then `tr './_' '-' | tr '[:upper:]' '[:lower:]'`.
   Example: `ghcr.io/pandazxx/codex-slack-master:v4.7-rc2` → `v4-7-rc2`
2. Run `.sre/staging-up.sh <branch> <image-ref> <image-digest>`
   - Derives `VERSION_SLUG` internally (same formula as step 1).
   - Pulls image by digest, brings up services under compose project `VERSION_SLUG`, waits for healthcheck, prints URL.
   - URL pattern: `http://master.<version-slug>.<host-ip-dashed>.nip.io`

## On failure
- Non-zero exit: stop and escalate. Do not tear down — preserve state for investigation.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
