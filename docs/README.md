# Documentation Map

This directory is the canonical home for repository documentation.

## Manuals

- `docs/manuals/user-manual.md` — end-user and day-to-day operator walkthrough
- `docs/manuals/ops-manual.md` — setup, deployment, runtime, and recovery entrypoint

## Guides

- `docs/guides/onboarding.md` — contributor onboarding
- `docs/guides/container-runtime.md` — container runtime behavior and mounts
- `docs/guides/slack-setup.md` — Slack application setup
- `docs/guides/discord-setup.md` — Discord application setup
- `docs/guides/project-agent-image.md` — standalone guide for project-specific agent images
- `docs/guides/multi-agent-setup.md` — historical pre-master multi-agent setup
- `docs/guides/tutorials.md` — tutorials and guided examples
- `docs/guides/runbooks/master-agent.md` — master-agent operational runbook
- `docs/guides/runbooks/cd-daemon.md` — CD daemon operational runbook

## References

- `docs/references/api.md` — implemented command surfaces and interaction contracts
- `docs/references/config.md` — configuration keys and defaults
- `docs/references/logging.md` — logging behavior and verbosity controls
- `docs/references/schemas/README.md` — placeholder for future schema docs

## Knowledge Base

- `docs/knowledge-base/faq.md` — frequently asked questions
- `docs/knowledge-base/lessons-learned.md` — append-only lessons learned

## Design

- `docs/design/agent-container-runtime-design.md` — canonical runtime contract for agent containers
- `docs/design/agent-provisioning-detailed-design.md` — detailed design for agent/channel/repo provisioning
- `docs/design/master-agent-interface-design.md` — canonical interface between master and agent containers
- `docs/design/frontend-master-interface-design.md` — canonical interface between Slack/Discord and master
- `docs/design/master-agent-architecture.md` — architecture background and constraints
- `docs/design/master-agent-implementation-plan.md` — phased implementation plan
- `docs/design/separate-base-agent-image-detailed-design.md` — detailed design for publishing and consuming the base agent image
- `docs/design/v3-0-multi-adapter-frontend-plan.md` — v3.0 adapter/frontend design plan

## Decisions

- `docs/decisions/README.md` — ADR directory conventions

## Test Plans

- `docs/test-plans/master-agent-uat.md` — current master-agent UAT checklist

## Releases

- `docs/releases/v3.2.md`
- `docs/releases/v3.3.md`

## Compatibility Entry Points

- `README.md` — project overview and top-level navigation
- `BUILD.md` — short pointer to the ops manual and setup guides
- `USAGE.md` — short pointer to the user manual and tutorials
