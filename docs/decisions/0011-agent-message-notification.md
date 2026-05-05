---
title: "ADR-0011: Outbound notifications when agents reply"
status: proposed
date: 2026-05-05
decision-makers: [maintainers]
consulted: [architect, engineer]
informed: [users]
---

## Context and Problem Statement

Agents can take minutes to respond. The web UI is the canonical interface (per
ADR-0006), but users do not keep a browser tab open while they wait. They
have asked for a push notification on a chat platform of their choice (Discord,
Telegram, WhatsApp) that links back to the topic in the web UI. See GitHub
issue [#118](https://github.com/pandazxx/codex-slack/issues/118).

This is **outbound-only**: the system pushes a "your agent replied" message to
a chat channel; users still issue commands and read full replies in the web UI.
It is not a return to the v2 model where chat platforms were the interface.

## Decision Drivers

- **Stay aligned with ADR-0006.** No inbound chat command handling. No
  platform-specific message splitting, formatting, or rate-limit gymnastics in
  the core. The notification payload is a fixed-shape "you have a reply" hint
  with a deep link.
- **Reuse existing patterns.** The CD daemon already implements webhook fan-out
  in `src/cd/notify.py` (Slack + Discord, threaded, swallow-and-log on error,
  dry-run aware). The agent-reply path should reuse that shape rather than
  invent a new one.
- **Single-user, self-hosted threat model.** Same posture as ADR-0009/0010 —
  webhook URLs and bot tokens are stored alongside other operator settings.
- **Minimum surface for v1.** Avoid a large new abstraction; ship one channel
  pattern that demonstrably works and extend it when there is concrete demand.
- **Don't block the agent reply path.** Notification delivery must not
  introduce latency or failure modes for the existing MQTT → DB → WebSocket
  fan-out.

## Considered Options

### Channel selection (which platforms ship in v1)

1. **Discord webhook only.** Reuse the exact `_discord_payload` shape from CD.
2. **Discord webhook + Telegram Bot API.** Two channels, both simple HTTPS
   POSTs.
3. **Discord + Telegram + WhatsApp Business API.**
4. **Generic webhook only — let users plug in any service via a single
   user-supplied URL and JSON template.**

### Configuration storage

A. **Global env vars only** (`MASTER_NOTIFY_*`), read once at process start.
B. **Per-workspace rows in the existing `config` table** (ADR-0009/0010), with
   global env vars as fallback.
C. **Both: global env vars are the default; workspace `config` rows override
   per-workspace via the merge already implemented in `runtime_config`.**

### Public-URL discovery

I. **New `MASTER_PUBLIC_URL` env var.** Operator sets the externally reachable
   base URL once; master uses it verbatim to build deep links.
II. **Reuse the existing `MASTER_URL` setting** (currently
    `http://master:8080`, an in-cluster address).
III. **Infer from the inbound HTTP `Host`/`X-Forwarded-*` headers** seen during
     normal API traffic.

### When to notify

P. **On every agent `response` MQTT event** (final reply per topic).
Q. **On every agent event including `status` transitions** (running → idle,
   etc.).
R. **Only on final reply, and only when no UI client is currently watching the
   topic.**

## Decision Outcome

**Chosen:** **2 + C + I + P.**

1. **Channels — Option 2 (Discord + Telegram).**
   - **Discord** ships first; the payload shape matches `src/cd/notify.py`
     (`{"content": "..."}`) so the existing helper can be lifted with minimal
     change. Operators who already set up CD webhooks can reuse the same URL
     shape.
   - **Telegram** ships in the same slice. The Bot API is a single
     `POST https://api.telegram.org/bot<TOKEN>/sendMessage` with JSON body
     `{"chat_id": "...", "text": "..."}`. This is the cheapest second channel
     to add and demonstrates the abstraction works for non-webhook providers.
   - **WhatsApp is out of scope for v1.** The Cloud / Business API requires a
     verified Meta business account, a registered phone number, template
     pre-approval for proactive messages outside a 24-hour user-initiated
     window, and review of message templates. This is materially more work
     than the rest of the feature combined, and the user base for a self-hosted
     dev tool is unlikely to maintain a Business API account. Documented as a
     future option; not built.
   - **Generic webhook is deferred.** It is a tempting catch-all but in
     practice forces every operator to write their own JSON template, which is
     more friction than picking a named provider.

2. **Configuration — Option C (env-default + workspace override).** The
   plumbing for this already exists:
   - Global defaults read from env vars by `load_master_settings()` (e.g.
     `MASTER_NOTIFY_DISCORD_WEBHOOK_URL`, `MASTER_NOTIFY_TELEGRAM_BOT_TOKEN`,
     `MASTER_NOTIFY_TELEGRAM_CHAT_ID`).
   - Per-workspace overrides via the existing `config` table (ADR-0009 §4) and
     workspace UI surface (ADR-0010). The same keys can be set per-workspace
     and they override the global default at notification time.
   - At each agent reply, master resolves the effective config for that
     workspace by reading `config` rows + env defaults via the merge already
     implemented for `load_agent_env`. Workspace UI heuristic masking from
     ADR-0010 already covers `*TOKEN*`, `*WEBHOOK*` will be added to the
     substring list.
   - Empty/None at every scope means "do not notify on this channel".

3. **Public URL — Option I (`MASTER_PUBLIC_URL`).** The existing
   `master_url` setting (`http://master:8080`) is the in-cluster address used
   by agent containers to reach the API; it is not safe to use in a chat
   message. A separate `MASTER_PUBLIC_URL` env var (no default; if unset,
   notifications are sent without a clickable link, only the topic identifier
   in plain text) keeps the two concerns separate. Header inference is
   rejected: it is non-deterministic, depends on every request setting the
   right headers, and the notification path runs from an MQTT callback that
   has no inbound request context.

4. **When to notify — Option P (every final reply).** The agent publishes
   exactly one `response` per dispatch (see `src/agent/mqtt_loop.py:231` —
   single `client.publish` to the response topic per turn). Status events
   are not user-meaningful for notification purposes. "Only when no client is
   watching" (Option R) is rejected for v1: it requires presence tracking on
   the WebSocket hub and creates a confusing "sometimes silent" UX. Users who
   do not want notifications can leave the channel unconfigured.

### Alignment with ADR-0006

ADR-0006 dropped Slack/Discord as **interfaces**. It explicitly leaves room
for chat integration as "an external adapter (a separate service that bridges
Slack/Discord events to `POST /api/workspaces/{id}/topics/{tid}/messages`)".

This feature is **not** that adapter and does not contradict ADR-0006:

- It is unidirectional (master → chat). No chat events come back into master.
- There is no slash-command parsing, no thread tracking, no platform-specific
  formatting beyond a one-line text template + URL.
- The web UI remains the only way to read full replies and send new messages.
- If a future adapter wants to consume agent replies and post them as proper
  threaded messages, it can do so via the existing WebSocket / HTTP API; the
  notification webhook is independent.

The notification is best understood as a **pager-style heads-up**: "your agent
replied — open the topic". It is intentionally minimal so it does not become a
de facto chat UI.

### Consequences

- **Good**
  - Users get out-of-band notifications without keeping a tab open.
  - Reuses the existing `cd/notify.py` pattern and the ADR-0009/0010 config
    machinery — minimal new code, no new tables, no new endpoints.
  - Per-workspace override means a user can route different projects to
    different channels (e.g. `#work` vs `#personal`).
  - Notification failures cannot break the agent reply path (delivery is
    fire-and-forget on a daemon thread with bounded timeout, errors logged
    and swallowed — same shape as `cd/notify.py`).
- **Bad / accepted tradeoffs**
  - WhatsApp users are not served in v1.
  - `MASTER_PUBLIC_URL` is a new operator-facing knob. If unset, the
    notification still goes out but contains only the topic id — operators
    who deploy behind a reverse proxy must set it explicitly.
  - Notifications fire even when the user is actively reading the topic in
    the browser. We accept the duplicate-buzz cost in v1 to avoid presence
    tracking; can be revisited.
  - Webhook URLs and bot tokens stored in plaintext (per ADR-0009 §E).

### Confirmation

- Unit tests in `tests/master/` for the channel adapters (Discord, Telegram)
  and the config-resolution logic (env default → workspace override → none).
- UAT case: configure a Discord webhook globally, send a message in any
  workspace, observe a Discord message arriving with the topic deep link.
  Marked `needs-human` since it requires a real Discord channel.
- UAT case: configure a Telegram bot token + chat id at workspace scope, send
  a message, observe Telegram delivery. `needs-human`.
- UAT case: leave both channels unset, verify no notifications are sent and
  no errors logged.
- UAT case: set `MASTER_PUBLIC_URL` to an obviously-wrong URL, verify the
  notification still arrives with that URL embedded (master does not validate
  the URL, only uses it).

## Pros and Cons of the Options

### Channels

| Option | Pro | Con |
|---|---|---|
| 1 — Discord only | Cheapest; one payload shape | Single-platform; misses Telegram users with no path forward |
| 2 — Discord + Telegram (chosen) | Two-channel design forces a small, real abstraction; covers both webhook and bot-API styles | Slightly more code than Option 1 |
| 3 — Discord + Telegram + WhatsApp | Maximum coverage | WhatsApp Business API onboarding dwarfs the rest of the feature |
| 4 — Generic webhook | Maximum flexibility | Pushes JSON-template authoring onto every operator; poor first-time UX |

### Configuration storage

| Option | Pro | Con |
|---|---|---|
| A — Env only | Simplest; matches `cd/notify.py` exactly | No per-workspace routing; one channel for the whole instance |
| B — Workspace `config` only | Consistent with ADR-0009 hierarchy | Forces every operator to set values per workspace even when one global set would do |
| C — Env default + workspace override (chosen) | Reuses ADR-0009/0010 merge; sensible default with per-project escape hatch | Two places to look when debugging; mitigated by the merged-view UI ADR-0010 already provides |

### Public-URL discovery

| Option | Pro | Con |
|---|---|---|
| I — `MASTER_PUBLIC_URL` (chosen) | Explicit; works from non-HTTP contexts (MQTT callback) | One more env var to set behind a proxy |
| II — Reuse `MASTER_URL` | No new config | Current value is in-cluster; would emit unreachable URLs |
| III — Infer from headers | Zero config | Non-deterministic; unavailable from MQTT callback path |

### When to notify

| Option | Pro | Con |
|---|---|---|
| P — Every final reply (chosen) | Simple; matches user mental model ("agent finished, ping me") | Buzzes even when the user is watching |
| Q — All status events | More info | Notification spam; status transitions are not user-meaningful |
| R — Only when nobody is watching | Quietest | Requires WebSocket presence tracking; opaque "sometimes silent" UX |
