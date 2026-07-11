# Operation: Spin up dev env

## Inputs
- `<branch>` — git branch name from the user's request

## Required env vars
- `DEV_DOCKER_HOST` — e.g. `ssh://ubuntu@10.10.10.238`

## Pre-conditions
- `sre-host-infra` stack is running on `DEV_DOCKER_HOST` (Traefik + `sre-traefik-public` network).
- If unsure, run: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p sre-host-infra ps`

## Steps

1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')`
2. Check if already up: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose ls --filter name=$BRANCH_SLUG`
   - If non-empty, print the existing env info and stop (not a failure).
3. Run:
   ```
   just dev-up <branch>
   ```
   The recipe builds the dev stage from the current commit, brings up services under the branch-slug compose project, waits for the master healthcheck, and prints the Traefik URL.

## On failure
- Non-zero exit: stop and escalate. Do not retry without escalation.
- Healthcheck timeout: the recipe already prints the last 50 lines of master logs. Pass through and escalate.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
