---
title: "ADR-0002: Google Drive as agent workspace and outbound delivery channel"
status: proposed
date: 2026-03-25
decision-makers: [user, architect agent]
consulted: []
informed: []
---

## Context and Problem Statement

ADR-0001 introduced outbound file delivery via Discord/Slack binary attachments. Two problems have emerged in UAT:

1. **Delivery mechanism fragility** — the agent must format its entire text reply as a raw JSON object containing `output_files`. LLMs do not reliably produce structured JSON as their sole text output; any preamble or markdown fencing breaks parsing silently.
2. **Platform size limits and complexity** — Discord's 25 MB cap requires a tiered compression pipeline; large files still fail. Slack imposes its own file API constraints.

The user proposes extending the agent's workspace concept: alongside the git repository, agents gain access to a Google Drive folder as cloud storage. Outbound files are uploaded to Drive and the agent replies with a URL rather than a binary attachment. Google Docs/Sheets URLs shared by users are also treated as a richer inbound path, bypassing binary docx/xlsx parsing.

## Decision Drivers

- Eliminate the JSON-in-text fragility of the current outbound mechanism
- Remove platform file size limits as a hard constraint
- Provide persistent, browsable cloud storage for agent outputs
- Optionally improve inbound document quality when users share Google Workspace URLs
- Keep the solution operationally simple (no new infrastructure beyond Google credentials)

## MCP Research Findings (2026-03-25)

A research spike was conducted to assess available MCP servers for Google Drive. Key findings:

### Official `@modelcontextprotocol/server-gdrive`
- **Archived and unmaintained** as of late 2024. Moved to `modelcontextprotocol/servers-archived`.
- **Read-only** — exposes only a `search` tool; no upload, no folder creation, no shareable URL generation.
- **OAuth2 browser flow only** — service accounts are not supported. Completely unusable in a headless container without pre-seeding an expiring token (Google revokes refresh tokens for test-mode apps after 7 days).
- **Auth is broken** in recent versions (`server-gdrive auth` command throws `MODULE_NOT_FOUND`; multiple open unresolved issues).
- **Verdict: unusable for this use case.**

### `rclone-ui/rclone-mcp`
- Wraps rclone's RC (remote control) HTTP API and auto-generates tools from the rclone OpenAPI spec.
- rclone itself **natively supports service account JSON files** (`service_account_file` config key) — fully headless, no browser.
- rclone supports upload (`rclone copy`) and shareable URL generation (`rclone link` — creates an "anyone with the link" permission and returns the URL).
- **Daemon requirement** — the MCP wrapper connects to a running `rclone rcd` daemon over HTTP; the daemon must be started as a sidecar. This adds operational complexity in a container.
- **Maturity: very low** — 2 GitHub stars, 2 commits total as of March 2026. API is auto-generated; tool descriptions are sparse.
- `rclone link` may fail with 403 `insufficientFilePermission` if the Google Workspace domain admin has disabled "anyone with the link" sharing.
- **Verdict: technically correct but too immature and operationally complex for production.**

### Community MCP servers (piotr-agier, taylorwilsdon, rishipradeep, asadudin)
- Several community servers support read + write + folder creation.
- **All use OAuth2 browser flow only** — none support service account JSON for headless use.
- Some (e.g. `rishipradeep`) allow pre-minting a refresh token via env vars, which avoids the interactive browser step but is not a service account.
- **Verdict: not suitable for headless container use without workarounds.**

### Google's official Cloud MCP servers
- Google announced official MCP support in late 2025.
- **Google Drive is not on the supported products list** as of March 2026.
- **Verdict: not available.**

### Cross-cutting issue: shareable URL generation
No MCP server today exposes an atomic "upload file → return shareable URL" tool. The operation always requires two API calls: (1) upload → get file ID; (2) set `anyone` permission → construct URL. rclone's `rclone link` handles both steps in one CLI call and is the most battle-tested path for this workflow.

### Research conclusion
**No production-ready MCP server exists today for headless Google Drive upload + shareable URL.** The most reliable mechanism is rclone CLI called directly — either by the master process or by the agent via bash, without an MCP wrapper.

## Considered Options

### Outbound delivery

1. **rclone CLI in agent container (agent-initiated)** — rclone is installed in the agent container and configured with a mounted service account JSON. The agent runs `rclone copy <file> gdrive:/<folder>/` then `rclone link gdrive:/<folder>/<file>`, captures the URL from stdout, and includes it in its plain-text reply. No MCP, no manifest, no binary transfer to master.
2. **rclone CLI in master (master-initiated)** — agent uses a manifest file (writes `/tmp/.agent_output_manifest.json`) to declare output files; master copies files via `podman cp`, then calls rclone to upload to Drive and post the URL. Drive logic stays centralised in master; agent writes files and a manifest as normal.
3. **Custom thin MCP server wrapping rclone** — a ~50-line Python MCP server wraps the two rclone commands and exposes a single `upload_and_share` tool. Installed in the agent container. Combines the agent-initiated upload model with a clean MCP tool interface.
4. **Google Drive API called directly from master (no rclone)** — master uses the `google-api-python-client` library with a service account credential to upload files and create sharing permissions. No rclone dependency; more code but full control.
5. **Manifest file + platform upload (current approach, no Drive)** — agent writes `/tmp/.agent_output_manifest.json`; master copies files via `podman cp` and uploads to Discord/Slack. Solves the JSON-in-text fragility but keeps platform size limits in play.

### Inbound from Google Workspace

1. **Master detects Google Docs/Sheets URLs, downloads via API** — when the user's message contains a `docs.google.com` or `sheets.google.com` URL, master calls the Google Docs/Sheets export API to fetch as plain text / CSV and injects into the prompt. Agent sees clean structured text rather than a binary file.
2. **Agent fetches via rclone or MCP** — agent uses rclone or an MCP tool to read the document directly. Requires rclone or MCP to be running and configured in the container.
3. **No change — user must share as file** — rejected; violates zero-friction requirement.

### Agent workspace model

1. **Shared Drive folder per deployment** — one Google Drive folder (or Shared Drive) for all agents in a deployment. Master has a service account credential; rclone or the Drive API is configured with the same credential.
2. **Per-agent subfolder** — within the shared Drive, each agent gets its own subfolder keyed by `agent_name`. Keeps outputs organised and prevents collisions.
3. **Per-session subfolder** — within the agent subfolder, each conversation gets a timestamped subfolder. Provides full auditability but may produce many small folders.

## Open Questions

1. ~~**MCP server maturity**~~ — **Resolved:** no production-ready MCP server exists for this use case. rclone CLI is the right mechanism.
2. ~~**Auth model**~~ — **Resolved:** service account JSON file, configured via rclone `service_account_file`. Fully headless, no browser. Requires a GCP project with Drive API enabled and a service account with Drive access. Do we have one?
3. ~~**MCP sidecar lifecycle**~~ — **Moot** if rclone CLI is used directly; no persistent MCP process needed.
4. **Which layer owns Drive — agent or master?** — Option 1 (agent runs rclone) keeps master simple but requires rclone in every agent container image. Option 2 (master runs rclone) centralises Drive logic but requires the manifest mechanism. Option 3 (thin MCP) is the cleanest interface but adds a custom server to maintain. Option 4 (Drive API in master) adds no container dependency but more Python code.
5. **Inbound URL detection** — should master detect and pre-fetch Google Workspace URLs automatically, or should the user explicitly signal intent?
6. **Fallback for non-Drive users** — users without Google accounts cannot view restricted Drive URLs. Policy decision: default to "anyone with the link" (public) or "restricted" (Google account required)?
7. **Does this supersede ADR-0001 outbound?** — Drive delivery and platform attachment delivery are not mutually exclusive. Drive could be the preferred path when configured; platform attachment a fallback for small files or when Drive credentials are absent.
8. **rclone link and domain policy** — `rclone link` fails with 403 if Google Workspace admin has disabled external link sharing. How do we detect and report this clearly to the operator?

## Decision

*Not yet made — pending resolution of open questions 4–8 above.*

## Consequences

*To be completed after decision is made.*
