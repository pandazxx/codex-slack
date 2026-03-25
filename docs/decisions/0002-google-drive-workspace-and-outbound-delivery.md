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

## Considered Options

### Outbound delivery

1. **Google Drive upload via MCP** — agent uses the `@modelcontextprotocol/server-gdrive` MCP server (configured as a sidecar in the agent container) to upload files directly to Drive and obtain a shareable URL. Agent replies with the URL in plain text. Master passes the text reply to the platform as normal — no file transfer needed.
2. **Google Drive upload via master** — agent writes the file to disk and uses the existing manifest/inner-JSON mechanism; master calls the Drive API directly (not via MCP) to upload and post the URL. Keeps Drive logic in the master, not in the agent.
3. **Manifest file + platform upload (current approach)** — agent writes `/tmp/.agent_output_manifest.json`; master copies files via `podman cp` and uploads to Discord/Slack. Solves the JSON-in-text fragility but keeps platform size limits in play.

### Inbound from Google Workspace

1. **Master detects Google Docs/Sheets URLs, downloads via API** — when the user's message contains a `docs.google.com` or `sheets.google.com` URL, master calls the Google Docs/Sheets API to export as plain text / CSV and injects into the prompt. Agent sees clean structured text rather than a binary file.
2. **Agent fetches via MCP** — agent uses the Drive MCP server to read the document directly. Requires MCP to be running and authenticated; agent must know to call the tool.
3. **No change — user must share as file** — rejected; violates zero-friction requirement.

### Agent workspace model

1. **Shared Drive folder per deployment** — one Google Drive folder (or Shared Drive) is mounted as the agent workspace. All agents in a deployment write to it. Master has a service account credential; MCP server is configured with the same credential.
2. **Per-agent subfolder** — within the shared Drive, each agent gets its own subfolder keyed by `agent_name`. Keeps outputs organised and prevents collisions.
3. **Per-session subfolder** — within the agent subfolder, each conversation gets a timestamped subfolder. Provides full auditability but may produce many small folders.

## Open Questions

1. **MCP server maturity** — is `@modelcontextprotocol/server-gdrive` stable enough for production? Does it support write/upload, or only read? Does it work headlessly with a service account?
2. **Auth model** — OAuth2 requires a browser flow (not viable in a headless container). Service account is the right model, but requires a Google Workspace account or a GCP project. Do we have one?
3. **MCP sidecar lifecycle** — does the MCP server process survive across `claude --continue` sessions, or does it restart on every invocation? If it restarts, is startup latency acceptable?
4. **Option 1 vs Option 2 for outbound** — if the agent uploads via MCP, the URL is produced inside the agent and requires no master-side Drive logic. If the master uploads, Drive logic stays centralised but the manifest mechanism is still needed. Which layer owns Drive?
5. **Inbound URL detection** — should master detect and pre-fetch Google Workspace URLs automatically, or should the user explicitly signal intent (e.g., by pasting the URL with a specific prefix)?
6. **Fallback for non-Drive users** — users without Google accounts cannot view Drive URLs set to restricted. Default share setting (anyone with link vs restricted) needs a policy decision.
7. **Does this supersede ADR-0001 outbound?** — Drive delivery and platform attachment delivery are not mutually exclusive. Drive could be the default; platform attachment a fallback for small files or when Drive is not configured.

## Decision

*Not yet made — pending resolution of open questions above.*

## Consequences

*To be completed after decision is made.*
