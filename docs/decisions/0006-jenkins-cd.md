---
title: "ADR-0006: Adopt Jenkins as the CD engine, retire the Python CD daemon"
status: proposed
date: 2026-05-05
decision-makers: [project-owner]
consulted: [sre-agent, engineer-agent]
informed: [tester-agent, doc-writer-agent]
supersedes: ["ADR-0005 (cicd-pipeline-design) — partially: §1 Jenkins decision and §2 CD daemon role"]
---

> **Numbering note.** The repository currently has two ADRs filed under `0005-*` and
> two under `0006-*` (a pre-existing collision). This ADR uses the file name
> requested by the user (`0006-jenkins-cd.md`) and is intended to extend/supersede
> the CI/CD pipeline ADR `0005-cicd-pipeline-design.md`. The numbering should be
> reconciled in a follow-up housekeeping commit; it does not affect the technical
> decision below.

## Context and Problem Statement

ADR-0005 (`0005-cicd-pipeline-design.md`) accepted a model where GitHub Actions
performs CI (build + push to GHCR) and a custom Python CD daemon (`src/cd/`) on
the deployment host polls GHCR and redeploys the master container via
`docker compose` when a digest changes. The daemon also performs a post-deploy
health check and rolls back to the previous digest on failure.

In practice the daemon has proven operationally fragile because it runs Docker
orchestration *from inside a container*: a single bug-fix PR (#116) closed five
distinct issues — Docker socket permissions and `DOCKER_GID` forwarding, volume
mount shadowing of the project directory, env-file forwarding to `compose`,
compose-file path visibility from inside the container, and idle-timer kill
behaviour interacting with the active agent. Each new environment variable or
mount point that the master service needs becomes another forwarding problem
for the daemon. The daemon is also a bespoke piece of code that we have to
test, image, version, and document.

Independently, the project owner wants to host CD for *other* projects on the
same single Docker host and is willing to operate a long-lived CD service.
This makes a generic CD orchestrator (which the daemon is not) attractive
beyond the needs of this one repository.

The question this ADR answers: **what runs the staging and production deploy
loop now that we are willing to host a real CD service?**

## Decision Drivers

- *Operability* — fewer custom moving parts, fewer "it broke because the daemon
  ran inside a container" classes of bug.
- *Multi-project reuse* — one CD engine that serves this repo plus future
  projects on the same host, without re-implementing the deploy loop each time.
- *Rollback parity* — must preserve or improve the current health-check +
  auto-rollback behaviour.
- *No inbound network requirement* — the deployment host may be behind NAT or
  a strict firewall; we should not require an inbound port from GitHub runner
  IPs.
- *Auditability* — deploys, rollbacks, and approvals should be inspectable
  after the fact (build history, console logs).
- *Secret hygiene* — secrets used at deploy time (GHCR pull, Slack/Discord
  webhooks, app env) should live in a single store with access control, not be
  re-passed through `.env` shadows on every host.
- *Minimal disruption to ADR-0005's image flow* — RC tagging, `:rc` →
  `:v1.2.3` retag, and bit-identical production are working and stay.

## Considered Options

1. **Keep the Python CD daemon (status quo).**
2. **Replace the daemon with Watchtower.**
3. **Adopt Jenkins as the CD engine; retire the Python daemon.** *(chosen)*
4. **Push-based CD from GitHub Actions over SSH.**

## Decision Outcome

*Chosen option:* **Option 3 — Jenkins as the CD engine; retire the Python CD
daemon.**

Jenkins replaces `src/cd/` for staging and production. Jenkins runs as a
long-lived container on the deployment host (Docker Compose, alongside other
projects), holds the deploy credentials, and runs a per-environment Jenkinsfile
pipeline that pulls the new image, redeploys via `docker compose`, health-checks
the master container, and rolls back on failure. Existing GitHub Actions
workflows (`ci-pr.yml`, `build-on-demand.yml`, `build-rc.yml`,
`promote-release.yml`) are unchanged: GHA remains the CI engine and the GHCR
publisher.

Why Jenkins specifically wins over the alternatives:

- *Versus the Python daemon* — Jenkins is generic and battle-tested; the
  fragility of running compose from inside a container is replaced by a single
  well-understood agent that has Docker access. Multi-project support comes for
  free (one job folder per project). Build history, console logs, and approvals
  are first-class.
- *Versus Watchtower* — Watchtower is the simplest replacement but cannot
  express custom deploy logic (compose with our service name, env-file
  selection, our specific health-check, our rollback procedure, our
  notifications). Adopting it would be a step backwards on rollback parity.
- *Versus push-based GHA over SSH* — keeps the deploy host strictly outbound,
  which Watchtower also offers but Jenkins offers with custom logic on top.
  GHA-over-SSH would require an inbound port and runner IP allowlisting, which
  ADR-0005 rejected for the same reason.

Jenkins is triggered by a webhook from GitHub Actions at the end of the publish
workflow (post-image-push). Jenkins does *not* poll GHCR. (See design doc
§"How CI hands off to CD" for the full reasoning.)

### Consequences

*Good*

- A single CD engine serves this project and future ones on the same host.
- The deploy loop is expressed in a Jenkinsfile checked into this repo —
  reviewable in PRs, versioned with the code, and discoverable in the same
  place as the GHA workflows.
- Build history, console output, and approval gates are visible in the Jenkins
  UI without us building any of that.
- The "compose-from-inside-a-container" class of bug goes away: the Jenkins
  agent has direct Docker access on the host.
- GHA → Jenkins handoff is sub-second (webhook), eliminating the 5–10 minute
  polling lag the daemon had on staging and production.
- Rollback logic is preserved (and arguably improved — it lives in a
  Jenkinsfile stage, not buried in Python).

*Bad*

- One more long-lived service to keep up: Jenkins itself, its plugins, and its
  reverse-proxy / TLS termination. Jenkins LTS upgrades and plugin CVEs become
  our problem.
- We must expose a webhook endpoint that GitHub can reach (the only inbound
  network requirement we accept; it can be limited to GitHub's published hook
  IP ranges and authenticated).
- Per-project access control inside Jenkins must be set up correctly or one
  project's pipeline can read another project's secrets.
- Secret rotation moves from "edit `.env` on the host" to "rotate Jenkins
  credential" — a different runbook than today.
- The custom CD daemon code, image, workflow, and example compose file are
  removed — anyone deploying off `master` after this lands must use Jenkins.

### Confirmation

- Both Jenkinsfiles (`Jenkinsfile.staging`, `Jenkinsfile.production`) checked
  into this repo and reviewed in the implementing PR.
- A successful end-to-end trigger from `build-rc.yml` → Jenkins staging job →
  green deploy on the staging host, observed in Jenkins build history.
- A forced-failure run that exercises the rollback stage and ends in the
  previous digest running on the host.
- `src/cd/`, `Dockerfile.cd-daemon`, `.github/workflows/publish-cd-daemon.yml`,
  and `docker-compose.cd-daemon.example.yml` removed; references in
  `docs/design/cicd-pipeline.md`, `docs/design/containers/cd-container-design.md`,
  and `docs/guides/runbooks/cd-daemon.md` updated or replaced by a Jenkins
  runbook before this ADR moves to `accepted`.

## Pros and Cons of the Options

### Option 1: Keep the Python CD daemon

Status quo: a custom Python process polls GHCR and runs `docker compose` from
inside a container.

- Pro: zero migration cost; works today.
- Pro: pure-outbound network; no inbound webhook to expose.
- Pro: bit-identical to what ADR-0005 already accepted.
- Con: running compose from inside a container is the root cause of repeated
  bugs (socket GID, mount shadowing, env-file forwarding, compose-file path).
- Con: bespoke code we own end-to-end — the daemon image, its workflow, its
  runbook, its config docs.
- Con: multi-project support requires N daemon containers, each with its own
  config — no central deploy history, no shared agent.
- Con: 5–10 minute polling lag.

### Option 2: Replace the daemon with Watchtower

Off-the-shelf container that watches the registry and recreates containers on
digest change.

- Pro: trivial to install; one container, one config.
- Pro: well-known and maintained externally.
- Pro: pure-outbound, same NAT/firewall story as the daemon.
- Con: cannot express our specific deploy semantics (named compose service,
  env-file selection, custom health check, custom rollback, Slack/Discord
  notifications).
- Con: rollback semantics are weaker than the daemon's: Watchtower has no
  concept of "previous good digest, redeploy if new one fails health".
- Con: not a multi-project CD engine — it is a single-process auto-updater.
  Doesn't help the broader goal of hosting CD for other projects.

### Option 3: Adopt Jenkins as the CD engine *(chosen)*

Long-lived Jenkins controller on the deployment host. GHA POSTs to a Jenkins
webhook after the image is pushed. Jenkins runs a Jenkinsfile that pulls,
redeploys, health-checks, and rolls back.

- Pro: generic, multi-project, well-understood, long-supported (LTS).
- Pro: Jenkinsfile is in-repo, reviewable, versioned alongside the code.
- Pro: build history, console logs, approval steps, parameterised builds —
  all built-in.
- Pro: Jenkins agent on the host has direct Docker access; no
  compose-inside-a-container fragility.
- Pro: sub-second GHA → CD handoff via webhook eliminates polling lag.
- Pro: secrets live in Jenkins Credentials store, injected per stage, with
  access control and rotation as first-class operations.
- Con: another long-lived service (Jenkins + reverse proxy + TLS) to keep up.
- Con: requires an inbound webhook endpoint reachable from GitHub.
- Con: Jenkins plugin / LTS upgrade discipline is now an operational
  responsibility.
- Con: per-project authorisation inside Jenkins must be configured correctly
  or one project can leak secrets to another.

### Option 4: Push-based CD from GitHub Actions over SSH

GHA logs into the deploy host over SSH after image push and runs `docker compose`.

- Pro: no new long-lived service to host.
- Pro: deploy logs visible in GitHub UI alongside the build.
- Con: requires inbound SSH from GitHub runner IPs (allowlist / VPN /
  bastion). ADR-0005 rejected this for the same reason; nothing has changed.
- Con: no central deploy queue or history across projects — every project
  re-implements the same SSH-deploy template.
- Con: secrets (deploy SSH key, image pull token, app env) end up in GitHub
  repo secrets, increasing blast radius if a repo is compromised.

## Relationship to ADR-0005

ADR-0005 §1 ("GitHub Actions only — no Jenkins") is *superseded* by this ADR.
ADR-0005 §2 ("CD daemon on staging and production; agent control on test bed")
is *partially superseded*: Jenkins replaces the CD daemon on staging and
production; the test bed remains agent-managed (no Jenkins job, no daemon).

ADR-0005 §3 (no merge to master before UAT), §4 (three-environment promotion
path), §5 (RC-based promotion with bit-identical production image), and §6
(image tagging strategy, no `:latest`) are *unchanged and still in force*. The
GHA workflows that implement them (`ci-pr.yml`, `build-on-demand.yml`,
`build-rc.yml`, `promote-release.yml`) are not modified by this ADR; only the
post-image-push step ("how the deploy actually happens") changes.
