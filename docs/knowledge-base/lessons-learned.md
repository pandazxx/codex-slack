# Lessons Learned

Append-only log. Each entry: date, summary, root cause, fix applied, prevention.

---

## 2026-03-24 — docs/knowledge-base directory initialised

*Summary:* The project CLAUDE.md references `docs/knowledge-base/lessons-learned.md` and `docs/knowledge-base/faq.md` as required knowledge-persistence targets, but neither file nor the directory existed.

*Root cause:* The document layout was defined in v3.4 as a target structure; no initialisation step created the required stubs.

*Fix applied:* Created `docs/knowledge-base/`, `docs/guides/runbooks/`, `docs/references/`, and `docs/manuals/` with initial stub files during v3.4 doc-writer pass.

*Prevention:* When a new document layout is defined in CLAUDE.md, include a chore commit that scaffolds the required directories and stubs so agents can immediately write to them without a missing-file error.

---

## 2026-03-26 — documentation map was inconsistent with the target layout

*Summary:* The repository had a documented category-based layout, but most files still lived flat under `docs/`, while release notes lived outside `docs/` entirely and root entrypoints duplicated content.

*Root cause:* The project accumulated documentation incrementally without a dedicated cleanup pass to normalize locations and cross-links.

*Fix applied:* Reorganized the docs tree by purpose, added canonical index/manual/reference entrypoints, moved release notes under `docs/releases/`, and converted `BUILD.md` and `USAGE.md` into compatibility pointers.

*Prevention:* When adding new documentation, place it directly in the category that matches its purpose and update `docs/README.md` in the same change.
