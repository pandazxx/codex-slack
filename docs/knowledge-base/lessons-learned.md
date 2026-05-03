# Lessons Learned

Append-only log. Each entry: date, summary, root cause, fix applied, prevention.

---

## 2026-03-24 — docs/knowledge-base directory initialised

*Summary:* The project `CLAUDE.md` references [`docs/knowledge-base/lessons-learned.md`](lessons-learned.md) and [`docs/knowledge-base/faq.md`](faq.md) as required knowledge-persistence targets, but neither file nor the directory existed.

*Root cause:* The document layout was defined in v3.4 as a target structure; no initialisation step created the required stubs.

*Fix applied:* Created `docs/knowledge-base/`, `docs/guides/runbooks/`, `docs/references/`, and `docs/manuals/` with initial stub files during v3.4 doc-writer pass.

*Prevention:* When a new document layout is defined in CLAUDE.md, include a chore commit that scaffolds the required directories and stubs so agents can immediately write to them without a missing-file error.
