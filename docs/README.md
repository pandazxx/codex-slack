# Documentation Map

This directory is the canonical home for repository documentation.

## Manuals

- [`docs/manuals/user-manual.md`](docs/manuals/user-manual.md) — end-user and day-to-day operator walkthrough
- [`docs/manuals/ops-manual.md`](docs/manuals/ops-manual.md) — setup, deployment, runtime, and recovery entrypoint

## Guides

- [`docs/guides/onboarding.md`](guides/onboarding.md) — contributor onboarding
- [`docs/guides/container-runtime.md`](guides/container-runtime.md) — container runtime behavior and mounts
- [`docs/guides/slack-setup.md`](guides/slack-setup.md) — Slack application setup
- [`docs/guides/discord-setup.md`](guides/discord-setup.md) — Discord application setup
- [`docs/guides/project-agent-image.md`](guides/project-agent-image.md) — standalone guide for project-specific agent images
- [`docs/guides/multi-agent-setup.md`](guides/multi-agent-setup.md) — historical pre-master multi-agent setup
- [`docs/guides/tutorials.md`](guides/tutorials.md) — tutorials and guided examples
- [`docs/guides/runbooks/master-agent.md`](guides/runbooks/master-agent.md) — master-agent operational runbook
- [`docs/guides/runbooks/cd-daemon.md`](guides/runbooks/cd-daemon.md) — CD daemon operational runbook

## References

- [`docs/references/api.md`](references/api.md) — implemented command surfaces and interaction contracts
- [`docs/references/config.md`](references/config.md) — configuration keys and defaults
- [`docs/references/logging.md`](references/logging.md) — logging behavior and verbosity controls
- [`docs/references/schemas/README.md`](references/schemas/README.md) — placeholder for future schema docs

## Knowledge Base

- [`docs/knowledge-base/faq.md`](knowledge-base/faq.md) — frequently asked questions
- [`docs/knowledge-base/lessons-learned.md`](knowledge-base/lessons-learned.md) — append-only lessons learned

## Design

- [`docs/design/containers/master-container-design.md`](design/containers/master-container-design.md) — master container startup, interfaces, lifecycle, and storage
- [`docs/design/containers/agent-container-design.md`](design/containers/agent-container-design.md) — agent container entrypoint, worker lifecycle, and runtime contract
- [`docs/design/containers/cd-container-design.md`](design/containers/cd-container-design.md) — CD daemon container startup, deploy loop, rollback, and state
- [`docs/design/containers/environment-variable-passdown-design.md`](design/containers/environment-variable-passdown-design.md) — environment variable loading, normalization, and passdown across CD, master, and agent
- [`docs/design/agent-container-runtime-design.md`](design/agent-container-runtime-design.md) — canonical runtime contract for agent containers
- [`docs/design/agent-provisioning-detailed-design.md`](design/agent-provisioning-detailed-design.md) — detailed design for agent/channel/repo provisioning
- [`docs/design/master-agent-interface-design.md`](design/master-agent-interface-design.md) — canonical interface between master and agent containers
- [`docs/design/frontend-master-interface-design.md`](design/frontend-master-interface-design.md) — canonical interface between Slack/Discord and master
- [`docs/design/master-agent-architecture.md`](design/master-agent-architecture.md) — architecture background and constraints
- [`docs/design/message-split-hint-detailed-design.md`](design/message-split-hint-detailed-design.md) — detailed design for agent-authored message split hints
- [`docs/design/separate-base-agent-image-detailed-design.md`](design/separate-base-agent-image-detailed-design.md) — detailed design for publishing and consuming the base agent image

## Decisions

- [`docs/decisions/README.md`](decisions/README.md) — ADR directory conventions

## Archive

- [`docs/archive/design/master-agent-implementation-plan.md`](archive/design/master-agent-implementation-plan.md) — archived historical implementation plan
- [`docs/archive/design/v3-0-multi-adapter-frontend-plan.md`](archive/design/v3-0-multi-adapter-frontend-plan.md) — archived historical v3.0 adapter/frontend plan

## Test Plans

- [`docs/test-plans/master-agent-uat.md`](test-plans/master-agent-uat.md) — current master-agent UAT checklist

## Releases

- [`docs/releases/v3.2.md`](releases/v3.2.md)
- [`docs/releases/v3.3.md`](releases/v3.3.md)

## Compatibility Entry Points

- [`README.md`](../README.md) — project overview and top-level navigation
- [`BUILD.md`](../BUILD.md) — short pointer to the ops manual and setup guides
- [`USAGE.md`](../USAGE.md) — short pointer to the user manual and tutorials
