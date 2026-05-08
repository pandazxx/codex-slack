# User Manual

This manual covers day-to-day use of the codex-slack v3 web UI.

## Overview

codex-slack v3 is a self-hosted chat interface for LLM coding agents. You interact with agents through a browser — there is no Slack or Discord dependency.

**Core concepts:**

- **Workspace** — maps to one Git repository. One agent container runs per workspace.
- **Topic** — a chat thread within a workspace. Each topic gets its own git worktree and its own LLM session per agent. Topics are independent of each other.
- **Agent** — a named LLM configuration (e.g. `claude`, `codex`, `engineer`). Each workspace has at least two default agents.

## Navigation

| Route | View |
|-------|------|
| `/` | Workspace list — create and browse active workspaces |
| `/workspaces/:id` | Workspace detail — topic list, agent configuration |
| `/workspaces/:wsId/topics/:topicId` | Topic chat — message thread, real-time agent output |
| `/workspaces/:wsId/topics/:topicId/settings` | Topic Settings — event actions for the topic |
| `/archived` | Archived workspaces (read-only) |
| `/workspaces/:id/archived-topics` | Archived topics for a workspace (read-only) |

## Core Workflows

### Create a workspace

1. Click **New Workspace** on the home page.
2. Enter a display name and the repository URL (HTTPS or SSH).
3. Submit. Master clones the repo and starts an agent container for the workspace.

Two default agents are registered automatically:
- `claude` — uses the Claude Code adapter
- `codex` — uses the Codex adapter

### Create a topic

1. Open a workspace.
2. Click **New Topic** and enter a subject line.
3. Optionally provide a branch name; otherwise one is auto-generated from the subject.

The topic gets its own git worktree at `/workspace/worktrees/<topic-id>` inside the agent container.

### Send a message

1. Open a topic.
2. Type your message in the input box and submit.
3. To address a specific agent, prefix your message with `@agent-name`, e.g. `@claude fix the login bug`. If no `@mention` is used, the message is routed to the default agent (set by the `agent_name` field, defaulting to `claude`).
4. A thinking spinner appears while the agent is processing.
5. The agent's response appears in the thread when complete.

### Attach images by pasting from the clipboard

While the message input box is focused, press Ctrl+V (or Cmd+V on Mac) to paste an image directly from your clipboard. The image appears as an attachment chip alongside the text input, identical to a file selected via the file picker. You can paste multiple images in a single paste action; each becomes a separate chip. Plain-text clipboard content is unaffected and pastes into the text field as normal.

Pasted images are uploaded with your message when you submit. Filenames are generated automatically in the form `pasted-image-{timestamp}.{ext}`.

### Session persistence

Each agent maintains a separate LLM session per topic. The first message in a topic starts a new session. Subsequent messages in the same topic resume the session automatically. If a session expires on the server side, the agent retries the prompt with a fresh session transparently.

### Archive a workspace or topic

Use the **Archive** button in the workspace or topic view. Archiving:
- Sets an `archived_at` timestamp — data is never deleted.
- Makes the item read-only in the UI.
- For workspaces: also archives all active topics and stops the agent container.

Archived items are viewable at `/archived` and `/workspaces/:id/archived-topics`.

### Manage agents

In the workspace detail view, the **Agents** section shows active agent configurations. You can:
- Add a new agent (name, adapter, optional subagent flag)
- Remove an agent (soft-delete — historical sessions are preserved)

### Configure event actions

Event actions automatically invoke a staff when something happens in a topic — a user message arrives, an agent replies, a cron schedule fires, or the topic is archived. Use them for recurring tasks like daily summaries, automatic translation of replies, or archival wrap-ups.

1. Open a topic and click the **gear icon** in the topic header (or in the topic row on the workspace page) to open **Topic Settings**.
2. Under **Event Actions**, click **+ Add action** and fill in the form:
   - **Event type** — what triggers the action.
   - **Staff** — the name of the staff to invoke (without `@`).
   - **Prompt template** — the text sent to the staff; use `{variable}` placeholders listed in the form hint.
   - For `topic_message_sent`: choose **timing** (`before` or `after` the user message is dispatched).
   - For `topic_scheduler`: enter a **cron expression** (5 fields; interpreted in the configured system timezone shown next to the field).
3. Save. The action appears in the list with a status card showing the last run time and outcome.

Use the checkbox on each action card to enable or disable it without deleting it. Click **Edit** to update the prompt or staff name. Click **✕** to delete permanently.

Each action card shows `last_run_status` — `ok` (green), or an error state (`staff_missing`, `render_error`, `dispatch_error`) in red. Click **details** to expand the full output for diagnosis.

For a full reference including template variables, cron rules, session sharing semantics, and troubleshooting, see [`docs/guides/event-actions.md`](../guides/event-actions.md).

Also navigate to:

| Route | View |
|-------|------|
| `/workspaces/:wsId/topics/:topicId/settings` | Topic Settings — event actions for the topic |

## Real-time Updates

The browser connects to `ws://master-host:8080/ws/events` when you open any topic. You receive:

- **Live trace** — as Claude works, the reply bubble fills with activity rows: tool invocations (e.g. `⚙ Bash: git log …`, `📄 Read: src/…`), subagent progress lines (`↳ …`), and the reply text with a blinking cursor. Subagent tool results appear in a blue block; chain-of-thought thinking appears in an amber block; tool-use rows are foldable to reveal the full input JSON.
- **Thinking indicator** — a coarse status pill shows `thinking` while the agent is running.
- **Final message** — when the agent finishes, the activity rows fold into a `▶ Show trace (N steps)` toggle and the clean reply text remains. Clicking the toggle expands the full trace in place.
- **Refresh recovery** — if you reload the page while the agent is mid-stream, the partial reply is restored automatically within ~1s from the master's persisted chunk store.

If the WebSocket disconnects (e.g. network interruption), reload the page to reconnect.

## Related Docs

- [`docs/manuals/ops-manual.md`](ops-manual.md) — setup and deployment
- [`docs/references/api.md`](../references/api.md) — API reference (for developers and integrators)
- [`docs/guides/runbooks/master-agent.md`](../guides/runbooks/master-agent.md) — operational procedures
