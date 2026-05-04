# 0005 CI/CD Pipeline Design

- Status: accepted
- Date: 2026-05-04

## Context

The project deploys two Docker images (master orchestrator and agent-minimal worker) to three
environments: test bed, staging, and production. Before this ADR the repository had two GitHub
Actions publish workflows (push-triggered, master/tag only) and a custom CD daemon
(`src/cd/`) that polls GHCR for digest changes and redeploys via docker compose. There was
no PR-time test gate and no written policy for how code flows between environments.

Three design questions needed resolution:

1. Should Jenkins be adopted alongside or instead of GitHub Actions?
2. Is the CD daemon (pull-based CD) the right deployment model, or should GitHub Actions
   push-deploy directly to hosts?
3. What are the formal trigger points and promotion rules for the three environments?

## Decision

### 1. GitHub Actions only — no Jenkins

Jenkins is not adopted. The existing GitHub Actions workflows already handle image building
and publishing. Jenkins would add an additional server to operate, a separate secret store,
plugin lifecycle management, and network access requirements between Jenkins and GHCR — with
no capability gain over what GitHub Actions provides for this project.

If the team later requires on-premises build agents that cannot reach GitHub, self-hosted
GitHub Actions runners satisfy that requirement without Jenkins.

### 2. Retain the pull-based CD daemon

The CD daemon (`src/cd/`) follows the pull-based deployment pattern: it runs on the
deployment host, polls GHCR for digest changes, and invokes docker compose to redeploy. This
is retained as the deployment mechanism for both staging and production.

**Why pull-based fits this project:**

- The deployment host may be behind NAT or a firewall. Pull-based requires only outbound
  connections from the host to GHCR; no inbound SSH port or GitHub runner IP allowlist is
  needed.
- The daemon provides rollback-on-failure built in: if a health check fails after deploy it
  re-pulls and redeploys the previous digest automatically.
- The daemon is already implemented, tested, and in use.

**Known limitations and mitigations:**

- *Polling lag* — the default poll interval is 300 s (5 min). A future improvement is to
  add a webhook receiver endpoint to the daemon so the GitHub Actions publish workflow can
  trigger an immediate check via HTTP POST rather than waiting for the next poll cycle.
- *Silent daemon failure* — if the daemon process dies, deployments stop without alerting.
  The compose file should set `restart: unless-stopped` on the daemon service, and Slack/
  Discord webhook notifications should be monitored.

**Rejected alternative — push-based SSH deploy from GHA:**

After image push a GHA job would SSH into the deployment host and run
`docker compose pull && docker compose up -d`. This is simpler operationally and makes
deploy history visible in GitHub, but requires an SSH key stored in GHA secrets and an
inbound firewall rule for GitHub runner IP ranges. Given that this project already has the
daemon and the firewall constraint is real, push-based is not adopted at this time.

### 3. Three-environment promotion path

```
PR branch  ──► CI jobs (pytest + docker build) ──► merge gate
                                                         │
                                                   merge to master
                                                         │
                                              build + push :latest + :sha-<hash>
                                                         │
                                                      STAGING
                                             (daemon tracks :latest)
                                                         │
                                              human decision: tag release
                                                         │
                                                git tag v1.2.3 + push
                                                         │
                                              build + push :v1.2.3
                                                         │
                                                    PRODUCTION
                                             (daemon tracks :v1.2.3)
```

**Test bed** — ephemeral, runs inside GitHub Actions on every PR commit. No persistent
infrastructure. The `ci-pr.yml` workflow runs `pytest` and validates both Docker builds.
Merging to master is blocked until this job passes (enforced via GitHub branch protection).

**Staging** — persistent host. The CD daemon is configured with `CD_IMAGE_TAG=latest`.
Every merge to master triggers a new image push, which the daemon detects within one poll
cycle and deploys automatically. Used for human smoke-testing before a release is tagged.

**Production** — persistent host. The CD daemon is configured with a pinned semver tag
(e.g. `CD_IMAGE_TAG=v1.2.3`). Promotion to production is a two-step human action:
(1) push a `v*` git tag; (2) update `CD_IMAGE_TAG` in the production environment's config
to the new semver (or restart the daemon with the new tag). The daemon then detects the new
digest for that tag and deploys.

## Alternatives Considered

### Jenkins

Rejected. See decision 1 above. No capability gap justifies the operational overhead.

### Watchtower (third-party pull-based daemon)

Considered as a replacement for `src/cd/`. Watchtower is a mature open-source container that
monitors registries and redeploys. It would eliminate the custom daemon code but remove the
built-in rollback-on-failure logic, the Slack/Discord notification integration, and the
per-environment configurability that the current daemon provides. Not adopted; the existing
daemon is retained.

### Push-based SSH deploy from GitHub Actions

Rejected. See decision 2 above. Requires inbound SSH access from GitHub runner IPs.

### Kubernetes with image-update automation (Flux/ArgoCD)

Not applicable. The project runs on a single host with docker compose. Kubernetes
infrastructure would be disproportionate overhead.

## Consequences

Positive:

- PRs are gated on tests; broken code cannot reach master.
- Staging always reflects the latest merged state within minutes.
- Production is promoted deliberately via a version tag; rollbacks are one tag-push away.
- No new infrastructure to operate (no Jenkins server, no Kubernetes cluster).
- The existing CD daemon and publish workflows are reused with minimal changes.

Tradeoffs:

- Staging deploys have up to 5 min lag from merge to live (polling interval).
- Production promotion requires two manual steps (git tag + env config update). This is
  intentional — a human must decide when staging is ready for production.
- The CD daemon process must itself be monitored; if it exits silently, deployments stall.

## Implementation Notes

- Add branch protection rule on `master`: require `CI — PR Tests / pytest` and
  `CI — PR Tests / Docker build check` status checks to pass before merge.
- Staging compose stack should set `restart: unless-stopped` on the CD daemon service.
- Production `CD_IMAGE_TAG` should be pinned to a semver string, never `latest`.
- When the webhook receiver improvement is implemented, record it in a new ADR or as an
  amendment to this one.
