# Test Plan: Topic Transcript Export

**Feature design:** [docs/design/topic-transcript-export.md](../design/topic-transcript-export.md)
**Related ADR:** [ADR-0014](../decisions/0014-topic-transcript-export.md)
**Date:** 2026-05-09
**Components under test:**
- `src/master/topic_export.py` — `render_markdown`, `_slug_filename`, and the FastAPI route
- `frontend/src/views/TopicChat.vue` — download button in the topic toolbar
**Test file:** `tests/test_topic_export.py`

---

## 1. Scope

This plan covers the transcript export feature described in
[docs/design/topic-transcript-export.md](../design/topic-transcript-export.md).

### In scope

- The `render_markdown(ws, topic, msgs)` pure function: all transcript shapes
  (empty topic, user-only, agent with no transcript, thinking blocks, tool
  blocks, missing tool result, multi-tool, attachments).
- The `_slug_filename(ws_name, topic_subject)` helper: slug formation, truncation,
  fallback to UUID.
- The FastAPI route `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export`:
  response headers (`Content-Type`, `Content-Disposition`), status codes (200, 404,
  413, 422).
- Archived topics: the export endpoint must not filter on `archived_at IS NULL`.
- The download button (`⬇`) in `TopicChat.vue`: presence in the toolbar for active
  and archived topics, and the browser download it triggers.

### Out of scope

- PDF, HTML, JSON, and DOCX export formats.
- Range or partial exports.
- Attachment content bundling (attachments are referenced as links only).
- Bulk export across topics or workspaces.
- The size-cap (413) path beyond confirming the HTTP response — profiling the
  render for large topics is a separate concern.
- Cross-browser clipboard and download API compatibility beyond Chrome.

---

## 2. Test Environment Prerequisites

### Automated tests

- Python >= 3.11, project dependencies installed.
- SQLite in-memory fixture used by `TestClient` in `tests/test_topic_export.py`.
- Run with: `.sre/test.sh tests/test_topic_export.py`

### UAT (needs-human)

- A running deployment reachable at the UI URL (local `docker compose up` or
  the dev environment spun up via SRE).
- A topic with at least one agent message that includes tool calls.
- A browser (Chrome) for inspecting the download dialog and the resulting file.

---

## 3. Test Cases

### TE-01: Empty topic exports successfully — automated

**Description:** A topic with no messages (or whose message list is empty after
filtering) returns HTTP 200 with the correct content-type and a non-empty body
containing at least the document header.

**Precondition:** A workspace and topic exist in the DB; no messages are
associated with the topic.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Status 200.
- `Content-Type: text/markdown; charset=utf-8`.
- Body contains `# Topic:` header line and `Workspace:` line.
- Body does not raise or produce an empty string.

---

### TE-02: Topic with user messages only — automated

**Description:** A topic containing only user messages exports with `## User —`
sections and no agent sections.

**Precondition:** A topic has two user messages with known body text; no agent
messages.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains two `## User —` sections.
- Each section contains the corresponding message body verbatim.
- No `## Agent` section is present.
- No `<details>` blocks are present.

---

### TE-03: Agent message with no transcript — automated

**Description:** An agent message whose `transcript` is null or an empty list
renders a `## Agent (...)` heading followed by `message.text`, with no
`<details>` blocks.

**Precondition:** A topic has one agent message; `transcript` is null;
`message.text` is a known string.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains `## Agent (` heading.
- The known `message.text` appears immediately after the heading.
- No `<details>` block of any kind is present in the agent section.

---

### TE-04: Agent message with thinking block — automated

**Description:** An agent message whose transcript contains an `assistant` event
with a `thinking` content block renders a collapsible `<details>` block with
`<summary>Thinking</summary>`.

**Precondition:** A topic has one agent message; transcript contains one
`assistant` event with a `thinking` block holding known text.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains `<details>` with `<summary>Thinking</summary>`.
- The known thinking text appears inside the `<details>` block.
- The block appears after the final response text in the agent section.

---

### TE-05: Agent message with tool_use and matching tool_result — automated

**Description:** An agent message whose transcript contains a `tool_use` event
and a matching `tool_result` event renders a `<details>` block with
`<summary>Tool: bash</summary>`, a fenced JSON input block, and a fenced plain-text
output block.

**Precondition:** Transcript contains one `assistant/tool_use` event (name `bash`,
known input dict) and one `user/tool_result` event with matching `tool_use_id`
and known result text.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains `<details>` with `<summary>Tool: bash</summary>`.
- Body contains a ` ```json` fenced block with the pretty-printed tool input.
- Body contains a plain fenced block (no language tag) with the result text.
- `_(no result captured)_` is not present.

---

### TE-06: Agent message with tool_use but no tool_result — automated

**Description:** When a `tool_use` event has no matching `tool_result` in the
transcript, the output section reads `_(no result captured)_`.

**Precondition:** Transcript contains one `assistant/tool_use` event; no
`user/tool_result` event with the corresponding `tool_use_id`.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains `<details>` with `<summary>Tool: ` for the tool.
- The output section contains `_(no result captured)_`.
- No exception is raised.

---

### TE-07: Agent message with multiple tool_use events — automated

**Description:** When the transcript contains multiple `tool_use` events, one
`<details>` block per tool is rendered, in the order the events appear in the
transcript.

**Precondition:** Transcript contains two `assistant/tool_use` events — first
`bash` then `read` — each with a matching `tool_result`.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains exactly two `<details>` blocks.
- `<summary>Tool: bash</summary>` appears before `<summary>Tool: read</summary>`.
- Each block contains its respective input and output.

---

### TE-08: Message with attachment — automated

**Description:** A message that has one or more attachments renders a Markdown
bullet list of links beneath the message body.

**Precondition:** A user message has one attachment with a known filename and ID.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Body contains a bullet line matching
  `- [<filename>](/attachments/<id>/download)` under the user message section.

---

### TE-09: Archived topic returns 200 — automated

**Description:** The export endpoint does not filter on `archived_at IS NULL`.
An archived topic is still exportable.

**Precondition:** A topic exists with `archived_at` set to a past timestamp; it
has at least one message.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=md`

**Expected:**
- Status 200.
- Body contains the topic content.

---

### TE-10: Filename slug reflects workspace name and topic subject — automated

**Description:** The `Content-Disposition` filename is derived by lowercasing and
slugging both the workspace name and topic subject, separated by a hyphen, with a
date suffix.

**Precondition:** Workspace name is `"My Repo"`, topic subject is `"Fix the bug"`.

**Steps:**
1. Call `_slug_filename("My Repo", "Fix the bug")` directly, or inspect the
   `Content-Disposition` header from a live request.

**Expected:**
- The filename contains the substring `my-repo-fix-the-bug`.
- The filename ends with a `YYYYMMDD.md` suffix.
- No spaces or special characters other than `-` and `.` are present.

---

### TE-11: Unknown workspace_id returns 404 — automated

**Description:** Requesting export for a workspace that does not exist returns 404.

**Precondition:** No workspace with the given ID exists in the DB.

**Steps:**
1. `GET /api/workspaces/nonexistent-ws/topics/any-topic/export?format=md`

**Expected:**
- Status 404.

---

### TE-12: Unknown topic_id returns 404 — automated

**Description:** Requesting export for a topic that does not exist in a known
workspace returns 404.

**Precondition:** The workspace exists; no topic with the given ID exists under
that workspace.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/nonexistent-topic/export?format=md`

**Expected:**
- Status 404.

---

### TE-13: Unsupported format returns 422 — automated

**Description:** Passing `?format=pdf` (or any value other than `md`) returns 422.

**Precondition:** A valid workspace and topic exist.

**Steps:**
1. `GET /api/workspaces/{workspace_id}/topics/{topic_id}/export?format=pdf`

**Expected:**
- Status 422.
- The `md` format is not affected by this test (no side effects).

---

### TE-14: Download button visible in toolbar for active topic — needs-human

**Description:** The `⬇` button appears in the topic toolbar next to the settings
cog when viewing an active (non-archived) topic.

**Precondition:** A dev or staging environment is running. Navigate to a topic
that is not archived.

**Steps:**
1. Open the topic page in a browser.
2. Inspect the toolbar row at the top of the topic view.

**Expected:**
- A `⬇` icon button is visible next to the settings cog (`⚙`).
- Hovering over the button shows the tooltip `"Export transcript as Markdown"`.

---

### TE-15: Download button visible for archived topic — needs-human

**Description:** The `⬇` button is also present when viewing an archived topic.
Export is read-only and does not require the topic to be active.

**Precondition:** An archived topic exists and is accessible via its URL.

**Steps:**
1. Navigate to an archived topic's URL directly.
2. Inspect the toolbar row.

**Expected:**
- The `⬇` button is visible in the toolbar, consistent with the active-topic view.

---

### TE-16: Clicking the button triggers a browser file download — needs-human

**Description:** Clicking the `⬇` button causes the browser to initiate a file
download dialog (or auto-download, depending on browser settings). The downloaded
file has a `.md` extension.

**Precondition:** A dev or staging environment is running. The topic has at least
one message.

**Steps:**
1. Navigate to any topic page.
2. Click the `⬇` button in the toolbar.
3. Observe the browser download bar or download dialog.
4. Note the suggested filename.

**Expected:**
- The browser initiates a download without navigating away from the page.
- The suggested filename ends with `.md`.
- The filename contains slugged forms of the workspace name and topic subject.
- Opening the downloaded file in a text editor or GitHub Markdown preview renders
  the conversation content with headings per message and collapsible `<details>`
  sections for any thinking or tool-call blocks.

---

## 4. Pass / Fail Criteria

| Class | Requirement |
|---|---|
| Automated (TE-01 through TE-13) | All 13 cases must pass (`pytest` exits 0) before the PR is merged. |
| needs-human (TE-14 through TE-16) | All 3 cases must be verified by a human reviewer and signed off in the PR before merge. TE-16 is blocking UAT sign-off. |
| Regression | No regressions in pre-existing suites: `tests/master/test_messages.py`, `tests/master/test_attachments.py`. |

---

## 5. Out of Scope

- PDF, HTML, and DOCX export formats — not implemented in v1.
- Range or partial exports — whole topic only in v1.
- Attachment bundling — attachments are linked, not embedded.
- Bulk export across topics or workspaces.
- Streaming response or background-job delivery.
- Redaction or PII scrubbing.
