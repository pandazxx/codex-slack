# Operation: Post-merge cleanup

## Inputs
- `<merged-branch>` — the feature branch that was just merged to master
- `<tag>` — image tag to deploy to staging (default: `master`); CI publishes `codex-slack-master:master` on every push to master

## Required env vars
- `STAGING_DOCKER_HOST`
- `REGISTRY`
- `DEV_DOCKER_HOST` (for dev env teardown)

## Pre-conditions
- CI `build-push.yml` `build-master` job has completed for the merge commit.
- `STAGING_DOCKER_HOST` is set and reachable.

## Steps

1. Run:
   ```
   just post-merge-cleanup <merged-branch> [<tag>]
   ```
   The recipe:
   - Refreshes the staging singleton: runs `just deploy staging <tag>` (default `<tag>=master`), which resolves the tag to a digest and replaces the running singleton in place.
   - Checks if a dev env for `<merged-branch>` is running on `DEV_DOCKER_HOST`. If found, tears it down via `just dev-down <merged-branch>`.
   - If no dev env exists for the branch, skips silently.

## On failure
- Deploy step failure: stop and escalate. The staging singleton may be in a degraded state — investigate before retrying.
- Dev teardown failure: escalate; staging is already refreshed so this is non-critical.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
