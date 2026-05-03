---
title: Drop Slack and Discord integration
status: accepted
date: 2026-05-03
---

## Context

In v2, Slack and Discord were the primary interfaces for users to interact with agents. Users issued slash commands (`/master-agent-*`) directly in chat channels, and agents responded in threads. This created tight coupling between the system and specific chat platforms, required platform-specific formatting pipelines, message chunking, and separate adapter code paths for each platform.

The v3 rewrite adopted a web UI (Vue 3 SPA) + REST API as the primary interface, decoupling agent orchestration from any chat platform.

## Decision

Drop Slack and Discord integrations entirely. They will not be ported to v3.

The v3 web UI is the canonical interface. If chat platform integration is needed in the future, it should be implemented as a thin adapter that calls the v3 REST API — not as a first-class concern baked into the core system.

## Consequences

- No platform-specific formatting, message splitting, or slash command handling in v3 core.
- Users interact via the web UI or the REST API directly.
- Removes the need to maintain compatibility with Slack/Discord rate limits, message size limits, and API changes.
- Future chat integration, if desired, can be implemented as an external adapter (a separate service that bridges Slack/Discord events to `POST /api/workspaces/{id}/topics/{tid}/messages`).
