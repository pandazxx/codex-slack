# Operation: Run tests

## Inputs
- `<pattern>` — optional pytest `-k` pattern (e.g. `test_image_contract`)

## Required env vars
- `DEV_DOCKER_HOST`

## Pre-conditions
- (none beyond standard pre-flight)

## Steps

1. Run:
   ```
   just test [<pattern>]
   ```
   The recipe builds the test stage on `DEV_DOCKER_HOST`, runs `pytest` inside an ephemeral compose project, and tears it down on exit.

## On failure
- Non-zero exit: pytest output is already in stdout. Pass through and escalate if tests fail.
- Build failure: escalate immediately.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
