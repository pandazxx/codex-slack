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
7. Report the commit SHA and confirm the push succeeded.

$ARGUMENTS
