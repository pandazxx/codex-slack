# Event Actions Guide

Event actions let you bind in-system events — a user message arriving, an agent reply landing, a topic being archived, or a cron schedule firing — to an automatic staff invocation. When the event fires, the system renders your prompt template with event-specific variables and dispatches it to the staff you chose, exactly as if you had typed that prompt yourself. The staff's reply lands in the same topic chat.

Event actions are configured per topic. One topic can have any number of event actions across any mix of event types. Each action fires independently: if one fails, the rest are unaffected.

## Event types

| Event type | When it fires | Variables available |
|---|---|---|
| `topic_message_sent` | A user sends a message in the topic | `{msgbody}`, `{topic_name}` |
| `topic_message_received` | An agent reply lands in the topic | `{msgbody}`, `{topic_name}` |
| `topic_scheduler` | A cron schedule matches (minute-level resolution) | `{topic_name}`, `{workspace_name}` |
| `topic_archived` | The topic is archived | `{topic_name}` |

`msgbody` is the raw text of the triggering message: the user's input for `topic_message_sent`, the agent's reply text for `topic_message_received`.

For `topic_message_sent` you must also choose a **timing**:

- `before` — fires before the user's message reaches the agent.
- `after` — fires after the user's message has been dispatched.

Both are observe-only. Neither can modify or veto the original message.

For `topic_scheduler` you must supply a **cron expression** (5 fields: minute hour day month weekday). The expression is interpreted in the configured system timezone, not UTC. The UI shows the timezone next to every cron input. Minimum resolution is 1 minute.

## How to create an event action

1. Open the topic you want to configure.
2. Click the gear icon in the topic chat header (or in the topic row on the workspace page) to open **Topic Settings**.
3. Under **Event Actions**, click **+ Add action**.
4. Choose the event type from the dropdown. The available fields adjust based on your selection.
5. Enter the staff name (without `@`), the prompt template, and any event-type-specific fields (timing or cron expression).
6. Check or uncheck **Enabled** — disabled actions are saved but never fire.
7. Click **Save**.

To edit an existing action, click **Edit** on its card. To toggle it on or off without editing, use the checkbox on the left side of the action card. To remove it permanently, click **✕**.

The event type cannot be changed after creation. Delete and recreate if you need a different event type.

## Examples

### Daily summary via `topic_scheduler`

Fire `@summariser` every morning to post a digest of recent activity:

- Event type: `topic_scheduler`
- Staff: `summariser`
- Prompt template: `Summarise the recent activity in {topic_name} and list any open action items.`
- Cron expression: `0 9 * * *`

With `system.timezone` set to `America/New_York`, this fires at 09:00 Eastern every day. The UI displays the configured timezone next to the cron field so you can confirm the local time at a glance.

### Auto-translate replies via `topic_message_received`

Translate every agent reply into French:

- Event type: `topic_message_received`
- Staff: `translator`
- Prompt template: `Translate the following to French:\n\n{msgbody}`
- Timing: `after` (the only valid value; set automatically)

When the agent posts a reply, `@translator` receives the reply text as `{msgbody}` and posts its own French translation into the same topic.

Note: agent-triggered dispatches use `sender="event"` in the database. The `topic_message_received` hook fires only for `sender="agent"` messages (genuine agent replies), so `@translator`'s reply will itself trigger a `topic_message_received` event only if another action subscribes to it. Each step in a chain requires explicit operator configuration; accidental infinite loops are not possible.

### Closing notes via `topic_archived`

Invoke `@archivist` whenever a topic is archived:

- Event type: `topic_archived`
- Staff: `archivist`
- Prompt template: `{topic_name} has been archived. Write a brief closing summary.`

The action fires once, after the archive is committed. The staff's reply lands in the archived topic view.

## Template variables and escaping

Templates use Python `str.format_map` syntax: `{variable_name}`. The available variables depend on the event type (see the table above).

If a placeholder is not in the variable list for the chosen event type, it is left in the rendered prompt as the literal text `{unknown_variable}` and a warning is logged. This is intentional — a misconfigured template does not crash dispatch.

To include a literal brace character in your prompt, escape it by doubling: `{{` renders as `{`, `}}` renders as `}`.

## Session sharing

Event-triggered dispatches share sessions with user-triggered ones according to the staff's `session_scope` setting:

- `session_scope='topic'` (default): the event-triggered run resumes the same conversation the user has been having with that staff in the topic.
- `session_scope='workspace'`: shared across all topics in the workspace.
- `session_scope='global'`: one shared session across the entire system.

An event-triggered `@reviewer` in a topic picks up the same Claude session as when you type `@reviewer` yourself. The staff has full context from prior turns.

## Loop prevention

Event-triggered messages are written to the database with `sender="event"`. The event hooks gate strictly:

- `topic_message_sent` fires only for `sender="user"` messages.
- `topic_message_received` fires only for `sender="agent"` messages (produced exclusively by the agent's reply path).

An event-triggered dispatch never re-fires `topic_message_sent`. An agent reply produced by an event-triggered run will fire `topic_message_received` exactly like a user-triggered run — this is intentional and allows deliberate chaining (e.g. a scheduler fires `@summariser`, which produces an agent reply, which triggers `@translator`). Chains terminate when there is no further matching action.

## Troubleshooting

Each event action card shows three observability fields:

- **Last run**: relative time since the most recent dispatch attempt (`2 min ago`, `never`).
- **Status badge**: green `ok`, or red `staff_missing` / `render_error` / `dispatch_error`.
- **Details**: click to expand the full `last_run_output` message.

**`staff_missing`** — the staff name could not be resolved at fire time. The staff may have been deleted or renamed. Check the staff name on the action and confirm the staff exists at topic, workspace, or global scope. You can save an action that references a staff that does not yet exist; resolution happens at fire time.

**`render_error`** — the prompt template could not be rendered. Most likely a malformed Python `format_map` expression (e.g. an unclosed `{`). Check the template for syntax errors.

**`dispatch_error`** — the dispatch to MQTT failed, or did not complete within 10 seconds. The 10-second limit covers inserting the message row and publishing to the MQTT broker — it is not the agent's reply latency. Agent replies arrive asynchronously and are visible in the chat. If you see repeated `dispatch_error`, check the master and MQTT broker logs.

**`last_run_at` vs. `last_fired_at`**: `last_run_at` is updated by the event worker after every dispatch attempt and reflects whether the dispatch succeeded. `last_fired_at` is updated by the scheduler tick before dispatch and is used as the cron watermark for next-fire calculation. For non-scheduler event types, `last_fired_at` is always null.

**Disabled actions** never fire. The checkbox in the action card is an instant enable/disable toggle. Disabled actions are retained for audit and can be re-enabled at any time.

**Archived topics**: `topic_scheduler` actions stop firing the moment the topic is archived. The scheduler query filters on `archived_at IS NULL`. `topic_archived` fires once on the transition and then the topic is excluded from future scheduler checks.

## Currently out of scope

The following capabilities are not implemented in v1:

- **Backpressure and rate limiting** for high-frequency event types. Tracked in [issue #154](https://github.com/pandazxx/codex-slack/issues/154).
- **Vetoable archiving** (`topic_archiving` pre-commit interceptor that can block the archive). Tracked in [issue #156](https://github.com/pandazxx/codex-slack/issues/156).
- **Workspace-scope events**. Only topic-scope (`scope_type='topic'`) is implemented. The schema is forward-compatible; a follow-up ADR will define the output channel.
- **TZ-awareness audit** of existing date columns across the codebase. Tracked in [issue #158](https://github.com/pandazxx/codex-slack/issues/158).
- **Sub-minute scheduler precision**. The minimum effective interval is 1 minute.

For the API reference, see [`docs/references/api.md`](../references/api.md#event-actions).
