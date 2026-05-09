# Operation: Run tests

## Inputs
- `<pattern>` — optional pytest `-k` pattern (e.g. `test_image_contract`)

## Required env vars
- `DEV_DOCKER_HOST`

## Pre-conditions
- (none beyond standard pre-flight)

## Steps
1. Run `.sre/run-tests.sh [<pattern>]`
   - Builds the `test` stage on `DEV_DOCKER_HOST`.
   - Runs `pytest` inside the container.
   - Tears down the ephemeral test compose project on exit.

## On failure
- Non-zero exit: pytest output is already in stdout. Pass through and escalate if tests fail.
- Build failure: escalate immediately.

## Output
The script's stdout is the user-facing output. Pass through verbatim.
