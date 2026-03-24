# Lessons Learned

Append-only log. Each entry: date, summary, root cause, fix applied, prevention.

---

## 2026-03-24 — docs/knowledge-base directory initialised

*Summary:* The project CLAUDE.md references `docs/knowledge-base/lessons-learned.md` and `docs/knowledge-base/faq.md` as required knowledge-persistence targets, but neither file nor the directory existed.

*Root cause:* The document layout was defined in v3.4 as a target structure; no initialisation step created the required stubs.

*Fix applied:* Created `docs/knowledge-base/`, `docs/guides/runbooks/`, `docs/references/`, and `docs/manuals/` with initial stub files during v3.4 doc-writer pass.

*Prevention:* When a new document layout is defined in CLAUDE.md, include a chore commit that scaffolds the required directories and stubs so agents can immediately write to them without a missing-file error.

---

## 2026-03-24 — Discord inbound: container_name unavailable at attachment read time

*Summary:* The `_read_all_attachments` call in `discord_app.py` happens before `channel_id` is resolved, so `container_name` cannot be passed to `attachment_to_prompt_fragment`. This means the staged-pointer path (for files exceeding `ATTACHMENT_INLINE_TOKEN_BUDGET`) never activates on Discord — all files are injected inline regardless of size.

*Root cause:* `channel_id` (and therefore the agent container lookup) is determined after the attachment read step in `on_message`. The two-tier delivery design assumed `container_name` would be available at that point.

*Impact:* Small docx/xlsx/pdf files work correctly (inline injection). Large files that should be staged to `/tmp/uploads/` and read via the agent's Read tool are instead injected inline, risking context window pressure.

*Fix applied:* None in v3.5 — small files are unaffected and this is a non-critical degradation. Deferred to a future refactor that resolves agent container name before reading attachments.

*Prevention:* When adding cross-cutting concerns (file staging) to an existing event handler, verify that all required context (channel_id, container_name) is available at the point of use before the feature is committed.
