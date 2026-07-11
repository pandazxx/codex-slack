# Design: Justfile + dotenv for compose deploys

**Status:** draft
**Author:** architect
**Date:** 2026-07-11
**Related ADRs:** ADR-0005 (CI/CD Pipeline Design), issue #245

## Problem Statement

Deploy tooling today is a set of ad-hoc bash scripts under `.sre/` that each
export a slightly different subset of environment variables, plus one operator
runbook per script. Operators (human or the `sre` agent) have to memorise which
variables belong to which script, export them by hand, and know which
`docker-compose.*.yml` files to combine. There is no first-class notion of
"deploy this version to *that* environment" — staging is hardwired to
`STAGING_DOCKER_HOST` and production has no path at all.

Issue #245 asks us to:

1. Make `docker-compose.yml` variables and per-environment Docker host endpoints
   configurable from a single `.env` file at the repo root.
2. Provide justfile targets that deploy the compose stack to `dev`, `staging`,
   or `prod` — with an explicit image version tag for staging/prod and the
   current commit for dev, both behind Traefik.

## Goals

- One documented entry point (`just <recipe>`) that humans and the `sre`
  operator agent both use. No more per-script argument idioms.
- A single `.env` at repo root drives per-machine configuration
  (Docker host endpoints, registry, secrets); `.env.example` documents every
  variable in one place.
- Per-environment deploys are generic: `just deploy <env> <tag>` works for
  `staging` today and `prod` the day a `PROD_DOCKER_HOST` is populated.
- Preserve ADR-0005 immutability guarantees for staging/prod: recipes accept a
  human tag, resolve to a digest, deploy by digest.
- Dev flow keeps its build-from-source-per-commit shape and Traefik routing.
- Existing runbook contracts (`.sre/operations/*.md`) keep working for the
  `sre` agent with minimal churn — recipes are the new implementation but the
  named operations, inputs, and outputs are unchanged.
- Real shell environment continues to override `.env` so CI, the `sre` agent's
  exported vars, and one-off overrides still win.

## Non-Goals

- Standing up a production environment. Prod support is a *code path*, not a
  running host — no `PROD_DOCKER_HOST` is provisioned here.
- Reintroducing implicit local-Docker fallback. `DEV_DOCKER_HOST=unix://...` is
  the supported way to target a local socket; the recipes never guess.
- Replacing the CD daemon or ADR-0005's promotion flow. Justfile is a
  *manual/agent* deploy tool; CD daemon still owns staging/prod on the merged
  main line.
- Consolidating `scripts/gen-env.sh` (CD-daemon runtime `.env` generator) into
  this new `.env`. Different concern, different consumer; kept separate.
- Rewriting the Slack bot config or Master runtime settings. Only compose-visible
  variables are in scope.

## Proposed Design

### Recipe surface (justfile)

Recipes stay 1-to-1 with `.sre/operations/*.md` names so runbook rewrites are
mechanical. Positional args match the current script signatures where possible.

| Recipe | Purpose | Args | Runbook it backs |
|---|---|---|---|
| `just dev-up [branch]` | Build dev stage on `DEV_DOCKER_HOST`, bring up, wait for health. Defaults `branch` to current git branch. | `branch?` | `env-up.md` |
| `just dev-down [branch]` | Tear down dev env for a branch. | `branch?` | `env-down.md` |
| `just deploy <env> <tag>` | Resolve `<tag>` → digest, pull, up on `<env>_DOCKER_HOST` using `docker-compose.staging.yml`. `<env>` in {`staging`,`prod`}. | `env`, `tag` | `staging-up.md` (and future prod) |
| `just undeploy <env> <tag>` | Bring down the compose project derived from `<tag>` on `<env>_DOCKER_HOST`. | `env`, `tag` | `staging-down.md` |
| `just status` | List active compose projects on all configured hosts. | — | `status.md` |
| `just logs <env> <service> [branch-or-tag]` | Stream logs for a service. `env` in {`dev`,`staging`,`prod`}. | `env`, `service`, `key?` | `logs.md` |
| `just shell <env> <service> [branch-or-tag]` | Exec interactive shell. | `env`, `service`, `key?` | `shell.md` |
| `just test [pattern]` | Build test stage and run pytest on `DEV_DOCKER_HOST`. | `pattern?` | `test.md` |
| `just post-merge-cleanup <branch> <tag>` | Refresh canonical staging + tear down feature-branch staging. | `branch`, `tag` | `post-merge-cleanup.md` |

Composite/private helpers (leading `_`) hold shared logic so recipes stay flat:

- `_slug SLUG_INPUT` — the branch/tag slug transform (`tr '/_.' '-' | lower`).
- `_host-ip DOCKER_HOST` — extract and dash the host IP for `.nip.io`.
- `_docker-gid DOCKER_HOST` — the remote `stat -c '%g' /var/run/docker.sock`
  probe used by `env-up.sh` today.
- `_resolve-digest IMAGE_REF` — `docker buildx imagetools inspect` or
  `docker manifest inspect` to turn a tag into a `sha256:...`.

### Environment layering

`justfile` uses `set dotenv-load := true` so `.env` at repo root is auto-loaded.
Precedence (highest to lowest):

1. Vars already exported in the caller's shell (CI, `sre` agent env, ad-hoc).
2. Vars in `.env` at repo root.
3. Recipe defaults (e.g. `DOCKER_GID` fallback via the `_docker-gid` probe).

`just` merges dotenv values *without* overriding existing environment
variables (this is `just`'s documented behaviour when `dotenv-load` is on).
That preserves the "real env wins" rule the SRE agent depends on.

### How compose sees the variables

Two layers, made explicit:

1. **Recipe → compose exports** — the recipe `export`s the variables that
   `docker-compose.yml` interpolates (`MASTER_RUNTIME_IMAGE`, `DOCKER_GID`,
   `BRANCH_SLUG`, `HOST_IP_DASHED`, `IMAGE_DIGEST`, `VERSION_SLUG`,
   `APP_VERSION`, `MASTER_SSH_AUTH_SOCK_PATH`, `DOCKER_HOST`). Compose reads
   them from the process environment. This mirrors what `.sre/*.sh` do today.
2. **`.env` file** — carries the values that are stable per-machine (secrets,
   host endpoints, registry). `just` loads them into its own environment before
   the recipe body runs, so compose picks them up transitively.

We do **not** rely on `docker compose`'s own auto-load of `.env` from the
project directory. On remote `DOCKER_HOST=ssh://...` targets the compose CLI
still runs locally, so its notion of "project directory" is the local repo
and its `.env` search would collide with ours in confusing ways. Explicit
export from the recipe is unambiguous and matches current script behaviour.
`--env-file` is not used for the same reason (adds a fourth precedence layer).

### Variable inventory

Split by *who computes it* and *whose secret it is*.

#### In `.env` (per-machine, human/agent maintained)

| Variable | Purpose | Example | Notes |
|---|---|---|---|
| `DEV_DOCKER_HOST` | Dev host endpoint | `ssh://ubuntu@10.10.10.238` | Required for `dev-up`, `test`, `logs dev`, `shell dev`. `unix:///var/run/docker.sock` allowed. |
| `STAGING_DOCKER_HOST` | Staging endpoint | `ssh://ubuntu@10.10.10.227` | Required for `deploy staging`, `undeploy staging`. |
| `PROD_DOCKER_HOST` | Prod endpoint | `ssh://ubuntu@prod.example.com` | Reserved. Recipes check for presence before `deploy prod`. |
| `REGISTRY` | Image namespace for staging/prod pulls | `ghcr.io/pandazxx` | Same semantics as today. |
| `REGISTRY_TOKEN` | Registry auth (non-GHCR) | (secret) | Optional. GHCR uses shell `GITHUB_TOKEN` per current setup. |
| `DOCKER_GID` | Host docker group GID | `988` | Optional; recipe probes if unset (same behaviour as `env-up.sh`). |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN` | Agent runtime secrets consumed by the `master` service via `docker-compose.yml` interpolation. | (secret) | Optional individually; the running app decides what's required. |
| `MASTER_SSH_AUTH_SOCK_PATH` | Path to host ssh-agent socket to bind into master | `/run/user/1000/ssh-agent.sock` | Optional; has recipe default. |
| `MASTER_CODEX_AUTH_JSON_PATH`, `MASTER_SSH_KNOWN_HOSTS_PATH`, `MASTER_GIT_USER_NAME`, `MASTER_GIT_USER_EMAIL`, `MASTER_AGENT_BASE_IMAGE`, `MASTER_PORT`, `MASTER_DRY_RUN`, `AGENT_IDLE_TIMEOUT_SECONDS`, `AGENT_AUTH_REFRESH_INTERVAL_SECONDS` | Compose interpolation pass-through | — | Optional; documented for completeness. |

#### Computed by recipes at deploy time (never in `.env`)

| Variable | How derived | Used by |
|---|---|---|
| `BRANCH_SLUG` | `_slug` on branch name | `docker-compose.override.yml` labels, project name |
| `HOST_IP_DASHED` | `_host-ip` on the target `DOCKER_HOST` | Traefik host rule |
| `APP_VERSION` | git branch + short SHA + dirty flag | Dockerfile build arg (dev) |
| `VERSION_SLUG` | `_slug` on image tag | `docker-compose.staging.yml` labels, project name |
| `IMAGE_DIGEST` | `_resolve-digest` on `<registry>/<image>:<tag>` | `docker-compose.staging.yml` `image:` |
| `MASTER_RUNTIME_IMAGE` | dev: `${BRANCH_SLUG}-master:dev`; staging/prod: `${REGISTRY}/codex-slack-master:<tag>` | Compose `image:` |
| `MASTER_AGENT_NETWORK` | `${BRANCH_SLUG_or_VERSION_SLUG}_internal` | Master runtime |
| `DOCKER_HOST` | Selected from `.env` by target env | All compose calls |

#### Not in scope for `.env` (kept elsewhere)

- CD daemon settings (`CD_*`) — separate `.env` written by
  `scripts/gen-env.sh` for the on-host daemon; do not merge.
- CI-only variables — GitHub Actions handles those via workflow secrets.

### `.env.example` layout

The committed `.env.example` at repo root is reorganised into three sections
with clear ownership markers:

```
# ============================================================================
# SECTION A — Justfile / deploy configuration
#   Consumed by: justfile recipes + docker-compose.*.yml at deploy time
#   Loaded by:   `set dotenv-load` in justfile
# ============================================================================
DEV_DOCKER_HOST=
STAGING_DOCKER_HOST=
# PROD_DOCKER_HOST=
REGISTRY=
# REGISTRY_TOKEN=
# DOCKER_GID=
# MASTER_SSH_AUTH_SOCK_PATH=

# ============================================================================
# SECTION B — Master service runtime secrets & config
#   Consumed by: docker-compose.yml `environment:` interpolation
# ============================================================================
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
CLAUDE_CODE_OAUTH_TOKEN=
GH_TOKEN=
# MASTER_PORT=8080
# MASTER_DRY_RUN=false
# ...

# ============================================================================
# SECTION C — CD daemon (unchanged; kept for compatibility)
# ============================================================================
# CD_IMAGE=ghcr.io/<org>/codex-slack-master
# CD_IMAGE_TAG=v1.2.3
# ... (existing content preserved)
```

`.env` is already gitignored (`.gitignore` line 17). No change there.

### Recipe behaviour details

- `just dev-up [branch]` — sets `DOCKER_HOST=$DEV_DOCKER_HOST`, computes
  `BRANCH_SLUG`, `HOST_IP_DASHED`, `APP_VERSION`, probes `DOCKER_GID` if
  unset, then `docker compose -p <slug> -f docker-compose.yml -f
  docker-compose.override.yml build master && up -d`, then healthcheck poll.
  Identical to `env-up.sh` semantics.
- `just deploy <env> <tag>` — resolves `${REGISTRY}/codex-slack-master:<tag>`
  to a digest, exports `IMAGE_DIGEST`, `VERSION_SLUG`, `HOST_IP_DASHED`,
  `MASTER_RUNTIME_IMAGE`, `DOCKER_HOST=$<ENV>_DOCKER_HOST`, then
  `docker compose -p <version-slug> -f docker-compose.yml -f
  docker-compose.staging.yml up -d`. Same shape as `staging-up.sh`, generic
  over env. Aborts with a clear error if `<env>=prod` and
  `PROD_DOCKER_HOST` is empty.
- `just post-merge-cleanup <branch> <tag>` — thin wrapper: calls
  `deploy staging main <tag>` (canonical staging refresh) then
  `undeploy staging <tag-of-feature-branch>`. Preserves current
  `post-merge-cleanup.md` two-step semantics.

### Runbook migration

Each `.sre/operations/*.md` is edited to replace the "Run `.sre/<script>.sh
...`" step with "Run `just <recipe> ...`". Inputs, pre-conditions, on-failure
handling, and output-passthrough contracts stay identical, so the `sre` agent
follows the same shape. The mechanical rewrite is small and testable.

`.sre/*.sh` scripts become thin wrappers around `just` for one release cycle
(they `exec just <recipe> "$@"`) so any external caller has a deprecation
window. They are removed once no callers remain in-repo and one green
canonical staging refresh has confirmed the runbooks work end-to-end.

### `just` availability

- Add `just` to the agent container image (Debian/Ubuntu package `just` or the
  official static binary release). The container image build is the
  `senior-sre` change that lands with this design.
- Document install for human users in `docs/guides/onboarding.md` (macOS:
  `brew install just`; Linux: distro package or `cargo install just`).
- CI is unaffected — CI workflows call `docker compose` directly (per
  ADR-0005), not the runbook scripts. If CI later wants to reuse a recipe,
  install `just` in the workflow step.

### Sequence example: `just deploy staging v4.19-rc1`

```mermaid
sequenceDiagram
    participant U as User/sre agent
    participant J as justfile
    participant R as Registry (GHCR)
    participant D as docker CLI (local)
    participant H as STAGING_DOCKER_HOST

    U->>J: just deploy staging v4.19-rc1
    J->>J: load .env; export DOCKER_HOST=$STAGING_DOCKER_HOST
    J->>J: MASTER_RUNTIME_IMAGE=$REGISTRY/codex-slack-master:v4.19-rc1
    J->>R: docker manifest inspect ... -> sha256:...
    J->>J: export IMAGE_DIGEST, VERSION_SLUG=v4-19-rc1, HOST_IP_DASHED
    J->>D: docker pull <image>@<digest>
    D->>H: pull via SSH
    J->>D: docker compose -p v4-19-rc1 -f base -f staging up -d
    D->>H: create/update containers
    J->>D: healthcheck poll (curl /health)
    D->>H: exec curl
    J->>U: print URL master.v4-19-rc1.<ip-dashed>.nip.io
```

## Alternatives Considered

### A. Justfile as a thin wrapper over existing `.sre/*.sh` scripts

Recipes would `exec .sre/env-up.sh "$@"` etc. Pros: minimal change, keeps two
sources of truth in sync during migration. Cons: leaves two languages for
deploy logic (bash + justfile) permanently; the `.env` question is answered
inside bash where precedence is fiddly; doesn't address issue #245's
`deploy <env> <tag>` generalisation without still editing every script.
Rejected — the wrapping is temporary migration scaffolding, not the endpoint.

### B. Make (Makefile) instead of justfile

`make` is universally available. Pros: no new tool. Cons: recipe args are
awkward (`make deploy ENV=staging TAG=v4.19-rc1`), tab-based syntax is
error-prone, `.PHONY` bookkeeping, and it wasn't asked for. `just` was
explicitly named in issue #245.

### C. Compose-native `.env` (rely on `docker compose`'s auto-load)

Drop the recipe-level export entirely; let `docker compose` load `.env` from
the project directory. Pros: simplest possible answer. Cons: (1) values like
`BRANCH_SLUG`, `IMAGE_DIGEST`, `APP_VERSION` are per-invocation and computed
at recipe time — they cannot live in a static `.env`; (2) with remote
`DOCKER_HOST=ssh://...` the "project directory" concept still resolves
locally but the operational model becomes muddy; (3) precedence rules
between compose's `.env`, `--env-file`, and process env differ from `just`'s
`dotenv-load` semantics, giving four sources of truth to explain. Rejected.

### D. direnv instead of justfile-loaded dotenv

Auto-load `.env` into the shell via `direnv allow`. Pros: works for direct
`docker compose` calls too. Cons: extra tool to install and trust; the `sre`
agent does not run through a login shell so hooking direnv is another wart;
still doesn't give us the recipe surface issue #245 asks for.

### E. One recipe per environment (`just deploy-staging`, `just deploy-prod`)

Pros: obvious names. Cons: duplicates recipe bodies; adding an env means
editing justfile *and* runbooks. Generic `just deploy <env> <tag>` is
cheaper to extend and matches how `<env>_DOCKER_HOST` scales.

## Open Questions

The recommended answers below are proposals; call them out in review if you
want any changed.

- [ ] **Recipe surface is fine?** Are the recipe names/args above the right
  surface, or do we want e.g. `just up staging <tag>` / `just down` style
  verbs? *Recommended:* keep the runbook-aligned names above so
  `.sre/operations/*.md` filenames and recipe names line up 1-to-1.
- [ ] **Justfile as source of truth vs. thin wrapper?** *Recommended:* source
  of truth. `.sre/*.sh` become one-line wrappers for a single release cycle,
  then are removed. Owner: engineer implementing.
- [ ] **First-class prod today or reserved slot only?** *Recommended:*
  reserved slot — `PROD_DOCKER_HOST` is documented in `.env.example`,
  recipes work when it is set, but no `senior-sre` bootstrap of a prod host
  is part of this change.
- [ ] **`.env` vs. `.env.deploy`?** Do we want deploy config in the same
  `.env` as CD-daemon and Master runtime settings, or a dedicated
  `.env.deploy`? *Recommended:* single `.env` with sectioned
  `.env.example`. Two files split the user's mental model without solving a
  real problem — they'd both be gitignored and both loaded by `just`.
- [ ] **Precedence: shell overrides `.env`.** *Recommended:* yes — matches
  `just`'s default `dotenv-load` behaviour and preserves the current
  contract where the `sre` agent's exported vars win. Owner: doc-writer to
  call out in `docs/sre.md`.
- [ ] **Digest resolution tool?** `docker buildx imagetools inspect` vs
  `docker manifest inspect` vs `crane digest`. *Recommended:*
  `docker buildx imagetools inspect --format '{{json .Manifest.Digest}}'` —
  already available wherever docker is, no extra tool. Owner: engineer.
- [ ] **How aggressively to consolidate `.env.example`?** Merge with existing
  CD-daemon `.env.example` content vs. keep three sections in one file vs.
  split into `.env.example` + `.env.cd.example`. *Recommended:* one file,
  three labelled sections (as sketched above). Owner: doc-writer.
- [ ] **Local docker host wording in docs/sre.md.** The issue mentions "local
  or remote docker host". *Recommended:* keep the existing rule — no
  implicit local fallback; `DEV_DOCKER_HOST=unix:///var/run/docker.sock` is
  the explicit local target. Owner: doc-writer.
- [ ] **`just` in the agent container.** Who lands the Dockerfile change to
  install `just`? *Recommended:* `senior-sre` in the same PR as the
  justfile so the agent's first `just` invocation cannot fail on a missing
  binary.
- [ ] **Wrapper deprecation window.** How long do `.sre/*.sh` remain as
  wrappers before removal? *Recommended:* one merge cycle — remove after
  the first canonical staging refresh through the new recipes has been
  observed green.

## Implementation Plan

1. **Justfile scaffold + dotenv loading.** Land `justfile` at repo root with
   recipes above, `set dotenv-load := true`, and private helpers. Rewrite
   `.env.example`. Add `just` to the agent container image (senior-sre).
2. **Recipe bodies.** Port `env-up.sh`, `env-down.sh`, `staging-up.sh`,
   `staging-down.sh`, `env-status.sh`, `run-tests.sh` into recipes. Preserve
   healthcheck poll, DOCKER_GID probe, APP_VERSION derivation verbatim so
   behaviour is unchanged.
3. **Generalise deploy.** Introduce `just deploy <env> <tag>` and
   `just undeploy <env> <tag>` with digest resolution. Wire staging first.
4. **Wrapper scripts.** Replace each `.sre/*.sh` body with
   `exec just <recipe> "$@"` so existing callers still work.
5. **Runbook rewrite.** Update `.sre/operations/*.md` to invoke recipes.
   Verify each runbook end-to-end with the `sre` agent on a scratch branch.
6. **Docs.** Update `docs/sre.md` env-var table to point at `.env` +
   `.env.example`; add `just` install note to `docs/guides/onboarding.md`.
7. **Post-adoption cleanup.** After one green canonical staging refresh via
   the new recipes, delete `.sre/env-up.sh`, `env-down.sh`, `staging-up.sh`,
   `staging-down.sh`, `env-status.sh`, `run-tests.sh`. Runbooks and justfile
   remain.
