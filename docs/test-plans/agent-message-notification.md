# Test Plan: Agent Message Notification

- **Feature design:** [docs/design/agent-message-notification.md](../design/agent-message-notification.md)
- **ADR:** [docs/decisions/0011-agent-message-notification.md](../decisions/0011-agent-message-notification.md)
- **Date:** 2026-05-05
- **Components under test:** `src/master/notify.py`, `src/master/config.py` (new settings fields), `src/master/mqtt_client.py` (call site integration)

---

## 1. Scope

### In scope

- `src/master/notify.py` module in isolation:
  - `build_topic_url` — URL construction from public base and identifiers.
  - `_render_body` — notification text assembly from `NotificationContent`.
  - `_discord_payload` — Discord-specific JSON payload builder.
  - `_telegram_payload` — Telegram-specific JSON payload builder.
  - `post_webhook` — HTTP POST helper with error swallowing and dry-run support.
  - `notify_reply` — main entry point: DB lookup, config merge, channel dispatch.
- Config resolution: global `MasterSettings` fields as default; per-workspace `config` table rows as override (`NOTIFY_*` keys).
- `NOTIFY_DISABLED` per-workspace suppression flag.
- Preview truncation to `NOTIFY_PREVIEW_CHARS` characters with ellipsis.
- Fire-and-forget dispatch on daemon threads (no blocking of the WebSocket broadcast path).
- Failure isolation between channels: a 5xx from one provider must not suppress delivery on the other.
- `dry_run` mode: no HTTP call, log line emitted.
- Live UAT against a real testbed: Discord webhook delivery, Telegram bot delivery, URL deep link correctness.

### Out of scope

- Inbound chat commands (explicitly excluded by ADR-0006 and ADR-0011).
- WhatsApp Business API (deferred in ADR-0011).
- Notification persistence or audit log (rejected alternative C in the design doc).
- Presence-aware suppression ("only notify when no browser tab is watching", deferred).
- Per-user preferences (single-user deployment, deferred).
- Retry on delivery failure (fire-and-forget by design; same posture as `cd/notify.py`).
- Open Graph / markdown rendering in notification bodies.
- Frontend `SECRET_KEY_SUBSTRINGS` extension for `WEBHOOK` (verified separately via component tests).
- `docs/references/config.md` and `.env.example` accuracy (doc-writer scope).

---

## 2. Test Cases

### 2.1 Unit tests (automated)

All unit tests live in `tests/master/test_notify.py`. They run against `src/master/notify.py` with `sqlite3` on a real temporary file-backed database (matching the pattern in `tests/master/test_mqtt_client.py` and `tests/master/test_db.py`). HTTP calls are patched via `unittest.mock.patch` at `src.master.notify.post_webhook` or `src.master.notify.urllib.request.urlopen`.

| # | Test name | What is asserted | Status |
|---|-----------|-----------------|--------|
| U-01 | `test_no_channels_configured_no_post` | When all `notify_*` settings are empty/unset, `notify_reply` spawns no threads and calls `post_webhook` zero times. | automated |
| U-02 | `test_discord_only_one_post_correct_payload` | Discord URL configured; one POST to that URL; payload has `content` key containing workspace name and topic subject. | automated |
| U-03 | `test_telegram_only_one_post_correct_url_and_body` | Telegram token + chat_id configured; one POST to `https://api.telegram.org/bot<TOKEN>/sendMessage`; body has `chat_id` and `text`. | automated |
| U-04 | `test_both_configured_two_posts` | Both Discord and Telegram configured; exactly two POSTs dispatched, one to each provider. | automated |
| U-05 | `test_workspace_discord_webhook_overrides_global` | Workspace `config` row `NOTIFY_DISCORD_WEBHOOK_URL` overrides the global webhook URL from `MasterSettings`. | automated |
| U-06 | `test_notify_disabled_skips_all_channels` | Workspace `config` row `NOTIFY_DISABLED=true`; all configured channels skipped; `post_webhook` never called. | automated |
| U-06b | `test_notify_disabled_uppercase_true_skips` | `NOTIFY_DISABLED=TRUE` (uppercase) is also treated as truthy. | automated |
| U-07 | `test_no_public_url_omits_url_from_body` | `master_public_url=""` (unset); Discord payload `content` field contains no HTTP URL. | automated |
| U-08 | `test_preview_chars_zero_omits_preview` | `notify_preview_chars=0`; agent reply text absent from the notification body. | automated |
| U-09 | `test_long_reply_truncated_with_ellipsis` | Reply longer than `notify_preview_chars`; body contains truncated text with `…` appended; full reply absent. | automated |
| U-09b | `test_reply_at_exact_limit_not_truncated` | Reply length equal to limit; no ellipsis added. | automated |
| U-10 | `test_discord_500_sibling_telegram_still_delivered` | Discord `post_webhook` raises `urllib.error.HTTPError(500)`; Telegram URL is still called; no exception propagates. | automated |
| U-11 | `test_dry_run_no_http_call` | `settings.dry_run=True`; `urllib.request.urlopen` never called; a log record containing `dry_run` is emitted. | automated |
| U-12 | `test_deleted_topic_logs_and_returns` | `topic_id` not present in the database; `post_webhook` never called; no exception raised. | automated |
| U-13 | `test_telegram_token_without_chat_id_skips_telegram` | Bot token set but `chat_id` empty; Telegram skipped; only Discord (if configured) dispatched. | automated |
| U-14 | `test_workspace_preview_chars_override` | Workspace `NOTIFY_PREVIEW_CHARS=5` overrides global `notify_preview_chars=200`; reply truncated at 5 chars. | automated |
| U-15 | `test_workspace_telegram_config_used_when_global_absent` | No global Telegram config; workspace `config` rows provide both token and chat_id; Telegram delivery occurs. | automated |
| U-16 | `test_normal_url` (`build_topic_url`) | `build_topic_url("https://codex.example.com", "ws1", "t1")` → `"https://codex.example.com/workspaces/ws1/topics/t1"`. | automated |
| U-17 | `test_trailing_slash_stripped` (`build_topic_url`) | Trailing slash on `public_url` is stripped before concatenation; result has no double slash. | automated |
| U-18 | `test_none_public_url_returns_none` (`build_topic_url`) | `None` input → `None` return. | automated |
| U-19 | `test_empty_string_public_url_returns_none` (`build_topic_url`) | `""` input → `None` return. | automated |
| U-20 | `test_http_500_does_not_raise` (`post_webhook`) | `urlopen` raises `HTTPError(500)`; `post_webhook` swallows it without re-raising. | automated |
| U-21 | `test_http_500_is_logged` (`post_webhook`) | `HTTPError(500)` produces a WARNING log record referencing the status code or the word "failed". | automated |
| U-22 | `test_url_error_does_not_raise` (`post_webhook`) | `urlopen` raises `URLError("connection refused")`; no exception propagates. | automated |
| U-23 | `test_dry_run_skips_http_call` (`post_webhook`) | `post_webhook(..., dry_run=True)` does not call `urlopen`. | automated |
| U-24 | `test_dry_run_emits_log_line` (`post_webhook`) | `post_webhook(..., dry_run=True)` emits a log record containing `dry_run`. | automated |
| U-25 | `test_has_content_key` (`_discord_payload`) | Return value has a `"content"` key whose value is a string. | automated |
| U-26 | `test_has_chat_id_and_text` (`_telegram_payload`) | Return value has `chat_id` matching supplied value and a `"text"` key. | automated |
| U-27 | `test_disable_web_page_preview` (`_telegram_payload`) | `disable_web_page_preview` is `True` in the Telegram payload. | automated |

### 2.2 UAT cases

UAT cases are executed against a live testbed deployment. Cases that require real credentials or visual verification are marked `needs-human`. Cases that can be driven entirely by API calls or log inspection are marked `automated`.

| # | Test case | Type | What to verify / how |
|---|-----------|------|----------------------|
| A-01 | Configure `MASTER_NOTIFY_DISCORD_WEBHOOK_URL` globally; trigger an agent reply; verify a Discord message arrives | needs-human | Set the env var in `.env` and redeploy (or use the global config API). Send a message to a topic via the web UI. Check the configured Discord channel for a message containing the workspace name, topic subject, and a clickable `https://…/workspaces/…/topics/…` URL. |
| A-02 | Configure Telegram token + chat id at workspace scope; trigger an agent reply; verify Telegram delivery | needs-human | Using the workspace config API (`PATCH /api/workspaces/{id}/config`) set `NOTIFY_TELEGRAM_BOT_TOKEN` and `NOTIFY_TELEGRAM_CHAT_ID`. Send a message to a topic in that workspace. Check the Telegram chat for a message from the bot. |
| A-03 | Set `NOTIFY_DISABLED=true` on a workspace; trigger an agent reply; confirm no notification and no errors | needs-human | Using `PATCH /api/workspaces/{id}/config` set `NOTIFY_DISABLED=true`. Both Discord (global) and/or Telegram (workspace) must be configured so the suppression is observable. Send a message. Verify: (a) no Discord/Telegram message arrives; (b) master logs contain no `ERROR` lines related to notify. |
| A-04 | Leave all notification config unset; trigger an agent reply; confirm master log has no errors | automated | On a testbed with no `MASTER_NOTIFY_*` env vars set and no workspace `NOTIFY_*` rows, send a topic message. Inspect master container logs (`docker logs master`) for any `ERROR` or `EXCEPTION` lines referencing `notify`. Pass criterion: zero such lines. |
| A-05 | Click the URL in a received notification; confirm it opens the correct topic in the web UI | needs-human | After A-01 or A-02 produces a notification containing a deep link, click that link in the Discord or Telegram client. Verify the browser opens the correct workspace and topic in the web UI (URL path matches `/workspaces/{wsId}/topics/{topicId}` and the topic subject is displayed). |

---

## 3. Pass / Fail Criteria

### Unit tests

All unit tests in `tests/master/test_notify.py` must pass (`pytest` exit code 0) before the feature branch is considered merge-ready. A single failure is a blocking defect.

### UAT

| Result | Meaning |
|--------|---------|
| `pass` | Executed programmatically or by human; outcome matches the expected result stated in the test case. |
| `fail` | Executed; outcome did not match. A failing UAT case blocks merge and is handed off to `engineer` with the full error detail. |
| `needs-human` | Cannot be verified without real credentials, a real chat account, or visual browser interaction. The human reviewer must reply on the PR with a per-row `pass` or `fail` before the PR is considered signed off. |

The PR is ready to merge only when:
1. All unit tests are green.
2. All `automated` UAT cases pass.
3. All `needs-human` UAT cases have been signed off (marked `pass`) by the user as a PR comment.
4. No `fail` UAT case is unresolved.

---

## 4. Edge Cases and Failure Modes

These edge cases are covered by the unit tests above and documented here for reference.

| Scenario | Expected behaviour | Covered by |
|----------|--------------------|------------|
| `MASTER_PUBLIC_URL` unset | `build_topic_url` returns `None`; notification body omits the URL line; notification is still sent (if channel is configured). | U-07, U-18, U-19 |
| `NOTIFY_PREVIEW_CHARS=0` | Preview omitted from body entirely. Workspace name and subject still present. | U-08 |
| Reply exactly at the preview character limit | No truncation, no ellipsis appended. | U-09b |
| Provider returns HTTP 5xx | `post_webhook` logs at WARNING level, does not re-raise. The sibling channel's delivery is not affected. | U-10, U-20, U-21 |
| Provider URL unreachable / `URLError` | Same as 5xx: logged, swallowed, sibling channel unaffected. | U-22 |
| Both channels misconfigured (e.g. Discord URL is empty, Telegram has token but no chat_id) | Both channels are individually skipped; `notify_reply` returns without error; one INFO log per skipped channel. | U-01, U-13 |
| Topic deleted between agent dispatch and reply arriving | DB join returns no row; `notify_reply` logs and returns without sending any notification. | U-12 |
| `dry_run=True` at the settings level | `post_webhook` is not called; all channel paths emit a `dry_run` log line instead. | U-11, U-23, U-24 |
| `NOTIFY_DISABLED=true` with mixed case | Truthy string check is case-insensitive (`"TRUE"`, `"True"`, `"1"`, `"yes"` all disable). | U-06, U-06b |
| Master process shuts down with notifications in flight | Background threads are daemons; they are abandoned on shutdown. Acceptable per fire-and-forget contract. | Design-only; not unit-testable without process lifecycle harness. |

---

## 5. Non-functional Requirements

| Requirement | Verification method |
|-------------|---------------------|
| `notify_reply` must not block the WebSocket broadcast | Confirmed by architecture: dispatch uses `daemon=True` threads; `notify_reply` returns without joining. Verified structurally in code review; not load-tested in v1. |
| Each `post_webhook` call has a 10-second timeout | Verified in implementation review (matches `cd/notify.py` posture). Not covered by automated tests (would require a slow HTTP server fixture). |
| Webhook URLs and bot tokens are treated as secrets | `NOTIFY_DISCORD_WEBHOOK_URL` and `NOTIFY_TELEGRAM_BOT_TOKEN` are masked in the workspace config UI via the `SECRET_KEY_SUBSTRINGS` extension (`WEBHOOK` substring added). Verified manually in the workspace UI during UAT. |
| No new DB tables, no new HTTP endpoints, no new MQTT topics | Confirmed by the implementation plan in the design doc (§ Implementation Plan last paragraph). Verified in code review. |
