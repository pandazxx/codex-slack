---
title: Auth token refresh — explicit API and auto-refresh
status: accepted
date: 2026-05-03
---

## Context

v2 provided a `/master-agent-refresh-auth` command to refresh stale Git or API tokens in a running agent container without restarting it. This was particularly useful for long-running sessions where OAuth tokens expire.

v3 has no equivalent mechanism. Currently, stale tokens require stopping and recreating the agent container with fresh environment variables.

## Decision

Add two-level token refresh support:

1. **Explicit refresh endpoint** — `POST /api/workspaces/{id}/refresh-auth` triggers a refresh of the agent container's credentials (e.g., re-injecting a new `GH_TOKEN` or re-running `claude auth`). The master service records `last_refreshed_at` on the workspace row.

2. **Auto-refresh** — master periodically checks `last_refreshed_at` and triggers a refresh proactively before tokens are expected to expire (configurable interval, defaulting to 12 hours). This eliminates the need for operators to manually intervene.

## Consequences

- `workspaces` table gains `last_refreshed_at TEXT` column (migration required).
- New `POST /api/workspaces/{id}/refresh-auth` endpoint.
- Background task in master runs the refresh check on a configurable interval.
- Refresh mechanism needs to be compatible with both claude OAuth tokens and `GH_TOKEN` style environment variables — implementation detail to be resolved during the feature slice.
