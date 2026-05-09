# Test Plan: Vetoable topic_archiving event

**Design doc:** [ADR-0014](../decisions/0014-vetoable-topic-archiving-event.md)
**Feature issue:** #156

---

## Scope

Covers the `topic_archiving` pre-commit interceptor: the synchronous-await dispatch path,
structured verdict protocol, API contract changes (423 / 504), frontend veto dialog, and
operator override. Does not cover the existing `topic_archived` post-commit event (covered
by prior test suite).

---

## Test Cases

### TC-01 — No topic_archiving actions: archive proceeds normally

**Type:** automated (unit)
**Path:** allow

1. Create workspace + topic.
2. No `topic_archiving` event_actions for the topic.
3. `DELETE /api/workspaces/{wid}/topics/{tid}` → **204**.
4. Topic has `archived_at` set in DB.
5. `topic_archived` post-commit event fires (existing behaviour unchanged).

---

### TC-02 — Allow path: veto staff returns allow

**Type:** automated (unit)
**Path:** allow

1. Create workspace + topic.
2. Create a `topic_archiving` event_action on the topic (staff: a mock/stub).
3. Stub `dispatch_to_staff` to succeed and immediately resolve the veto Future with
   `{"verdict": "allow", "reason": ""}`.
4. `DELETE /api/workspaces/{wid}/topics/{tid}` → **204**.
5. `topic_archived` fires; topic `archived_at` is set.
6. `last_run_status = 'ok'` recorded for the event_action.

---

### TC-03 — Deny path: veto staff returns deny

**Type:** automated (unit)
**Path:** deny

1. Create workspace + topic with a `topic_archiving` event_action.
2. Stub veto Future to resolve with `{"verdict": "deny", "reason": "unanswered review comments"}`.
3. `DELETE /api/workspaces/{wid}/topics/{tid}` → **423 Locked**.
4. Response body: `{"detail": {"reason": "unanswered review comments"}}`.
5. Topic `archived_at` is **not** set.
6. `topic_archived` does **not** fire.
7. `last_run_status = 'vetoed'`; `last_run_output` contains the reason.

---

### TC-04 — Timeout path: veto staff does not respond

**Type:** automated (unit — VETO_TIMEOUT_S patched to 0.1 s)
**Path:** timeout

1. Create workspace + topic with a `topic_archiving` event_action.
2. Stub `dispatch_to_staff` to succeed but never resolve the veto Future.
3. `DELETE /api/workspaces/{wid}/topics/{tid}` → **504 Gateway Timeout** (after ≤0.1 s).
4. Response body: `{"detail": {"reason": "veto staff did not respond in time"}}`.
5. Topic `archived_at` is **not** set.
6. `last_run_status = 'veto_timeout'` recorded.

---

### TC-05 — Override path: bypass veto with ?override=true

**Type:** automated (unit)
**Path:** override

1. Create workspace + topic with a `topic_archiving` event_action that would deny.
2. `DELETE /api/workspaces/{wid}/topics/{tid}?override=true` → **204**.
3. `veto_dispatch` is **not called** (no dispatch to staff).
4. Topic `archived_at` is set.
5. `topic_archived` fires normally.

---

### TC-06 — Multiple actions: first-deny-wins

**Type:** automated (unit)
**Path:** deny

1. Create topic with two `topic_archiving` actions.
2. First action resolves `allow`; second resolves `deny`.
3. `DELETE` → **423**.
4. `last_run_status = 'ok'` for the allow action; `'vetoed'` for the deny action.

---

### TC-07 — Multiple actions: all allow → proceeds

**Type:** automated (unit)
**Path:** allow

1. Two `topic_archiving` actions both resolving `allow`.
2. `DELETE` → **204**.
3. Both actions have `last_run_status = 'ok'`.

---

### TC-08 — Staff missing: action skipped, archive proceeds

**Type:** automated (unit)

1. `topic_archiving` action references a deleted/non-existent staff.
2. `DELETE` → **204** (no veto Future registered; `veto_dispatch` returns allow).
3. `last_run_status = 'staff_missing'`.

---

### TC-09 — Disabled action: not dispatched

**Type:** automated (unit)

1. `topic_archiving` action with `enabled=false`.
2. `DELETE` → **204** without dispatching (same as no actions).

---

### TC-10 — topic_archiving CRUD: create/list/get/patch/delete

**Type:** automated (unit)

1. POST `topic_archiving` with valid body (no timing/cron) → 201.
2. GET → action listed.
3. PATCH → update `prompt_template` → 200.
4. POST with `timing='before'` → **422** (timing must be null or 'after').
5. POST with `cron_expr='* * * * *'` → **422** (cron_expr must be null).
6. DELETE → 204; GET → 404.

---

### TC-11 — DB migration: existing databases gain topic_archiving

**Type:** automated (unit)

1. Create a DB with the old schema (no `topic_archiving` in event_type CHECK).
2. Run `init_db`.
3. INSERT `topic_archiving` row → succeeds.
4. INSERT old `topic_archived` row → still succeeds (existing data unaffected).

---

### TC-12 — Agent verdict extraction: inline JSON

**Type:** automated (unit — `_extract_verdict`)

1. Input: `'{"verdict": "deny", "reason": "open PRs exist"}'`
2. Output: `{"verdict": "deny", "reason": "open PRs exist"}`.

---

### TC-13 — Agent verdict extraction: trailing JSON in prose

**Type:** automated (unit — `_extract_verdict`)

1. Input: `'After reviewing the topic, I recommend holding off.\n\n{"verdict": "deny", "reason": "2 unanswered questions"}'`
2. Output: `{"verdict": "deny", "reason": "2 unanswered questions"}`.

---

### TC-14 — Agent verdict extraction: no JSON falls back to allow

**Type:** automated (unit — `_extract_verdict`)

1. Input: `'This topic looks good to archive.'`
2. Output: `{"verdict": "allow", "reason": "(no verdict found in response)"}`.

---

### TC-15 — Agent: response_mode=verdict publishes on /verdict and /response

**Type:** automated (unit — `_process_prompt` patched)

1. Payload includes `response_mode="verdict"`.
2. After LLM call, agent publishes on `/verdict` (QoS 1) with `{reply_to, verdict, reason, agent_name}`.
3. Agent also publishes on `/response` as normal (text message appears in topic chat).

---

### TC-16 — Frontend: veto dialog shown on 423, override proceeds

**Type:** needs-human (visual)

1. Configure a `topic_archiving` action whose template causes the agent to deny.
2. Click Archive on a topic.
3. Observe the loading state ("Archiving…") on the button while the veto runs.
4. Observe the veto dialog: heading "Archive blocked", topic name, reason text, "Override and archive anyway" + "Cancel".
5. Click "Override and archive anyway" → topic disappears from active list.

---

### TC-17 — Frontend: veto dialog shown on 504

**Type:** needs-human (visual)

1. Archive a topic when the agent container is stopped.
2. After 30 s (or a shortened test timeout), the veto dialog appears with heading "Veto staff timed out" and the timeout message.
3. Click "Cancel" → dialog closes; topic remains active.
4. Click Archive again → veto dialog appears again.

---

### TC-18 — Integration: full allow path against dev env

**Type:** automated (stack)

1. Configure a `topic_archiving` action invoking `@reviewer` with a template that asks it to allow.
2. `DELETE /api/…/topics/{tid}` → 204.
3. Agent's reasoning text appears in the topic's message history.
4. Topic appears in archived list.

---

### TC-19 — Integration: full deny path against dev env

**Type:** automated (stack)

1. Configure a `topic_archiving` action with a template that instructs the agent to deny with a specific reason.
2. `DELETE /api/…/topics/{tid}` → 423 with the reason in the body.
3. Topic remains in active list.
4. Agent's text reply (the reasoning) appears in the topic's message history.

---

## Pass / Fail Criteria

- All automated unit tests pass (`tests/master/test_topic_archiving_veto.py`; `tests/agent/test_mqtt_loop.py`).
- TC-16 and TC-17 signed off by a human reviewer in the PR.
- No regression in existing `topic_archived` tests.
- `DELETE /api/workspaces/{wid}/topics/{tid}` without any `topic_archiving` actions: latency unchanged (< 200 ms).
