# Operation: Post-merge cleanup

## Inputs
- `<merged-branch>` — the feature branch that was just merged to master
- `<image-ref>` — image reference for the new master build (from CI build-push output)
- `<image-digest>` — sha256 digest for the new master build

## Required env vars
- `STAGING_DOCKER_HOST`
- `REGISTRY`
- `DEV_DOCKER_HOST` (for dev env teardown confirmation)

## Pre-conditions
- CI build-push workflow has completed for the merge commit on master.
- `image-ref` and `image-digest` are available from the workflow output.

## Steps
1. Refresh canonical staging (master):
   `.sre/staging-up.sh master <image-ref> <image-digest>`
   - This updates the running `master` slug staging env in-place.
2. Tear down the merged feature-branch staging env:
   `.sre/staging-down.sh <merged-branch>`
   - If no staging env exists for the branch, skip silently.
3. Report dev env teardown note:
   "Dev env for `<merged-branch>` is the developer's responsibility to tear down.
    Run: `.sre/env-down.sh <merged-branch>` when ready."

## On failure
- Step 1 failure: stop and escalate. Do not proceed to step 2.
- Step 2 failure: escalate; the master env is already refreshed so this is non-critical.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
