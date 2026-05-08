# Documentation Map

This directory is the canonical home for repository documentation.

## Manuals

- [`docs/manuals/user-manual.md`](manuals/user-manual.md) — end-user and day-to-day operator walkthrough
- [`docs/manuals/ops-manual.md`](manuals/ops-manual.md) — setup, deployment, runtime, and recovery entrypoint

## Guides

- [`docs/guides/onboarding.md`](guides/onboarding.md) — contributor onboarding (v3)
- [`docs/guides/runbooks/master-agent.md`](guides/runbooks/master-agent.md) — master-agent operational runbook (v3)
- [`docs/guides/runbooks/cd-daemon.md`](guides/runbooks/cd-daemon.md) — CD daemon operational runbook
- [`docs/guides/container-runtime.md`](guides/container-runtime.md) — container runtime, Podman socket, and mounts (v3)
- [`docs/guides/project-agent-image.md`](guides/project-agent-image.md) — project-specific agent images
- [`docs/guides/multi-agent-setup.md`](guides/multi-agent-setup.md) — multi-agent compose example
- [`docs/guides/sre.md`](guides/sre.md) — SRE-managed dev/staging workflow
- [`docs/guides/sre-onboarding-summary.md`](guides/sre-onboarding-summary.md) — SRE onboarding summary
- [`docs/guides/repo-harness.md`](guides/repo-harness.md) — repository harness reference
- [`docs/guides/deploy-prod.md`](guides/deploy-prod.md) — production deploy steps
- [`docs/guides/tutorials.md`](guides/tutorials.md) — tutorials and guided examples (v3)

## References

- [`docs/references/api.md`](references/api.md) — REST API, WebSocket, and MQTT topic reference
- [`docs/references/config.md`](references/config.md) — configuration keys and defaults
- [`docs/references/logging.md`](references/logging.md) — logging behavior and verbosity controls
- [`docs/references/schemas/README.md`](references/schemas/README.md) — SQLite database schema (all five tables)

## Knowledge Base

- [`docs/knowledge-base/faq.md`](knowledge-base/faq.md) — frequently asked questions
- [`docs/knowledge-base/lessons-learned.md`](knowledge-base/lessons-learned.md) — append-only lessons learned
- [`docs/knowledge-base/v3-bug-triage.md`](knowledge-base/v3-bug-triage.md) — v3 bug triage notes

## Design

- [`docs/design/v3-system-architecture.md`](design/v3-system-architecture.md) — v3 system architecture (current)
- [`docs/design/streaming-agent-reply.md`](design/streaming-agent-reply.md) — streaming reply protocol
- [`docs/design/paste-image-support.md`](design/paste-image-support.md) — clipboard paste-to-attach for the web UI
- [`docs/design/attachment-management.md`](design/attachment-management.md) — native attachment storage
- [`docs/design/cicd-pipeline.md`](design/cicd-pipeline.md) — CI/CD pipeline design
- [`docs/design/containers/master-container-design.md`](design/containers/master-container-design.md) — master container startup, interfaces, lifecycle, storage
- [`docs/design/containers/agent-container-design.md`](design/containers/agent-container-design.md) — agent container entrypoint and runtime contract
- [`docs/design/containers/cd-container-design.md`](design/containers/cd-container-design.md) — CD daemon container startup, deploy loop, rollback
- [`docs/design/containers/environment-variable-passdown-design.md`](design/containers/environment-variable-passdown-design.md) — env var loading and passdown across CD, master, and agent
- [`docs/design/agent-container-runtime-design.md`](design/agent-container-runtime-design.md) — canonical runtime contract for agent containers
- [`docs/design/agent-runtime-cleanup-detailed-design.md`](design/agent-runtime-cleanup-detailed-design.md) — cleanup of transitional agent runtime startup behavior
- [`docs/design/agent-provisioning-detailed-design.md`](design/agent-provisioning-detailed-design.md) — detailed design for agent/channel/repo provisioning
- [`docs/design/separate-base-agent-image-detailed-design.md`](design/separate-base-agent-image-detailed-design.md) — base agent image publishing and consumption

The following design documents describe v2-era architecture that was superseded by ADR-0006 (drop Slack/Discord). They are kept for historical context and should not be read as a description of the current system:

- [`docs/design/frontend-master-interface-design.md`](design/frontend-master-interface-design.md) — v2 Slack/Discord-to-master interface (superseded)
- [`docs/design/master-agent-interface-design.md`](design/master-agent-interface-design.md) — v2 master/agent slash-command interface (partially superseded)
- [`docs/design/master-agent-architecture.md`](design/master-agent-architecture.md) — v2 architecture background (historical)
- [`docs/design/message-split-hint-detailed-design.md`](design/message-split-hint-detailed-design.md) — v2 chat-platform message split hints (superseded)
- [`docs/design/agent-message-notification.md`](design/agent-message-notification.md) — v2 chat-platform notification design (partially superseded)

## Decisions

- [`docs/decisions/README.md`](decisions/README.md) — ADR directory conventions
- [`docs/decisions/0001-agent-provisioning-orchestration.md`](decisions/0001-agent-provisioning-orchestration.md) — agent provisioning and orchestration
- [`docs/decisions/0002-separate-base-agent-image.md`](decisions/0002-separate-base-agent-image.md) — separate base agent image
- [`docs/decisions/0003-message-split-hint-protocol.md`](decisions/0003-message-split-hint-protocol.md) — message split hint protocol
- [`docs/decisions/0004-agent-runtime-cleanup.md`](decisions/0004-agent-runtime-cleanup.md) — agent runtime cleanup
- [`docs/decisions/0005-cicd-pipeline-design.md`](decisions/0005-cicd-pipeline-design.md) — CI/CD pipeline design
- [`docs/decisions/0005-v3-system-architecture.md`](decisions/0005-v3-system-architecture.md) — v3 system architecture (accepted; implemented)
- [`docs/decisions/0006-drop-slack-discord-integration.md`](decisions/0006-drop-slack-discord-integration.md) — drop Slack/Discord integration
- [`docs/decisions/0007-native-attachment-management.md`](decisions/0007-native-attachment-management.md) — native attachment management
- [`docs/decisions/0008-auth-token-refresh.md`](decisions/0008-auth-token-refresh.md) — auth token refresh
- [`docs/decisions/0009-runtime-configuration-and-staff-system.md`](decisions/0009-runtime-configuration-and-staff-system.md) — runtime configuration and staff system
- [`docs/decisions/0010-workspace-env-var-overrides.md`](decisions/0010-workspace-env-var-overrides.md) — workspace env var overrides
- [`docs/decisions/0011-agent-message-notification.md`](decisions/0011-agent-message-notification.md) — agent message notification
- [`docs/decisions/0011-system-vs-user-config-panel.md`](decisions/0011-system-vs-user-config-panel.md) — system vs user config panel
- [`docs/decisions/0011-version-display.md`](decisions/0011-version-display.md) — version display
- [`docs/decisions/0012-streaming-agent-reply.md`](decisions/0012-streaming-agent-reply.md) — streaming agent reply

## Archive

- [`docs/archive/design/master-agent-implementation-plan.md`](archive/design/master-agent-implementation-plan.md) — archived historical implementation plan
- [`docs/archive/design/v3-0-multi-adapter-frontend-plan.md`](archive/design/v3-0-multi-adapter-frontend-plan.md) — archived historical v3.0 adapter/frontend plan
- [`docs/archive/guides/slack-setup.md`](archive/guides/slack-setup.md) — archived v2 Slack app setup guide
- [`docs/archive/guides/discord-setup.md`](archive/guides/discord-setup.md) — archived v2 Discord app setup guide

## Test Plans

- [`docs/test-plans/v3-core-uat.md`](test-plans/v3-core-uat.md) — current v3 core UAT checklist
- [`docs/test-plans/streaming-agent-reply.md`](test-plans/streaming-agent-reply.md)
- [`docs/test-plans/paste-image-support.md`](test-plans/paste-image-support.md)
- [`docs/test-plans/version-display.md`](test-plans/version-display.md)
- [`docs/test-plans/workspace-env-var-overrides.md`](test-plans/workspace-env-var-overrides.md)
- [`docs/test-plans/agent-message-notification.md`](test-plans/agent-message-notification.md)
- [`docs/test-plans/adr-0009-staff-runtime-config.md`](test-plans/adr-0009-staff-runtime-config.md)
- [`docs/test-plans/v3-slice-14-uat.md`](test-plans/v3-slice-14-uat.md)

## Releases

- [`docs/releases/v3.2.md`](releases/v3.2.md)
- [`docs/releases/v3.3.md`](releases/v3.3.md)
- [`docs/releases/v3.9.md`](releases/v3.9.md)

Release notes are historical records and intentionally retain references to features (Slack/Discord adapters, slash commands) that were later removed by ADR-0006.

## Compatibility Entry Points

- [`README.md`](../README.md) — project overview and top-level navigation
- [`BUILD.md`](../BUILD.md) — short pointer to the ops manual and setup guides
- [`USAGE.md`](../USAGE.md) — short pointer to the user manual and tutorials
