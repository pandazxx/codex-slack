---
description: Stage and commit all changes with a conventional commit message, then push
argumentHint: "[scope or hint for the commit message]"
model: claude-haiku-4-5-20251001
---

Stage and commit all current changes, then push to the remote branch.

1. Run `git status` to see what changed.
2. Run `git diff` and `git diff --staged` to understand the changes.
3. Stage relevant files. Prefer specific file paths over `git add -A` to avoid accidentally including secrets or build artefacts. If the working tree is entirely intentional, `git add -A` is fine.
4. Write a commit message following Conventional Commits:
   - Format: `<type>(<optional scope>): <short description>`
   - Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
   - Subject line ≤ 72 characters, imperative mood ("add X", not "added X")
   - Add a body paragraph if the *why* is not obvious from the subject
5. Commit: `git commit -m "..."`
6. Push: `git push`
7. Keep the workspace clean after committing:
   - Run `git status` again — there must be no uncommitted changes, untracked files, or modified files remaining.
   - If any files are left untracked and are not needed (build artefacts, temp files, editor files, etc.), add them to `.gitignore` and commit that change too.
   - If untracked files are needed but should not be committed yet, flag them to the user explicitly.
8. Report:
   - Commit SHA (full 40-char hash from `git rev-parse HEAD`)
   - Remote branch name
   - If the remote is GitHub, the direct URL to the commit: `https://github.com/<owner>/<repo>/commit/<sha>` (derive `<owner>/<repo>` from `git remote get-url origin`)

If the user provided a scope, hint, or specific files in the arguments, use that context in steps 3–4.
