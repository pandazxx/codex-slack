# 0011 Version Number Display

- Status: accepted
- Date: 2026-05-05
- Branch: `topic/version-display-5f4afa2`

## Context

Operators and on-call responders cannot currently tell which version of the
codex-slack code is running inside a deployed container. The startup banner
logs configuration but no version string, and `GET /health` returns only
`{"status": "ok"}`. There is no other in-band signal of the running build.

This is a problem because:

- staging runs `:rc` and production runs `:v<major>.<minor>`, but a CD daemon
  may be slow to roll over a digest — the only way to confirm which build is
  actually live is to `docker inspect` the running container, which is an
  out-of-band step
- when triaging an incident, the responder needs to confirm "what code am I
  looking at" before reading logs or stack traces
- UAT cycles depend on knowing whether staging has picked up the latest RC

The codebase already produces immutable image tags from git tags
(`v<major>.<minor>(-rc<N>)`) via `build-rc.yml`, and the
`promote-release.yml` workflow guarantees release images are bit-identical
to their RC predecessor by **retagging rather than rebuilding**. There is no
runtime source of truth for the git tag inside the container today:

- `.git` is not in the Docker build context, so `git describe` at runtime
  cannot work in production containers
- no build-time argument currently injects the tag

## Decision

Bake the git tag into each image at CI build time as an `APP_VERSION`
environment variable, and surface it at runtime through (a) the startup log
banner of every long-running process and (b) the master `GET /health`
response.

### Build-time injection

All three Dockerfiles declare a build arg with a `dev` default and promote
it into the runtime environment:

```dockerfile
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
```

Placement rules per Dockerfile:

- `Dockerfile` — declare alongside the existing `ARG CODEX_NPM_PACKAGE`
  block near the top, set `ENV APP_VERSION` together with the existing
  `ENV` block (`PYTHONDONTWRITEBYTECODE`, etc.) so the value is present
  for the entire build and the runtime image
- `Dockerfile.agent-minimal` — same placement: alongside the existing
  `ARG` block, promoted via `ENV APP_VERSION` near the existing `ENV`
- `Dockerfile.cd-daemon` — there is no existing `ARG` block; add the
  `ARG APP_VERSION=dev` line immediately before the existing `ENV` block
  and promote with `ENV APP_VERSION=${APP_VERSION}`

The `dev` default is what local builds (`docker build .`), PR-time CI
build checks, and any forgotten workflow will report. This is intentional:
"dev" is an unambiguous signal that the binary did not come through a
tagged CI build.

### CI workflow changes

| Workflow | Pass value | Rationale |
|---|---|---|
| `build-rc.yml` | `--build-arg APP_VERSION=${{ github.ref_name }}` | Tag-triggered (`v*-rc*`); `github.ref_name` is the tag string, e.g. `v4.0-rc1` |
| `build-on-demand.yml` | `--build-arg APP_VERSION=sha-${{ steps.sha.outputs.short }}` (and append the `label-` value when present) | Build is identified by short SHA, no semver tag exists |
| `publish-cd-daemon.yml` | `--build-arg APP_VERSION=sha-$(git rev-parse --short HEAD)` | Built from master commits, not from semver tags |
| `publish-master.yml` / `publish-agent-minimal.yml` | `--build-arg APP_VERSION=sha-$(git rev-parse --short HEAD)` | Manual ad-hoc builds, no tag context |
| `ci-pr.yml` | no change — leave `APP_VERSION=dev` | Build is never pushed; the `dev` fallback is correct |
| `promote-release.yml` | **no change** — see "Release-tag display" below | This workflow does not rebuild; it retags |

For each workflow that needs the change, the build arg goes inside the
`docker/build-push-action@v5` step under `with: build-args: |`.

### Release-tag display behaviour (intentional)

`promote-release.yml` retags `:rc` to `:v<major>.<minor>` without
rebuilding, by design (see `docs/design/cicd-pipeline.md` and ADR 0005).
Production therefore runs an image whose `APP_VERSION` env var still
contains the **last RC string** (e.g. `v4.0-rc3`), not the release string
(`v4.0`).

This is accepted, not worked around. Rebuilding to embed the release tag
would break the "bit-identical to UAT-approved RC" invariant and require a
fresh round of testing. The trade-off in observability is small: any RC
that was promoted to a release is, by construction, the build that passed
UAT — so seeing `v4.0-rc3` in production logs is informative ("this is the
RC that became v4.0").

To make this less confusing for operators, the runtime version display
will use a single string field (`version`) and the operational manual will
document that production tags are the RC string of the last UAT-passed
build. No automation is added to translate RC strings into release strings.

If at some future point the team prefers production to display the release
tag, the options are:

- a) rebuild on release with `--build-arg APP_VERSION=v<major>.<minor>`
  and abandon the bit-identical invariant
- b) ship a sidecar `version.txt` file injected at promote time and have
  the running process re-read it

Both are out of scope here.

### Python helper

A new module `src/version.py` exposes a single function:

```python
def get_app_version() -> str:
    """Return the build-time-baked version string, or "dev" if absent."""
    return os.environ.get("APP_VERSION", "dev").strip() or "dev"
```

Conventions:

- the helper lives at `src/version.py`, not under any subpackage, because
  it is consumed by `src/master/`, `src/agent/`, and `src/cd/` and must
  not introduce a cross-package dependency
- the helper has no other side effects, no logging, and no caching beyond
  what `os.environ.get` already provides
- callers must not parse or transform the returned string; it is opaque

### Runtime surfaces

1. **Master startup log** — extend the existing `master.startup` log
   message at `src/master/main.py:206` to include `version=%s` as the
   first interpolated value. The change is one extra format specifier and
   one extra positional arg sourced from `get_app_version()`.

2. **Agent worker startup log** — `src/agent/main.py` does not log a
   startup banner today. Add one, emitted from `main()` after
   `configure_logging(args.log_level)` and before
   `load_worker_settings()`, of the form:

   ```
   agent.startup version=<ver>
   ```

   (Worker settings details remain logged by `run_worker` if they already
   are; this banner is purely an identity line.)

3. **CD daemon startup log** — extend `cd.daemon_start` at
   `src/cd/daemon.py:154` to include `version=%s` as the first
   interpolated value, sourced from `get_app_version()`.

4. **Health endpoint** — change `GET /health` at `src/master/main.py:265`
   to return `{"status": "ok", "version": get_app_version()}`.
   Field name is `version`, value is the raw string. No additional
   metadata (commit SHA, build time) is added in this iteration; the
   `version` string is sufficient on its own and `sha-<hash>` builds
   already encode the commit ref for non-tag builds.

The agent and CD daemon do not expose an HTTP surface, so version display
is log-only for those services.

### Scope boundary

In scope:
- baking `APP_VERSION` at CI build time
- log banner additions for master, agent, CD daemon
- `version` field on master `/health`
- `src/version.py` helper

Out of scope (deferred):
- exposing version on agent or CD daemon HTTP endpoints (neither has one)
- a `/version` endpoint distinct from `/health`
- a frontend UI badge (the SPA can read `/health` if it wants to display
  the version; that work is for a separate PR)
- emitting version as a Prometheus / metrics label
- any rebuild-on-promote scheme to make production show `v4.0` instead of
  `v4.0-rc3`

## Alternatives Considered

### 1. Runtime `git describe`

Rejected.

The Docker context excludes `.git`, so `git describe` cannot run inside
production containers. Adding `.git` to the context would bloat every
image, leak commit history into the runtime, and still produce the wrong
answer for promoted release images (which are retagged, not rebuilt from
a release tag). The git binary is installed for `gh` and repo cloning,
not for runtime introspection.

### 2. Embed version in a `VERSION` file shipped with the source

Rejected.

This would require either (a) a CI step that writes the file before
`docker build` and another step that commits it back to the branch, or
(b) a Dockerfile `RUN` that materialises the file from a build arg, which
is strictly worse than just setting `ENV APP_VERSION` directly. The env
var is the simplest representation of a single string.

### 3. Use Docker image labels (`org.opencontainers.image.version`)

Rejected as the **primary** mechanism.

OCI labels are excellent metadata for the registry and for `docker
inspect`, but they are not visible to the running process unless the
container also reads them through the Docker socket — which the master
process must not depend on. Labels can be added later for registry
hygiene without changing the runtime contract; the env var remains the
in-process source of truth.

### 4. Read version from `pyproject.toml` / package metadata

Rejected.

The project does not publish itself as a wheel, and there is no
single-source-of-truth `pyproject.toml` `version` field that tracks git
tags today. Wiring this up would require a release tooling change far
larger than the problem being solved, and would still need a CI step to
substitute the tag at build time.

### 5. Rebuild on release tag to bake `v<major>.<minor>`

Rejected.

Breaks the bit-identical invariant defined by `promote-release.yml` and
documented in ADR 0005 / `docs/design/cicd-pipeline.md`. A new build
means new bytes, which means UAT must be redone. The cost is much greater
than the benefit of seeing `v4.0` instead of `v4.0-rc3` in `/health`.

## Consequences

Positive:

- operators can confirm the running build via `curl /health` without
  shelling into the container
- on-call responders see the version in startup logs alongside other
  configuration, making log triage faster
- consistent identity line across master, agent, and CD daemon
- the `dev` fallback makes locally-built or context-less images
  unmistakably non-production
- zero new runtime dependencies; no new HTTP surface

Tradeoffs:

- production `/health` reports the RC string of the build that passed
  UAT (e.g. `v4.0-rc3`), not the release string (`v4.0`). This is by
  design and must be documented in the operations manual
- every CI workflow that builds an image needs the `--build-arg` line;
  forgetting it on a new workflow yields `dev` in the running image
  (loud and obvious, but still a possible misconfiguration)
- adds a tiny new module (`src/version.py`) to the import graph of every
  service entry point

## Confirmation

The following checks confirm the decision is implemented correctly:

- unit test for `get_app_version()` covering: env var set, env var
  unset, env var set to empty string (each must return the expected
  value, with `dev` as the fallback)
- integration / smoke test that builds the master image with
  `--build-arg APP_VERSION=test-1.2.3`, runs it, hits `/health`, and
  asserts `{"status": "ok", "version": "test-1.2.3"}`
- a CI build of any tagged RC produces an image whose `docker run --rm
  <image> sh -c 'echo $APP_VERSION'` matches the tag
- the existing agent-minimal smoke step in `build-rc.yml`,
  `build-on-demand.yml`, and `publish-agent-minimal.yml` is extended to
  assert `[ -n "$APP_VERSION" ] && [ "$APP_VERSION" != "dev" ]` for
  pushed (non-PR) builds
- after deploy to staging, the operator confirms via
  `curl https://staging/health` that the `version` field matches the
  most recent RC tag

## Implementation Guidance

Engineer should treat this ADR as defining the implementation boundary:

- add `src/version.py` with `get_app_version()` and a unit test
- patch all three Dockerfiles with the `ARG`/`ENV` block at the
  positions described in "Build-time injection"
- patch `build-rc.yml`, `build-on-demand.yml`, `publish-cd-daemon.yml`,
  `publish-master.yml`, `publish-agent-minimal.yml` to pass
  `--build-arg APP_VERSION=...` per the table in "CI workflow changes"
- leave `ci-pr.yml` and `promote-release.yml` untouched
- extend the master startup log, the agent startup (new banner), and the
  CD daemon `cd.daemon_start` log
- extend `GET /health` to include the `version` field
- update `docs/manuals/ops-manual.md` (or create the relevant section)
  to document that production reports the RC string of the UAT-approved
  build, not the release string
- add an entry to `docs/knowledge-base/lessons-learned.md` explaining
  the "production shows RC tag" behaviour so future responders do not
  mistake it for a deployment bug
