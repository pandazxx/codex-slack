# Operations Manual

This manual is the primary entry point for setting up, deploying, and operating the repository.

## Choose an Operating Mode

- Single bot mode: attach one Slack bot to one local Codex session.
- Master mode: run the orchestration control plane and route prompts to multiple agents.
- CD daemon mode: poll for new images and redeploy the master runtime with rollback support.

## Start Here

1. Read `README.md` for the project overview and deployment options.
2. Follow `docs/guides/slack-setup.md` and `docs/guides/discord-setup.md` for chat frontend setup.
3. Use `docs/guides/container-runtime.md` for container runtime details, mounts, and auth handling.
4. Use `docs/guides/runbooks/master-agent.md` for production runtime operation.
5. Use `docs/guides/runbooks/cd-daemon.md` when enabling automated deployments.

## Related References

- `docs/references/config.md` for configuration keys
- `docs/references/logging.md` for log destinations and verbosity
- `docs/test-plans/master-agent-uat.md` for acceptance verification
- `docs/releases/` for release-specific changes and migration notes
