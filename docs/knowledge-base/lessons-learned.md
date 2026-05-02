# Lessons Learned

Append-only log. Each entry: date, summary, root cause, fix applied, prevention.

---

## 2026-05-02 — v3 bug triage: three bugs fixed

*Summary:* Bug triage of pre-v3 issues against the v3 codebase revealed three actionable bugs: (A) `Dockerfile.agent-minimal` was missing the `/home/appuser/.claude` pre-creation fix applied to `Dockerfile`, causing agent session volumes to be root-owned and unwritable; (B) `_respawn_agents` in `main.py` was respawning archived workspace containers on master restart; (C) `send_message` in `messages.py` accepted messages to archived workspaces/topics whose containers had already been stopped.

*Root cause:* (A) A targeted fix was applied to one Dockerfile but not the other. (B)(C) Archived-at filtering was added for workspace CRUD but not carried through to `_respawn_agents` or `send_message`.

*Fix applied:* Added `mkdir -p /home/appuser/.claude` to `Dockerfile.agent-minimal`; added `AND archived_at IS NULL` guards to `_respawn_agents` SQL and both workspace/topic lookups in `send_message`.

*Prevention:* When a soft-delete pattern (`archived_at`) is added, audit every query that touches the affected tables to ensure the filter is applied everywhere — not just in the CRUD layer.

---

## 2026-03-24 — docs/knowledge-base directory initialised

*Summary:* The project `CLAUDE.md` references [`docs/knowledge-base/lessons-learned.md`](lessons-learned.md) and [`docs/knowledge-base/faq.md`](faq.md) as required knowledge-persistence targets, but neither file nor the directory existed.

*Root cause:* The document layout was defined in v3.4 as a target structure; no initialisation step created the required stubs.

*Fix applied:* Created `docs/knowledge-base/`, `docs/guides/runbooks/`, `docs/references/`, and `docs/manuals/` with initial stub files during v3.4 doc-writer pass.

*Prevention:* When a new document layout is defined in CLAUDE.md, include a chore commit that scaffolds the required directories and stubs so agents can immediately write to them without a missing-file error.
