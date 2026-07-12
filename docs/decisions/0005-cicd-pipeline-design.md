# 0005 CI/CD Pipeline Design

- Status: accepted (superseded in part by ADR-0016 on 2026-07-11 — sections 2 and 4's CD-daemon-based staging/prod deploy model are retired; the CI build/tag/promotion flow in sections 1, 3, 5, and 6 remains in force)
- Date: 2026-05-04

## Context

The project deploys two Docker images (master orchestrator and agent-minimal worker) to three
environments: test bed, staging, and production. Before this ADR the repository had two GitHub
Actions publish workflows (push-triggered, master/tag only) and a custom CD daemon
(`src/cd/`) that polls GHCR for digest changes and redeploys via docker compose. There was
no PR-time test gate and no written policy for how code flows between environments.

Five design questions needed resolution:

1. Should Jenkins be adopted alongside or instead of GitHub Actions?
2. Is the CD daemon (pull-based CD) the right deployment model for all environments?
3. What are the formal trigger points and promotion rules for the three environments?
4. How should the test bed be managed given that its primary operator is an LLM agent?
5. How do we ensure master only receives UAT-approved code, and that the image deployed
   to production is bit-identical to the image that was tested on staging?

## Decision

### 1. GitHub Actions only — no Jenkins

Jenkins is not adopted. The existing GitHub Actions workflows already handle image building
and publishing. Jenkins would add an additional server to operate, a separate secret store,
plugin lifecycle management, and network access requirements between Jenkins and GHCR — with
no capability gain over what GitHub Actions provides for this project.

The master orchestrator's `gh` CLI is the bridge between the LLM agent and GitHub Actions
workflows — no separate CI server is needed for agent-triggered builds.

### 2. CD daemon on staging and production; agent control on test bed

The CD daemon is retained for staging and production but is **not used on the test bed**.

**Why pull-based fits staging and production:**

- The deployment host may be behind NAT or a firewall. Pull-based requires only outbound
  connections to GHCR; no inbound SSH port or GitHub runner IP allowlist is needed.
- The daemon provides rollback-on-failure: if a health check fails after deploy it
  re-pulls and redeploys the previous digest automatically.
- The daemon is already implemented, tested, and in use.

**Why the test bed is agent-managed:**

The test bed's purpose is pre-UAT development testing by LLM agents. Agents need to deploy
arbitrary builds on demand, iterate rapidly, and control the exact deploy lifecycle. A
passive polling daemon cannot do this. The agent already controls container lifecycle via
the Podman socket the master orchestrator holds.

### 3. No merge to master before UAT sign-off

Code is built and tested from the feature branch. `master` only receives commits after UAT
is complete. This is enforced by:

- Building release candidate images from RC tags on the feature branch (not from master)
- Requiring the PR to be approved (UAT sign-off) before it can be merged
- Enforcing linear history on master (rebase-only merges, no merge commits)
- Requiring branches to be up to date before merging

Master is a stable, always-releasable pointer. It receives new commits through fast-forward-
equivalent rebase merges only after the code has been UAT-approved on staging.

### 4. Three-environment promotion path

```
feat/x branch
      │
      │  agent: build-on-demand.yml → :sha-<hash> → test bed
      │  agent: creates PR, runs ci-pr.yml gate
      │
      ▼  git tag v1.2.3-rc1 (on branch)
         build-rc.yml: builds both images
         pushes :v1.2.3-rc1 (immutable) + :rc (mutable)
      │
      ▼  STAGING ← CD daemon, CD_IMAGE_TAG=rc
         auto-deploys on new :rc digest
         user + agent perform UAT

  ┌─ UAT issues → fix on feat/x, git tag v1.2.3-rc2, repeat ─┐
  └───────────────────────────────────────────────────────────┘

      │  UAT sign-off
      ▼  PR approved, merged to master (rebase, linear history)
      │
      ▼  git tag v1.2.3 (on master)
         promote-release.yml: retags :rc → :v1.2.3 (no rebuild)
      │
      ▼  PRODUCTION ← CD daemon, CD_IMAGE_TAG=v1.2.3
         operator updates .env, daemon deploys
```

**Test bed** — persistent, agent-managed. Agent triggers `build-on-demand.yml` via
`gh workflow run`, waits on the run ID, deploys `:sha-<hash>` directly. No daemon.

**Staging** — persistent, CD daemon, `CD_IMAGE_TAG=rc`. Receives RC builds automatically
when a `v*-rc*` tag is pushed to the feature branch. Used for user and agent UAT.

**Production** — persistent, CD daemon, `CD_IMAGE_TAG=v1.2.3`. Receives releases only
after UAT sign-off. `CD_IMAGE_TAG` is updated manually by the operator per release.

### 5. RC-based promotion with bit-identical production image

**Trigger for staging deploy:** a `v*-rc*` tag pushed to the feature branch (not a merge to
master). This is the key structural change from the original design.

**Staging tracks `:rc` (mutable).** Every new RC tag updates `:rc` in GHCR. The staging
daemon auto-deploys on digest change. Multiple RC iterations are expected during UAT; each
one advances `:rc` to the latest tested build.

**Release promotion without rebuild (`promote-release.yml`):** when a `v*` (non-rc) tag is
pushed on master after merge, the workflow pulls `:rc` by digest and pushes that same digest
under `:v1.2.3`. It does not invoke `docker build`. This guarantees that production receives
the exact image bits that were UAT-approved on staging.

**Why retag instead of rebuild:** rebuilding from the same source could theoretically produce
a different image (base layer updates, pip dependency resolution). Retagging the `:rc` digest
eliminates this risk entirely.

**Note on commit SHAs and fast-forward:** GitHub's rebase merge creates new commit SHAs even
for identical code. The image identity guarantee is not provided by commit SHA matching — it
is provided by operating on GHCR image digests in `promote-release.yml`.

### 6. Image tagging strategy (`:latest` removed)

| Tag | When pushed | Mutable | Tracked by |
|---|---|---|---|
| `:sha-<hash>` | Every `build-on-demand.yml` run | No | Agent (test bed) |
| `:v1.2.3-rc1` | RC tag push on any branch | No | Audit trail |
| `:rc` | RC tag push (always latest RC) | Yes | Staging CD daemon |
| `:v1.2.3` | Release retag on master tag | No | Production CD daemon |

`:latest` is not used. All promotion tags have unambiguous semantics and a clear owner.

## Alternatives Considered

### Jenkins

Rejected. No capability gap justifies the operational overhead.

### CD daemon on test bed

Rejected. The test bed is for iterative agent testing. A polling daemon cannot deploy on
demand or handle the rapid broken/fixed cycle that pre-UAT development involves.

### Merge to master before UAT (original design)

Rejected. In the original design, every master merge triggered an image build and auto-
deployed to staging. This meant unverified code could reach staging and potentially
production before any human had validated the behaviour. The RC-based flow inverts this:
staging only receives explicitly tagged, deliberately promoted builds.

### Staging tracks `:staging` (explicit promote step)

Considered as an alternative to staging tracking `:rc`. With `:staging`, a human or agent
would run `promote-staging.yml` to move a specific SHA to staging. Rejected because:
- RC tagging already serves as the explicit promotion action
- An additional promote step between test bed and staging adds friction without benefit
- Agents will iterate through multiple RCs; automating the staging deploy on RC tag is
  the right default

### Rebuild at release time instead of retag

Rejected. Rebuilding from the same source could produce a different image (base layer
updates, non-deterministic pip resolution). Retagging the `:rc` digest guarantees
production receives exactly what was tested.

### Watchtower (third-party pull-based daemon)

Considered as a replacement for `src/cd/`. Loses the built-in rollback-on-failure logic,
the Slack/Discord notification integration, and per-environment configurability. Not adopted.

### Push-based SSH deploy from GitHub Actions

Rejected. Requires inbound SSH access from GitHub runner IPs. Firewall constraint is real.

### Kubernetes with image-update automation (Flux/ArgoCD)

Not applicable. Single-host docker compose deployment.

## Consequences

Positive:

- `master` is always clean — it only receives UAT-approved code.
- The production image is bit-identical to the image that ran on staging during UAT.
- Staging gets RC builds automatically on tag push — no extra promotion step needed.
- Test bed is fast and flexible — agent deploys any SHA in seconds, no polling lag.
- Multiple RC iterations are first-class — each new tag advances staging automatically.
- Linear history on master makes git log clean and bisectable.

Tradeoffs:

- Operators must push two tags per release: the RC tag (on branch) and the release tag
  (on master after merge). This is intentional — each is a deliberate human action.
- GitHub's rebase merge does not guarantee commit SHA identity. Image identity is
  guaranteed instead via digest-based retagging.
- Staging always reflects the latest RC. If rc2 is pushed before rc1 UAT is complete,
  staging moves to rc2. Document this as expected behaviour; avoid pushing new RCs while
  UAT is actively in progress on the previous RC.
- The CD daemon has polling lag (≤5 min staging, ≤10 min production). Acceptable because
  both have explicit human promotion gates.

## Implementation Notes

- `build-rc.yml` triggers on `v*-rc*` tags on any branch — not just master.
- `promote-release.yml` triggers on `v*` non-rc tags on `master` only — prevents accidental
  release promotion from feature branches.
- `publish-master.yml` master-push trigger should be removed; master pushes no longer build
  images. Only tag-triggered builds remain.
- `promote-staging.yml` is not needed — staging promotion is automatic via `:rc` tag.
- Branch protection on master: rebase-only, require up-to-date, require CI checks.
- Production `CD_IMAGE_TAG` must always be a semver string, never `:rc`.
- Implement `build-rc.yml` and `promote-release.yml` after this ADR is signed off.
