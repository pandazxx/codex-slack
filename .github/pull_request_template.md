## Summary

<!-- Describe what changed and why in 1-3 sentences. -->

## Checklist

<!-- Mark completed items with [x]. -->

- [ ] Tests pass locally (`.sre/test.sh` or `pytest`)
- [ ] No new `latest` tags in Dockerfile or docker-compose files
- [ ] If database schema changed: migrations are present and reversible
- [ ] Commit messages are clear and link issues where applicable
- [ ] No hardcoded secrets or credentials

## Related Issues

<!-- Link any GitHub issues resolved by this PR: Fixes #123 -->

## Notes for Reviewer

<!-- Anything the reviewer should know: design tradeoffs, known limitations, areas of concern. -->

---

For more context on merge rules and SRE concerns, see:
- [Repository Harness](../../docs/guides/repo-harness.md) — branch protection, CI requirements.
- [SRE Workflow](../../docs/guides/sre.md) — container operations, test execution.
